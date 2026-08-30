"""
JRA競馬AI - 予測(ランキング出力)
新しい出馬表(まだ結果の出ていないレース)を入力し、馬ごとの1着率・3連対率を予測してランキング化する。

【重要な前提】
このスクリプトは data/race_result.csv に入っている過去実績を使って
「その馬・その騎手・その調教師・その枠のこれまでの成績」を計算する。
このCSVは2021-07-31までしか実績が入っていないため、2021-08以降の直近成績は反映されない。
毎週自動で"今の"精度を保つには、直近レース結果を継続的に追加していく仕組み(スクレイパー)が別途必要。
( .github/workflows/weekly_predict.yml の TODO 参照 )

入力CSVの想定カラム(出馬表の時点でわかる情報のみ):
  race_id, race_date, track_code, surface, distance, baba, weather,
  waku, umaban, horse, sex, age, weight_carry, jockey, trainer,
  horse_weight(任意), odds_win(任意, 直前オッズがあれば期待値も計算)
"""
import sys
import duckdb
import lightgbm as lgb
import pandas as pd
import numpy as np

RACE_RESULT = "data/race_result.csv"
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
]
CATEGORICAL_FEATURES = ["surface", "baba", "weather", "sex", "dist_bucket", "track_code"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def build_asof_features(entries: pd.DataFrame) -> pd.DataFrame:
    """entries(出馬表)の各馬について、race_date時点より前の実績のみからasof特徴量を作る"""
    con = duckdb.connect()
    con.execute("PRAGMA threads=1")
    con.register("entries", entries)
    con.execute(f"""
    CREATE TABLE hist AS
    SELECT
        "馬名" AS horse, "騎手" AS jockey, "調教師" AS trainer,
        CAST("レース日付" AS DATE) AS race_date,
        "競馬場コード" AS track_code, "芝・ダート区分" AS surface,
        "距離(m)" AS distance, TRY_CAST("枠番" AS INTEGER) AS waku,
        TRY_CAST("着順" AS INTEGER) AS finish,
        CASE WHEN TRY_CAST("着順" AS INTEGER)=1 THEN 1 ELSE 0 END AS y_win,
        CASE WHEN TRY_CAST("着順" AS INTEGER)<=3 THEN 1 ELSE 0 END AS y_top3,
        TRY_CAST("4コーナー" AS INTEGER) AS corner4,
        count(*) OVER (PARTITION BY "レースID") AS field_size
    FROM read_csv_auto('{RACE_RESULT}', encoding='utf-8')
    WHERE "レース日付" IS NOT NULL AND "馬名" IS NOT NULL
    """)
    con.execute("""
    CREATE TABLE hist2 AS
    SELECT *, CASE
        WHEN distance < 1400 THEN 'sprint' WHEN distance < 1800 THEN 'mile'
        WHEN distance < 2200 THEN 'middle' ELSE 'long' END AS dist_bucket
    FROM hist
    """)

    out_rows = []
    for _, e in entries.iterrows():
        rd = e["race_date"]
        db_ = "sprint" if e["distance"] < 1400 else "mile" if e["distance"] < 1800 else "middle" if e["distance"] < 2200 else "long"

        h = con.execute("SELECT * FROM hist2 WHERE horse=? AND race_date<? ORDER BY race_date DESC LIMIT 5",
                         [e["horse"], rd]).df()
        career = con.execute("SELECT avg(y_win) w, avg(y_top3) t3, count(*) n, max(race_date) last_date FROM hist2 WHERE horse=? AND race_date<?",
                              [e["horse"], rd]).df().iloc[0]
        dist_apt = con.execute("SELECT avg(y_win) w, avg(y_top3) t3, count(*) n FROM hist2 WHERE horse=? AND surface=? AND dist_bucket=? AND race_date<?",
                                [e["horse"], e["surface"], db_, rd]).df().iloc[0]
        jockey = con.execute("""SELECT avg(y_win) w, avg(y_top3) t3, count(*) n FROM
                                 (SELECT * FROM hist2 WHERE jockey=? AND race_date<? ORDER BY race_date DESC LIMIT 200)""",
                              [e["jockey"], rd]).df().iloc[0]
        trainer = con.execute("SELECT avg(y_win) w, count(*) n FROM hist2 WHERE trainer=? AND race_date<?",
                               [e["trainer"], rd]).df().iloc[0]
        waku_b = con.execute("SELECT avg(y_win) w, count(*) n FROM hist2 WHERE track_code=? AND surface=? AND dist_bucket=? AND waku=? AND race_date<?",
                              [e["track_code"], e["surface"], db_, int(e["waku"]), rd]).df().iloc[0]

        row = dict(e)
        row["dist_bucket"] = db_
        row["field_size"] = e.get("field_size", np.nan)
        row["pace_ratio"] = 1.0  # 出馬表の時点ではペース未確定のため中立値
        row["recent5_avg_finish"] = h["finish"].mean() if len(h) else np.nan
        row["recent5_winrate"] = h["y_win"].mean() if len(h) else np.nan
        row["recent5_top3rate"] = h["y_top3"].mean() if len(h) else np.nan
        row["recent5_starts"] = len(h)
        row["recent5_style_ratio"] = (h["corner4"] / h["field_size"]).mean() if len(h) else np.nan
        row["career_winrate"] = career["w"]
        row["career_top3rate"] = career["t3"]
        row["career_starts"] = career["n"]
        row["dist_aptitude_winrate"] = dist_apt["w"]
        row["dist_aptitude_top3rate"] = dist_apt["t3"]
        row["dist_aptitude_starts"] = dist_apt["n"]
        row["days_since_last"] = (pd.Timestamp(rd) - pd.Timestamp(career["last_date"])).days if pd.notnull(career["last_date"]) else np.nan
        row["jockey_winrate"] = jockey["w"]
        row["jockey_top3rate"] = jockey["t3"]
        row["jockey_rides"] = jockey["n"]
        row["trainer_winrate"] = trainer["w"]
        row["trainer_starts"] = trainer["n"]
        row["waku_bias_winrate"] = waku_b["w"]
        row["waku_bias_n"] = waku_b["n"]
        row.setdefault("weight_diff", 0)
        out_rows.append(row)

    return pd.DataFrame(out_rows)


def predict(entries_csv: str, out_csv: str = "data/predictions.csv"):
    entries = pd.read_csv(entries_csv)
    entries["race_date"] = pd.to_datetime(entries["race_date"]).dt.date
    feat_df = build_asof_features(entries)

    for c in CATEGORICAL_FEATURES:
        feat_df[c] = feat_df[c].astype("category")
    for c in NUMERIC_FEATURES:
        feat_df[c] = pd.to_numeric(feat_df[c], errors="coerce")

    model_win = lgb.Booster(model_file="data/model_win.txt")
    model_top3 = lgb.Booster(model_file="data/model_top3.txt")

    feat_df["pred_win"] = model_win.predict(feat_df[FEATURES])
    feat_df["pred_top3"] = model_top3.predict(feat_df[FEATURES])
    feat_df["pred_win_rank"] = feat_df.groupby("race_id")["pred_win"].rank(ascending=False, method="min").astype(int)
    feat_df["pred_top3_rank"] = feat_df.groupby("race_id")["pred_top3"].rank(ascending=False, method="min").astype(int)
    feat_df["pred_win_norm"] = feat_df.groupby("race_id")["pred_win"].transform(lambda s: s / s.sum())

    if "odds_win" in feat_df.columns:
        feat_df["expected_value"] = (feat_df["pred_win_norm"] * feat_df["odds_win"]).round(2)

    out_cols = ["race_id", "race_date", "track_code", "horse", "umaban", "waku", "jockey",
                "pred_win_norm", "pred_top3", "pred_win_rank", "pred_top3_rank"]
    if "odds_win" in feat_df.columns:
        out_cols += ["odds_win", "expected_value"]
    result = feat_df[out_cols].sort_values(["race_id", "pred_win_rank"])
    result.to_csv(out_csv, index=False)
    print(f"予測結果を {out_csv} に出力しました({len(result)}頭)")
    return result


if __name__ == "__main__":
    entries_csv = sys.argv[1] if len(sys.argv) > 1 else "data/sample_entries.csv"
    predict(entries_csv)
