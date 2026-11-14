"""
ウォークフォワード予測: 各年Yの予測を「Y年より前だけで学習したモデル」で作る。
買い方の検証(strategy_search.py / ticket_model.py)で、未来を知らない予測を使うため。

使い方:
    python src/walkforward_preds.py data/features.parquet data/wf_preds.parquet --start-year 2019 --end-year 2026
"""
import argparse

import duckdb
import lightgbm as lgb
import pandas as pd

NUM = ["age", "weight_carry", "waku", "umaban", "field_size", "distance", "recent5_avg_finish", "recent5_winrate",
       "recent5_top3rate", "recent5_starts", "career_winrate", "career_top3rate", "career_starts",
       "dist_aptitude_winrate", "dist_aptitude_top3rate", "dist_aptitude_starts", "days_since_last",
       "recent5_style_ratio", "jockey_winrate", "jockey_top3rate", "jockey_rides", "trainer_winrate", "trainer_starts",
       "waku_bias_winrate", "waku_bias_n", "horse_weight", "weight_diff", "race_class", "distance_change_from_last",
       "weight_carry_change_from_last", "last_race_finish", "jockey_trainer_combo_winrate", "jockey_trainer_combo_n"]
CAT = ["surface", "baba", "weather", "sex", "dist_bucket", "track_code", "interval_category"]
F = NUM + CAT
EXTRA = ["race_id", "race_date", "horse", "jockey", "finish", "y_win", "y_top3", "odds_win", "popularity"]
KEEP = ["race_id", "race_date", "umaban", "finish", "odds_win", "popularity", "ai_win", "ai_top3", "ai_rank",
        "ai3_rank", "race_class", "field_size", "surface", "track_code", "distance", "baba", "recent5_style_ratio",
        "career_starts", "age"]
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=42)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("features")
    ap.add_argument("out")
    ap.add_argument("--start-year", type=int, default=2019)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--train-years", type=int, default=12)
    ap.add_argument("--race-result", default="data/race_result_merged.csv")
    a = ap.parse_args()
    first = a.start_year - a.train_years
    cols = ", ".join(f'"{c}"' for c in EXTRA + F)
    df = duckdb.connect().execute(
        f"SELECT {cols} FROM '{a.features}' WHERE race_date >= DATE '{first}-01-01'").df()
    import past_features   # 学習用(train_model.py)と同じ特徴量を足す
    df = past_features.add(df, past_features.run_table(a.race_result))
    FF = F + past_features.FEATURES
    for c in CAT:
        df[c] = df[c].astype("category")
    df["race_date"] = pd.to_datetime(df.race_date)
    out = []
    for Y in range(a.start_year, a.end_year + 1):
        tr = df[(df.race_date >= f"{Y - a.train_years}-01-01") & (df.race_date < f"{Y}-01-01")]
        te = df[(df.race_date >= f"{Y}-01-01") & (df.race_date < f"{Y + 1}-01-01")].copy()
        if te.empty:
            continue
        for tgt, n in [("y_win", 250), ("y_top3", 450)]:
            m = lgb.train(PARAMS, lgb.Dataset(tr[FF], tr[tgt], categorical_feature=CAT), n)
            te["p_" + tgt] = m.predict(te[FF])
        out.append(te)
        print(Y, f"学習{len(tr):,}行", f"予測{te.race_id.nunique():,}レース", flush=True)
    d = pd.concat(out)
    d["ai_win"] = d.p_y_win / d.groupby("race_id").p_y_win.transform("sum")
    d["ai_top3"] = d.p_y_top3 / d.groupby("race_id").p_y_top3.transform("sum") * 3
    d["ai_rank"] = d.groupby("race_id").p_y_win.rank(ascending=False, method="first")
    d["ai3_rank"] = d.groupby("race_id").p_y_top3.rank(ascending=False, method="first")
    d[KEEP].to_parquet(a.out)
    print("完了", len(d), d.race_id.nunique())


if __name__ == "__main__":
    main()
