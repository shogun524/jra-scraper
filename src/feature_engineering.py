"""
JRA競馬AI - 特徴量エンジニアリング
race_result.csv / laptime.csv を元に、馬単位の予測用特徴量テーブルを構築する。

重要: 全ての集計特徴量は「そのレースより前のデータのみ」を使う(リーク防止)。
出力: data/features.parquet
"""
import duckdb
import time

RACE_RESULT = "data/race_result.csv"
LAPTIME = "data/laptime.csv"
OUT_PARQUET = "data/features.parquet"

t0 = time.time()
con = duckdb.connect()
con.execute("PRAGMA threads=1")  # 1CPU環境向け

print("[1/6] ベーステーブル読み込み...")
con.execute(f"""
CREATE TABLE base AS
SELECT
    "レース馬番ID"        AS row_id,
    "レースID"            AS race_id,
    CAST("レース日付" AS DATE) AS race_date,
    "競馬場コード"        AS track_code,
    "競馬場名"            AS track_name,
    "芝・ダート区分"      AS surface,
    "距離(m)"             AS distance,
    "馬場状態1"           AS baba,
    "天候"                AS weather,
    TRY_CAST("枠番" AS INTEGER) AS waku,
    TRY_CAST("馬番" AS INTEGER) AS umaban,
    "馬名"                AS horse,
    "性別"                AS sex,
    TRY_CAST("馬齢" AS INTEGER) AS age,
    TRY_CAST("斤量" AS DOUBLE) AS weight_carry,
    "騎手"                AS jockey,
    "調教師"              AS trainer,
    TRY_CAST("着順" AS INTEGER) AS finish,
    TRY_CAST("単勝" AS DOUBLE) AS odds_win,
    TRY_CAST("人気" AS INTEGER) AS popularity,
    TRY_CAST("馬体重" AS INTEGER) AS horse_weight,
    TRY_CAST("場体重増減" AS INTEGER) AS weight_diff,
    TRY_CAST("上り" AS DOUBLE) AS last3f,
    TRY_CAST("4コーナー" AS INTEGER) AS corner4,
    TRY_CAST("1コーナー" AS INTEGER) AS corner1
FROM read_csv_auto('{RACE_RESULT}', encoding='utf-8')
WHERE "レース日付" IS NOT NULL AND "馬名" IS NOT NULL
""")
n = con.execute("SELECT count(*) FROM base").fetchone()[0]
print(f"  -> {n:,} 行  ({time.time()-t0:.1f}s)")

print("[2/6] レース単位の付随情報(頭数・距離帯・ラップ)...")
con.execute("""
CREATE TABLE base2 AS
SELECT
    b.*,
    count(*) OVER (PARTITION BY race_id) AS field_size,
    CASE
        WHEN distance < 1400 THEN 'sprint'
        WHEN distance < 1800 THEN 'mile'
        WHEN distance < 2200 THEN 'middle'
        ELSE 'long'
    END AS dist_bucket,
    CASE WHEN finish = 1 THEN 1 ELSE 0 END AS y_win,
    CASE WHEN finish IS NOT NULL AND finish <= 3 THEN 1 ELSE 0 END AS y_top3
FROM base b
""")

print("[3/6] ラップ(前半ペース)の結合 -> ハイペース/スローペース判定...")
con.execute(f"""
CREATE TABLE lap AS
SELECT
    "レースID" AS race_id,
    TRY_CAST("前半3ハロン" AS DOUBLE) AS early3f,
    TRY_CAST("上がり3ハロン" AS DOUBLE) AS late3f
FROM read_csv_auto('{LAPTIME}', encoding='utf-8')
""")
con.execute("""
CREATE TABLE base3 AS
SELECT b.*, l.early3f, l.late3f,
    -- そのコース・距離帯における平均前半3F比で相対ペースを表現(1.0=平均的, <1.0=ハイペース)
    l.early3f / NULLIF(AVG(l.early3f) OVER (PARTITION BY b.track_code, b.surface, b.dist_bucket), 0) AS pace_ratio
FROM base2 b
LEFT JOIN lap l USING (race_id)
""")

print("[4/6] 馬別の過去成績特徴量(直近5走・キャリア通算, リーク防止)...")
con.execute("""
CREATE TABLE horse_feat AS
SELECT *,
    -- 直近5走(このレースより前)
    AVG(finish)  OVER w5 AS recent5_avg_finish,
    AVG(y_win)   OVER w5 AS recent5_winrate,
    AVG(y_top3)  OVER w5 AS recent5_top3rate,
    COUNT(*)     OVER w5 AS recent5_starts,
    -- キャリア通算(このレースより前)
    AVG(y_win)   OVER wc AS career_winrate,
    AVG(y_top3)  OVER wc AS career_top3rate,
    COUNT(*)     OVER wc AS career_starts,
    -- 同コース×同馬場×同距離帯での適性(このレースより前)
    AVG(y_win)   OVER wd AS dist_aptitude_winrate,
    AVG(y_top3)  OVER wd AS dist_aptitude_top3rate,
    COUNT(*)     OVER wd AS dist_aptitude_starts,
    -- 前走からの間隔(日数)
    date_diff('day', LAG(race_date) OVER wo, race_date) AS days_since_last,
    -- 脚質指標: このレースより前の平均(4コーナー通過順位/頭数) -> 0に近いほど先行, 1に近いほど追込
    AVG(corner4 * 1.0 / NULLIF(field_size,0)) OVER w5 AS recent5_style_ratio
FROM base3
WINDOW
    wo AS (PARTITION BY horse ORDER BY race_date, race_id),
    w5 AS (PARTITION BY horse ORDER BY race_date, race_id ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
    wc AS (PARTITION BY horse ORDER BY race_date, race_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
    wd AS (PARTITION BY horse, surface, dist_bucket ORDER BY race_date, race_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
""")

print("[5/6] 騎手・調教師・枠番トラックバイアスの過去統計(リーク防止)...")
con.execute("""
CREATE TABLE full_feat AS
SELECT *,
    -- 騎手: 直近200騎乗の成績
    AVG(y_win)  OVER wj AS jockey_winrate,
    AVG(y_top3) OVER wj AS jockey_top3rate,
    COUNT(*)    OVER wj AS jockey_rides,
    -- 調教師: 通算成績
    AVG(y_win)  OVER wt AS trainer_winrate,
    COUNT(*)    OVER wt AS trainer_starts,
    -- 枠番トラックバイアス: 同コース×同馬場種別×同距離帯×同枠番の過去勝率(このレースより前)
    AVG(y_win)  OVER wk AS waku_bias_winrate,
    COUNT(*)    OVER wk AS waku_bias_n
FROM horse_feat
WINDOW
    wj AS (PARTITION BY jockey ORDER BY race_date, race_id ROWS BETWEEN 200 PRECEDING AND 1 PRECEDING),
    wt AS (PARTITION BY trainer ORDER BY race_date, race_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
    wk AS (PARTITION BY track_code, surface, dist_bucket, waku ORDER BY race_date, race_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
""")

print("[6/6] Parquet書き出し...")
con.execute(f"COPY full_feat TO '{OUT_PARQUET}' (FORMAT PARQUET)")
n2 = con.execute("SELECT count(*) FROM full_feat").fetchone()[0]
print(f"  -> {n2:,} 行を {OUT_PARQUET} へ出力  (合計 {time.time()-t0:.1f}s)")
