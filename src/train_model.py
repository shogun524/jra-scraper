"""
JRA競馬AI - モデル学習 & バックテスト
data/features.parquet を読み込み、
  1) 1着確率モデル (y_win)
  2) 3着内確率モデル (y_top3)
を LightGBM で学習し、時系列split(未来データはvalidに一切使わない)でROIをバックテストする。

重要: odds_win / popularity は特徴量から除外している。
オッズをそのまま特徴量にすると「市場の受け売り」になり、市場に対するエッジを測れなくなるため。
モデルは純粋に「馬の実力」だけから確率を推定し、その後に実際のオッズと比較して期待値を計算する。
"""
import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import json

PARQUET = "data/features.parquet"

NUMERIC_FEATURES = [
    "age", "weight_carry", "waku", "umaban", "field_size", "distance", "pace_ratio",
    "recent5_avg_finish", "recent5_winrate", "recent5_top3rate", "recent5_starts",
    "career_winrate", "career_top3rate", "career_starts",
    "dist_aptitude_winrate", "dist_aptitude_top3rate", "dist_aptitude_starts",
    "days_since_last", "recent5_style_ratio",
    "jockey_winrate", "jockey_top3rate", "jockey_rides",
    "trainer_winrate", "trainer_starts",
    "waku_bias_winrate", "waku_bias_n",
    "horse_weight", "weight_diff",
    "race_class", "distance_change_from_last", "weight_carry_change_from_last", "last_race_finish",
    "jockey_trainer_combo_winrate", "jockey_trainer_combo_n",
]
CATEGORICAL_FEATURES = ["surface", "baba", "weather", "sex", "dist_bucket", "track_code", "interval_category"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

TRAIN_END = "2019-01-01"
VALID_END = "2021-08-01"
# test(backtest) = VALID_END 以降、モデルは一切見ていない未来データ(2021-08〜2022年のスクレイピング分を含む)

print("[1] 特徴量読み込み...")
con = duckdb.connect()
cols = ["race_id", "race_date", "horse", "finish", "y_win", "y_top3", "odds_win", "popularity"] + FEATURES
col_sql = ", ".join(f'"{c}"' for c in cols)
df = con.execute(f"SELECT {col_sql} FROM '{PARQUET}'").df()
print(f"  -> {len(df):,} 行")

for c in CATEGORICAL_FEATURES:
    df[c] = df[c].astype("category")

df["race_date"] = pd.to_datetime(df["race_date"])
train = df[df.race_date < TRAIN_END]
valid = df[(df.race_date >= TRAIN_END) & (df.race_date < VALID_END)]
test = df[df.race_date >= VALID_END]
print(f"  train={len(train):,}  valid={len(valid):,}  test(backtest)={len(test):,}")

def train_lgb(target):
    dtrain = lgb.Dataset(train[FEATURES], label=train[target], categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False)
    dvalid = lgb.Dataset(valid[FEATURES], label=valid[target], categorical_feature=CATEGORICAL_FEATURES, reference=dtrain, free_raw_data=False)
    params = dict(
        objective="binary", metric=["auc", "binary_logloss"],
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=200,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        verbose=-1, num_threads=1,
    )
    model = lgb.train(
        params, dtrain, num_boost_round=2000, valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(80, verbose=False), lgb.log_evaluation(0)],
    )
    return model

print("[2] 1着確率モデル学習...")
model_win = train_lgb("y_win")
print(f"  best_iteration={model_win.best_iteration}  valid AUC={model_win.best_score['valid_0']['auc']:.4f}")

print("[3] 3着内確率モデル学習...")
model_top3 = train_lgb("y_top3")
print(f"  best_iteration={model_top3.best_iteration}  valid AUC={model_top3.best_score['valid_0']['auc']:.4f}")

model_win.save_model("data/model_win.txt")
model_top3.save_model("data/model_top3.txt")

print("[4] テスト(未来データ)へ予測を付与...")
test = test.copy()
test["pred_win"] = model_win.predict(test[FEATURES], num_iteration=model_win.best_iteration)
test["pred_top3"] = model_top3.predict(test[FEATURES], num_iteration=model_top3.best_iteration)
# レース内で確率を正規化(1レースの1着確率合計が概ね1になるように)
test["pred_win_norm"] = test.groupby("race_id")["pred_win"].transform(lambda s: s / s.sum())

print("[5] 特徴量重要度(1着モデル)...")
imp = pd.Series(model_win.feature_importance(importance_type="gain"), index=FEATURES).sort_values(ascending=False)
print(imp.head(15))

print("\n[6] バックテスト: モデル予測 vs 実オッズでの期待値(EV)戦略")

def backtest(df_test, prob_col, ev_thresholds):
    results = []
    d = df_test.dropna(subset=["odds_win"]).copy()
    d["ev"] = d[prob_col] * d["odds_win"]
    for th in ev_thresholds:
        bets = d[d["ev"] >= th]
        if len(bets) == 0:
            continue
        stake = len(bets) * 100
        payout = (bets["y_win"] * bets["odds_win"] * 100).sum()
        roi = payout / stake * 100
        results.append(dict(ev_threshold=th, n_bets=len(bets), hit_rate=bets["y_win"].mean(),
                             roi_pct=roi, avg_odds=bets["odds_win"].mean()))
    return pd.DataFrame(results)

print("\n--- 単勝: 全レース フラットベット(ベースライン, EV無視) ---")
d_all = test.dropna(subset=["odds_win"])
base_roi = (d_all["y_win"] * d_all["odds_win"] * 100).sum() / (len(d_all) * 100) * 100
print(f"n_bets={len(d_all):,}  hit_rate={d_all['y_win'].mean():.4f}  ROI={base_roi:.1f}%")

print("\n--- 単勝: 期待値(EV)フィルタ戦略(モデル予測×オッズ >= 閾値のみ購入) ---")
bt = backtest(test, "pred_win_norm", [1.0, 1.1, 1.2, 1.3, 1.5, 2.0])
print(bt.to_string(index=False))

bt.to_json("data/backtest_win.json", orient="records", force_ascii=False, indent=2)
imp.to_json("data/feature_importance.json", force_ascii=False, indent=2)

with open("data/backtest_summary.json", "w") as f:
    json.dump(dict(
        train_rows=len(train), valid_rows=len(valid), test_rows=len(test),
        win_auc=model_win.best_score['valid_0']['auc'],
        top3_auc=model_top3.best_score['valid_0']['auc'],
        baseline_flat_roi_pct=float(base_roi),
    ), f, ensure_ascii=False, indent=2)

print("\n完了。モデルは data/model_win.txt, data/model_top3.txt に保存。")
