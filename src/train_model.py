"""
JRA競馬AI - モデル学習 & バックテスト
data/features.parquet を読み込み、
  1) 1着確率モデル (y_win)
  2) 3着内確率モデル (y_top3)
を LightGBM で学習し、時系列split(未来データはvalidに一切使わない)でバックテストする。

重要: odds_win / popularity は特徴量から除外している。
オッズをそのまま特徴量にすると「市場の受け売り」になり、市場に対するエッジを測れなくなるため。
モデルは純粋に「馬の実力」だけから確率を推定し、その後に実際のオッズと比較する。

【2026-10 改訂】
- データの抜け(2022-11〜2026-07)を埋めたので、学習・検証・テスト期間を最新寄りに変更
    train: TRAIN_START 〜 TRAIN_END / valid: 〜 VALID_END / test(バックテスト): VALID_END 〜
  (環境変数で上書き可)
- pace_ratio(そのレース自身のペース = レース後にしか分からない値)を特徴量から削除
- バックテストに「市場(単勝オッズ)との比較」を追加:
    勝ち馬に付けた確率の対数損失、AIと市場を混ぜたときの最適な重み
- テストで性能を確認したあと、全期間で作り直したモデルを本番用として保存(FINAL_REFIT=1)
"""
import json
import os

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

PARQUET = "data/features.parquet"

NUMERIC_FEATURES = [
    "age", "weight_carry", "waku", "umaban", "field_size", "distance",
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

TRAIN_START = os.environ.get("TRAIN_START", "2010-01-01")
TRAIN_END = os.environ.get("TRAIN_END", "2024-01-01")
VALID_END = os.environ.get("VALID_END", "2025-01-01")
FINAL_REFIT = os.environ.get("FINAL_REFIT", "1") == "1"
NUM_THREADS = int(os.environ.get("NUM_THREADS", "0"))  # 0 = 使えるCPUをすべて使う

print("[1] 特徴量読み込み...")
con = duckdb.connect()
cols = ["race_id", "race_date", "horse", "finish", "y_win", "y_top3", "odds_win", "popularity"] + FEATURES
col_sql = ", ".join(f'"{c}"' for c in cols)
df = con.execute(f"SELECT {col_sql} FROM '{PARQUET}' WHERE race_date >= DATE '{TRAIN_START}'").df()
print(f"  -> {len(df):,} 行 ({TRAIN_START}〜)")

for c in CATEGORICAL_FEATURES:
    df[c] = df[c].astype("category")

df["race_date"] = pd.to_datetime(df["race_date"])
train = df[df.race_date < TRAIN_END]
valid = df[(df.race_date >= TRAIN_END) & (df.race_date < VALID_END)]
test = df[df.race_date >= VALID_END].copy()
print(f"  train={len(train):,}  valid={len(valid):,}  test(backtest)={len(test):,}")
if len(valid) == 0:
    raise SystemExit(f"検証期間({TRAIN_END}〜{VALID_END})のデータがありません。バックフィルが終わっているか確認してください。")

PARAMS = dict(
    objective="binary", metric=["auc", "binary_logloss"],
    learning_rate=0.05, num_leaves=63, min_data_in_leaf=200,
    feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
    verbose=-1, num_threads=NUM_THREADS, seed=42,
)


def train_lgb(target):
    dtrain = lgb.Dataset(train[FEATURES], label=train[target], categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False)
    dvalid = lgb.Dataset(valid[FEATURES], label=valid[target], categorical_feature=CATEGORICAL_FEATURES, reference=dtrain, free_raw_data=False)
    return lgb.train(
        PARAMS, dtrain, num_boost_round=3000, valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)],
    )


def refit_all(target, n_rounds):
    """テストで確認し終えたあと、テスト期間まで含めた全データで作り直す(本番用)。
    木の本数は検証で決まった本数を、データが増えた分だけ少し増やして使う。"""
    full = df
    dfull = lgb.Dataset(full[FEATURES], label=full[target], categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False)
    return lgb.train(PARAMS, dfull, num_boost_round=max(50, int(n_rounds * 1.1)))


print("[2] 1着確率モデル学習...")
model_win = train_lgb("y_win")
win_auc = model_win.best_score["valid_0"]["auc"]
print(f"  best_iteration={model_win.best_iteration}  valid AUC={win_auc:.4f}")

print("[3] 3着内確率モデル学習...")
model_top3 = train_lgb("y_top3")
top3_auc = model_top3.best_score["valid_0"]["auc"]
print(f"  best_iteration={model_top3.best_iteration}  valid AUC={top3_auc:.4f}")

print("[4] 特徴量重要度(1着モデル)...")
imp = pd.Series(model_win.feature_importance(importance_type="gain"), index=FEATURES).sort_values(ascending=False)
print(imp.head(15))

summary = dict(
    train_period=f"{TRAIN_START}〜{TRAIN_END}", valid_period=f"{TRAIN_END}〜{VALID_END}",
    test_period=f"{VALID_END}〜", train_rows=len(train), valid_rows=len(valid), test_rows=len(test),
    win_auc=win_auc, top3_auc=top3_auc,
    win_best_iteration=model_win.best_iteration, top3_best_iteration=model_top3.best_iteration,
)
bt = pd.DataFrame()


def fit_blend(d: pd.DataFrame):
    """p ∝ p_ai^a × p_mkt^b の a, b を最尤推定(グリッドサーチ)。a≈0 ならAIは市場に情報を足せていない。"""
    la = np.log(d["p_ai"].clip(1e-4).to_numpy())
    lm = np.log(d["p_mkt"].clip(1e-4).to_numpy())
    rid = pd.factorize(d["race_id"])[0]
    win = d["y_win"].to_numpy() == 1
    n_r = rid.max() + 1
    best = (np.inf, 0.0, 1.0)
    for a in np.arange(0, 2.001, 0.05):
        for b in np.arange(0, 2.001, 0.05):
            s = a * la + b * lm
            mx = np.full(n_r, -np.inf)
            np.maximum.at(mx, rid, s)
            z = np.zeros(n_r)
            np.add.at(z, rid, np.exp(s - mx[rid]))
            ll = -(s[win] - mx[rid[win]] - np.log(z[rid[win]])).mean()
            if ll < best[0]:
                best = (ll, a, b)
    return best


if len(test):
    print("[5] テスト(未来データ)へ予測を付与...")
    test["pred_win"] = model_win.predict(test[FEATURES], num_iteration=model_win.best_iteration)
    test["pred_top3"] = model_top3.predict(test[FEATURES], num_iteration=model_top3.best_iteration)
    test["pred_win_norm"] = test.groupby("race_id")["pred_win"].transform(lambda s: s / s.sum())

    # 市場との比較は、全馬のオッズと勝ち馬がそろっているレースだけで行う
    ok = test.groupby("race_id").apply(
        lambda g: g["odds_win"].notna().all() and (g["y_win"] == 1).sum() == 1, include_groups=False)
    d = test[test["race_id"].isin(ok[ok].index)].copy()
    d["p_ai"] = d["pred_win_norm"]
    inv = 1.0 / d["odds_win"]
    d["p_mkt"] = inv / inv.groupby(d["race_id"]).transform("sum")
    winners = d[d["y_win"] == 1]
    ll_ai = float(-np.log(winners["p_ai"].clip(1e-4)).mean())
    ll_mkt = float(-np.log(winners["p_mkt"].clip(1e-4)).mean())
    ll_unif = float(np.log(d.groupby("race_id").size()).mean())
    ll_blend, a, b = fit_blend(d)
    print("\n[6] AI vs 市場(テスト期間、勝ち馬に付けた確率の対数損失。小さいほど良い)")
    print(f"  レース数={d['race_id'].nunique():,}")
    print(f"  AI={ll_ai:.4f}  市場={ll_mkt:.4f}  全馬同確率={ll_unif:.4f}")
    print(f"  最適ブレンド AI^{a:.2f} × 市場^{b:.2f} -> {ll_blend:.4f}")
    print("  (AIの重みが0に近いほど、AIは市場に上乗せできる情報を持っていない)")

    print("\n[7] 回収率: 全馬単勝・◎単勝・期待値フィルタ")

    def roi(sub):
        sub = sub.dropna(subset=["odds_win"])
        if len(sub) == 0:
            return None
        return dict(n_bets=int(len(sub)), hit_rate=float(sub["y_win"].mean()),
                    roi_pct=float((sub["y_win"] * sub["odds_win"]).sum() / len(sub) * 100),
                    avg_odds=float(sub["odds_win"].mean()))

    base = roi(test)
    honmei = roi(test[test.groupby("race_id")["pred_win"].rank(ascending=False, method="first") == 1])
    fav = roi(test[test["popularity"] == 1])
    print(f"  全馬: {base}")
    print(f"  ◎(AI1位): {honmei}")
    print(f"  1番人気: {fav}")
    rows = []
    t = test.dropna(subset=["odds_win"]).copy()
    t["ev"] = t["pred_win_norm"] * t["odds_win"]
    for th in [1.0, 1.1, 1.2, 1.3, 1.5, 2.0]:
        r = roi(t[t["ev"] >= th])
        if r:
            rows.append(dict(ev_threshold=th, **r))
    bt = pd.DataFrame(rows)
    print(bt.to_string(index=False))

    summary.update(
        test_races_with_odds=int(d["race_id"].nunique()),
        logloss_ai=ll_ai, logloss_market=ll_mkt, logloss_uniform=ll_unif,
        blend_weight_ai=float(a), blend_weight_market=float(b), logloss_blend=float(ll_blend),
        baseline_flat_roi_pct=base["roi_pct"] if base else None,
        honmei_roi=honmei, favorite_roi=fav,
    )
else:
    print("[5] テスト期間のデータが無いのでバックテストは省略")

if FINAL_REFIT:
    print("\n[8] 本番用に全期間で作り直し...")
    model_win = refit_all("y_win", model_win.best_iteration)
    model_top3 = refit_all("y_top3", model_top3.best_iteration)
    summary["production_model"] = f"全期間({TRAIN_START}〜{df.race_date.max():%Y-%m-%d})で再学習"
else:
    summary["production_model"] = "train期間のみで学習したモデル(FINAL_REFIT=0)"

model_win.save_model("data/model_win.txt", num_iteration=None if FINAL_REFIT else model_win.best_iteration)
model_top3.save_model("data/model_top3.txt", num_iteration=None if FINAL_REFIT else model_top3.best_iteration)
summary["data_last_date"] = f"{df.race_date.max():%Y-%m-%d}"

bt.to_json("data/backtest_win.json", orient="records", force_ascii=False, indent=2)
imp.to_json("data/feature_importance.json", force_ascii=False, indent=2)
with open("data/backtest_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=float)

print("\n完了。モデルは data/model_win.txt, data/model_top3.txt に保存。")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=float))
