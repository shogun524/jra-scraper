"""
コース×距離別の実データ集計
data/race_result_merged.csv から、各競馬場×芝ダート×距離ごとに
  - 枠番別の複勝率(3着内率) -> 内枠/外枠のどちらが有利か
  - 脚質別の成績(4コーナー通過順位を頭数で正規化して分類) -> 逃げ/先行/差し/追込のどれが決まるか
  - 1番人気の信頼度(勝率・複勝率)
  - 平均頭数、平均勝ちタイム
を集計し、data/course_stats.json に出力する。

これは「調べた話」ではなく手元の実データからの集計値。
集計期間は直近の傾向を見るため 2015年以降に限定している。
"""
import duckdb
import json

RACE_RESULT = "data/race_result_merged.csv"
SINCE = "2015-01-01"
MIN_RACES = 30  # これ未満のコースはサンプル不足として除外

con = duckdb.connect()
con.execute("PRAGMA threads=1")

print("[1/4] ベーステーブル作成...")
con.execute(f"""
CREATE TABLE base AS
SELECT
    "競馬場コード" AS track_code,
    "芝・ダート区分" AS surface,
    TRY_CAST("距離(m)" AS INTEGER) AS distance,
    "レースID" AS race_id,
    TRY_CAST("枠番" AS INTEGER) AS waku,
    TRY_CAST("着順" AS INTEGER) AS finish,
    TRY_CAST("人気" AS INTEGER) AS popularity,
    TRY_CAST("4コーナー" AS INTEGER) AS corner4,
    "タイム" AS time_str
FROM read_csv_auto('{RACE_RESULT}', encoding='utf-8', union_by_name=true, types={{'着順': 'VARCHAR'}})
WHERE "レース日付" >= '{SINCE}'
  AND "芝・ダート区分" IS NOT NULL
  AND "距離(m)" IS NOT NULL
  AND TRY_CAST("着順" AS INTEGER) IS NOT NULL
""")

print("[2/4] 頭数・脚質分類の付与...")
con.execute("""
CREATE TABLE base2 AS
SELECT *,
    count(*) OVER (PARTITION BY race_id) AS field_size,
    CASE WHEN finish = 1 THEN 1 ELSE 0 END AS y_win,
    CASE WHEN finish <= 3 THEN 1 ELSE 0 END AS y_top3
FROM base
""")
con.execute("""
CREATE TABLE base3 AS
SELECT *,
    -- 4コーナー通過順位を頭数で正規化し、脚質を4分類する
    CASE
        WHEN corner4 IS NULL THEN NULL
        WHEN corner4 = 1 THEN 'nige'
        WHEN corner4 * 1.0 / NULLIF(field_size,0) <= 0.33 THEN 'senko'
        WHEN corner4 * 1.0 / NULLIF(field_size,0) <= 0.66 THEN 'sashi'
        ELSE 'oikomi'
    END AS style
FROM base2
""")

print("[3/4] コース×距離ごとの集計...")
rows = con.execute(f"""
SELECT
    track_code, surface, distance,
    count(distinct race_id) AS n_races,
    round(avg(field_size), 1) AS avg_field_size,
    -- 1番人気の信頼度
    round(avg(CASE WHEN popularity = 1 THEN y_win * 1.0 END) * 100, 1) AS fav_win_pct,
    round(avg(CASE WHEN popularity = 1 THEN y_top3 * 1.0 END) * 100, 1) AS fav_top3_pct,
    -- 枠番別の複勝率(内枠1-2枠 / 中枠3-6枠 / 外枠7-8枠)
    round(avg(CASE WHEN waku <= 2 THEN y_top3 * 1.0 END) * 100, 1) AS inner_top3_pct,
    round(avg(CASE WHEN waku BETWEEN 3 AND 6 THEN y_top3 * 1.0 END) * 100, 1) AS mid_top3_pct,
    round(avg(CASE WHEN waku >= 7 THEN y_top3 * 1.0 END) * 100, 1) AS outer_top3_pct,
    -- 脚質別の複勝率
    round(avg(CASE WHEN style = 'nige'   THEN y_top3 * 1.0 END) * 100, 1) AS nige_top3_pct,
    round(avg(CASE WHEN style = 'senko'  THEN y_top3 * 1.0 END) * 100, 1) AS senko_top3_pct,
    round(avg(CASE WHEN style = 'sashi'  THEN y_top3 * 1.0 END) * 100, 1) AS sashi_top3_pct,
    round(avg(CASE WHEN style = 'oikomi' THEN y_top3 * 1.0 END) * 100, 1) AS oikomi_top3_pct
FROM base3
GROUP BY track_code, surface, distance
HAVING count(distinct race_id) >= {MIN_RACES}
ORDER BY track_code, surface, distance
""").df()

print(f"  -> {len(rows)} コース分を集計")

print("[4/4] JSON書き出し...")
out = {}
for _, r in rows.iterrows():
    key = f"{r['track_code']}_{r['surface']}_{int(r['distance'])}"
    out[key] = {k: (None if str(v) == 'nan' else (int(v) if k in ('n_races',) else float(v)))
                for k, v in r.items() if k not in ('track_code', 'surface', 'distance')}
    out[key]['track_code'] = r['track_code']
    out[key]['surface'] = r['surface']
    out[key]['distance'] = int(r['distance'])

with open("data/course_stats.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"  -> data/course_stats.json ({len(out)}件)")
