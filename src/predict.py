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
import os
import json
import duckdb
import lightgbm as lgb
import pandas as pd
import numpy as np


def _normalize_capped(s: pd.Series, target_sum: float, cap: float = 1.0) -> pd.Series:
    """sの合計がtarget_sumになるよう比例配分するが、どの値も cap(=100%) を超えないようにする。
    超えた分は、まだ超えていない馬たちに比例配分し直す(水準器のように繰り返し均す)。
    単純に s / s.sum() * target_sum とするだけだと、1頭だけ突出した予測値を持つ場合に
    100%を超える値が出てしまうことがあるため。"""
    s = s.astype(float).copy()
    fixed = pd.Series(False, index=s.index)
    remaining_target = target_sum
    for _ in range(len(s)):
        active = ~fixed
        active_sum = s[active].sum()
        if active_sum <= 0 or active.sum() == 0:
            break
        scale = remaining_target / active_sum
        scaled = s[active] * scale
        over = scaled[scaled > cap]
        if len(over) == 0:
            s[active] = scaled
            break
        s[over.index] = cap
        fixed[over.index] = True
        remaining_target -= cap * len(over)
    return s

RACE_RESULT = "data/race_result_merged.csv"
COURSE_STATS_PATH = "data/course_stats.json"
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


def _interval_category(days):
    if days is None or (isinstance(days, float) and np.isnan(days)):
        return None
    if days <= 10:
        return "renchou"
    if days <= 27:
        return "chu1-3shu"
    if days <= 63:
        return "chu4-8shu"
    return "hisashiburi"


def build_asof_features(entries: pd.DataFrame) -> pd.DataFrame:
    """entries(出馬表)の各馬について、race_date時点より前の実績のみからasof特徴量を作る。

    【重要】DB(race_result_merged.csv)に載っている「その馬の最終レース」が古すぎる場合
    (=直近の主催者データにアクセスできず更新できていない場合)、直近成績を古いデータから
    計算すると実態と全く違う値になり、予測が暴走する(期待値が異常に高くなる等)。
    そのため、最終戦からの間隔が STALE_THRESHOLD_DAYS を超える場合は、
    「直近成績は不明」としてNaNのまま扱う(古いデータをそれらしく使わない)。
    """
    STALE_THRESHOLD_DAYS = 180  # これより空いていたら「直近データなし」として扱う

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
        TRY_CAST("斤量" AS DOUBLE) AS weight_carry,
        TRY_CAST("着順" AS INTEGER) AS finish,
        CASE WHEN TRY_CAST("着順" AS INTEGER)=1 THEN 1 ELSE 0 END AS y_win,
        CASE WHEN TRY_CAST("着順" AS INTEGER)<=3 THEN 1 ELSE 0 END AS y_top3,
        TRY_CAST("4コーナー" AS INTEGER) AS corner4,
        count(*) OVER (PARTITION BY "レースID") AS field_size
    FROM read_csv_auto('{RACE_RESULT}', encoding='utf-8', union_by_name=true, types={{'着順': 'VARCHAR'}})
    WHERE "レース日付" IS NOT NULL AND "馬名" IS NOT NULL
    """)
    con.execute("""
    CREATE TABLE hist2 AS
    SELECT *, CASE
        WHEN distance IS NULL THEN NULL
        WHEN distance < 1400 THEN 'sprint' WHEN distance < 1800 THEN 'mile'
        WHEN distance < 2200 THEN 'middle' ELSE 'long' END AS dist_bucket
    FROM hist
    """)

    out_rows = []
    for _, e in entries.iterrows():
        rd = e["race_date"]
        distance_val = e.get("distance")
        if pd.isna(distance_val):
            db_ = None
        else:
            db_ = "sprint" if distance_val < 1400 else "mile" if distance_val < 1800 else "middle" if distance_val < 2200 else "long"
        horse_name = str(e["horse"]) if pd.notna(e.get("horse")) else None
        jockey_name = str(e["jockey"]) if pd.notna(e.get("jockey")) else None
        trainer_name = str(e["trainer"]) if pd.notna(e.get("trainer")) else None
        surface_val = str(e["surface"]) if pd.notna(e.get("surface")) else None

        h = con.execute("SELECT * FROM hist2 WHERE horse=? AND race_date<? ORDER BY race_date DESC LIMIT 5",
                         [horse_name, rd]).df()
        career = con.execute("SELECT avg(y_win) w, avg(y_top3) t3, count(*) n, max(race_date) last_date FROM hist2 WHERE horse=? AND race_date<?",
                              [horse_name, rd]).df().iloc[0]
        if surface_val and db_:
            dist_apt = con.execute("SELECT avg(y_win) w, avg(y_top3) t3, count(*) n FROM hist2 WHERE horse=? AND surface=? AND dist_bucket=? AND race_date<?",
                                    [horse_name, surface_val, db_, rd]).df().iloc[0]
        else:
            dist_apt = pd.Series({"w": np.nan, "t3": np.nan, "n": 0})
        if jockey_name:
            jockey = con.execute("""SELECT avg(y_win) w, avg(y_top3) t3, count(*) n FROM
                                     (SELECT * FROM hist2 WHERE jockey=? AND race_date<? ORDER BY race_date DESC LIMIT 200)""",
                                  [jockey_name, rd]).df().iloc[0]
        else:
            jockey = pd.Series({"w": np.nan, "t3": np.nan, "n": 0})
        if trainer_name:
            trainer = con.execute("""SELECT avg(y_win) w, count(*) n FROM hist2
                                      WHERE replace(replace(trainer,'・',''),' ','')=replace(replace(?,'・',''),' ','')
                                      AND race_date<?""",
                                   [trainer_name, rd]).df().iloc[0]
        else:
            trainer = pd.Series({"w": np.nan, "n": 0})
        waku_val = e.get("waku")
        if pd.notna(waku_val) and surface_val and db_:
            waku_b = con.execute("SELECT avg(y_win) w, count(*) n FROM hist2 WHERE track_code=? AND surface=? AND dist_bucket=? AND waku=? AND race_date<?",
                                  [e["track_code"], surface_val, db_, int(waku_val), rd]).df().iloc[0]
        else:
            waku_b = pd.Series({"w": np.nan, "n": 0})

        row = dict(e)
        row["dist_bucket"] = db_
        row["field_size"] = e.get("field_size", np.nan)
        row["pace_ratio"] = 1.0  # 出馬表の時点ではペース未確定のため中立値
        row["recent5_avg_finish"] = h["finish"].mean() if len(h) else np.nan
        row["recent5_winrate"] = h["y_win"].mean() if len(h) else np.nan
        row["recent5_top3rate"] = h["y_top3"].mean() if len(h) else np.nan
        row["recent5_starts"] = len(h)
        row["recent5_style_ratio"] = (h["corner4"] / h["field_size"]).mean() if len(h) else np.nan
        days_since_last = (pd.Timestamp(rd) - pd.Timestamp(career["last_date"])).days if pd.notnull(career["last_date"]) else np.nan
        is_stale = pd.isna(days_since_last) or days_since_last > STALE_THRESHOLD_DAYS

        if is_stale:
            # DB側のこの馬の最終戦が古すぎる(=直近の実データを持っていない)ので、
            # 直近成績だけでなく、キャリア通算成績・距離適性など「この馬固有」の
            # 特徴量はすべて不明(NaN)として扱う。古い時代の数字をそれらしく使うと、
            # 実際は下降傾向の馬を「勝率2割の実力馬」のように誤って高評価してしまう。
            #
            # ただし【脚質だけは例外】。勝率・着順は時間とともに変わる「能力・調子」の指標だが、
            # 脚質(逃げ/先行/差し/追込)は馬の性質でありキャリアを通じて比較的安定するため、
            # 古いデータからでも参考値として意味がある。表示用に残す。
            row["career_winrate"] = np.nan
            row["career_top3rate"] = np.nan
            row["career_starts"] = 0
            row["dist_aptitude_winrate"] = np.nan
            row["dist_aptitude_top3rate"] = np.nan
            row["dist_aptitude_starts"] = 0
            row["days_since_last"] = np.nan
            row["interval_category"] = None
            row["recent5_avg_finish"] = np.nan
            row["recent5_winrate"] = np.nan
            row["recent5_top3rate"] = np.nan
            row["recent5_starts"] = 0
            row["last_race_finish"] = np.nan
            row["distance_change_from_last"] = np.nan
            row["weight_carry_change_from_last"] = np.nan
            # 脚質のみ、古いデータからでも算出する(モデル入力には使わず表示用)
            row["recent5_style_ratio"] = np.nan
            row["style_ratio_display"] = (h["corner4"] / h["field_size"]).mean() if len(h) else np.nan
        else:
            row["career_winrate"] = career["w"]
            row["career_top3rate"] = career["t3"]
            row["career_starts"] = career["n"]
            row["dist_aptitude_winrate"] = dist_apt["w"]
            row["dist_aptitude_top3rate"] = dist_apt["t3"]
            row["dist_aptitude_starts"] = dist_apt["n"]
            row["days_since_last"] = days_since_last
            row["interval_category"] = _interval_category(days_since_last)
            row["recent5_avg_finish"] = h["finish"].mean() if len(h) else np.nan
            row["recent5_winrate"] = h["y_win"].mean() if len(h) else np.nan
            row["recent5_top3rate"] = h["y_top3"].mean() if len(h) else np.nan
            row["recent5_starts"] = len(h)
            row["recent5_style_ratio"] = (h["corner4"] / h["field_size"]).mean() if len(h) else np.nan
            row["style_ratio_display"] = row["recent5_style_ratio"]
            if len(h):
                last_row = h.iloc[0]
                row["last_race_finish"] = last_row["finish"]
                row["distance_change_from_last"] = e["distance"] - last_row["distance"]
                row["weight_carry_change_from_last"] = e["weight_carry"] - last_row["weight_carry"] if "weight_carry" in last_row else np.nan
            else:
                row["last_race_finish"] = np.nan
                row["distance_change_from_last"] = np.nan
                row["weight_carry_change_from_last"] = np.nan

        row["jockey_winrate"] = jockey["w"]
        row["jockey_top3rate"] = jockey["t3"]
        row["jockey_rides"] = jockey["n"]
        row["trainer_winrate"] = trainer["w"]
        row["trainer_starts"] = trainer["n"]
        row["waku_bias_winrate"] = waku_b["w"]
        row["waku_bias_n"] = waku_b["n"]
        row.setdefault("weight_diff", 0)
        row.setdefault("race_class", np.nan)  # 出馬表の時点ではクラス格付けを別途与えない限り不明

        # 騎手×調教師コンビの過去成績
        if jockey_name and trainer_name:
            combo = con.execute("""SELECT avg(y_win) w, count(*) n FROM hist2
                                    WHERE jockey=? AND replace(replace(trainer,'・',''),' ','')=replace(replace(?,'・',''),' ','')
                                    AND race_date<?""",
                                 [jockey_name, trainer_name, rd]).df().iloc[0]
        else:
            combo = pd.Series({"w": np.nan, "n": 0})
        row["jockey_trainer_combo_winrate"] = combo["w"]
        row["jockey_trainer_combo_n"] = combo["n"]

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
    # 3連対率もレース内で正規化する。1レースにつき必ず3頭が3着以内に入るため、
    # 頭数に関わらずレース内合計が3(300%)になるよう揃える(生の予測値のままだと
    # レースによって合計が161%だったり450%だったりバラつき、馬同士の比較に使えないため)。
    # ただし単純に合計を揃えるだけだと1頭だけ突出した予測値がある場合に100%を超えてしまうため、
    # 100%を上限にキャップし、超えた分は他の馬に配り直す。
    feat_df["pred_top3_norm"] = feat_df.groupby("race_id")["pred_top3"].transform(lambda s: _normalize_capped(s, target_sum=3.0))

    # ------------------------------------------------------------------
    # 追加ファクター(1着率・3連対率だけでは見えない情報を補う)
    # ------------------------------------------------------------------

    # 1) 信頼度スコア: この予測がどれだけ確かなデータに基づいているか(0-100)
    #    出走実績が少ない馬・DBが古い馬は予測の根拠が薄いため、それを明示する。
    def _confidence(row):
        score = 0
        starts = row.get("career_starts") or 0
        score += min(starts, 15) / 15 * 45           # キャリア実績(最大45点)
        r5 = row.get("recent5_starts") or 0
        score += min(r5, 5) / 5 * 30                  # 直近5走が揃っているか(最大30点)
        apt = row.get("dist_aptitude_starts") or 0
        score += min(apt, 5) / 5 * 25                 # 同条件での実績(最大25点)
        return round(score)
    feat_df["confidence"] = feat_df.apply(_confidence, axis=1)

    # 2) 安定度: 3連対率 ÷ 1着率。値が大きいほど「勝ち切れないが崩れにくい」タイプ、
    #    小さいほど「勝つか凡走かの一発型」。複勝・ワイド向きか単勝向きかの判断材料。
    feat_df["stability"] = (feat_df["pred_top3_norm"] / feat_df["pred_win_norm"].clip(lower=1e-6)).round(2)

    # 3) 想定脚質: 過去の4コーナー通過位置(頭数比)から、逃げ/先行/差し/追込を推定。
    def _style(v):
        if pd.isna(v):
            return "不明"
        if v <= 0.15:
            return "逃げ"
        if v <= 0.40:
            return "先行"
        if v <= 0.70:
            return "差し"
        return "追込"
    feat_df["running_style"] = feat_df["style_ratio_display"].apply(_style)

    # 4) near_top: 1着率上位馬との差。1位との差が小さいほど「混戦」であることを示す。
    feat_df["gap_from_top"] = feat_df.groupby("race_id")["pred_win_norm"].transform(
        lambda s: (s.max() - s) * 100).round(1)

    # 5) 展開利: そのレースの脚質構成から、各馬がペースの恩恵を受けやすいかを判定する。
    #    逃げ馬が多い -> ハイペースになりやすく差し・追込有利
    #    逃げ馬が少ない -> スロー濃厚で逃げ・先行有利
    pace_edge = pd.Series("—", index=feat_df.index, dtype=object)
    for _, idx in feat_df.groupby("race_id").groups.items():
        g = feat_df.loc[idx]
        n_front = g["running_style"].isin(["逃げ", "先行"]).sum()
        n_known = (g["running_style"] != "不明").sum()
        if n_known < 3:
            continue
        front_ratio = n_front / n_known
        for i, st in g["running_style"].items():
            if st == "不明":
                continue
            if front_ratio >= 0.55:            # 前に行く馬が多い = ハイペース想定
                pace_edge[i] = "◎" if st in ("差し", "追込") else "△"
            elif front_ratio <= 0.30:          # 前に行く馬が少ない = スロー想定
                pace_edge[i] = "◎" if st in ("逃げ", "先行") else "△"
            else:
                pace_edge[i] = "○"
    feat_df["pace_edge"] = pace_edge

    # 6) コース相性: そのコース(競馬場×芝ダ×距離)で、その馬の脚質・枠がどれだけ有利か。
    #    build_course_stats.py が出力した実データ集計(course_stats.json)を参照する。
    course_stats = {}
    if os.path.exists(COURSE_STATS_PATH):
        with open(COURSE_STATS_PATH, encoding="utf-8") as fp:
            course_stats = json.load(fp)

    STYLE_KEY = {"逃げ": "nige_top3_pct", "先行": "senko_top3_pct",
                 "差し": "sashi_top3_pct", "追込": "oikomi_top3_pct"}

    def _course_fit(row):
        code = str(row.get("track_code")).zfill(2)
        key = f"{code}_{row.get('surface')}_{int(row['distance'])}" if pd.notna(row.get("distance")) else None
        st = course_stats.get(key) if key else None
        if not st:
            return None
        scores = []
        # 脚質の相性: そのコースでの当該脚質の複勝率 ÷ 全脚質平均
        sk = STYLE_KEY.get(row.get("running_style"))
        if sk and st.get(sk) is not None:
            vals = [st[v] for v in STYLE_KEY.values() if st.get(v) is not None]
            if vals:
                scores.append(st[sk] / (sum(vals) / len(vals)))
        # 枠の相性: そのコースでの当該ゾーンの複勝率 ÷ 全ゾーン平均
        waku = row.get("waku")
        if pd.notna(waku):
            zone = "inner_top3_pct" if waku <= 2 else ("outer_top3_pct" if waku >= 7 else "mid_top3_pct")
            zvals = [st[z] for z in ("inner_top3_pct", "mid_top3_pct", "outer_top3_pct") if st.get(z) is not None]
            if st.get(zone) is not None and zvals:
                scores.append(st[zone] / (sum(zvals) / len(zvals)))
        if not scores:
            return None
        return round(sum(scores) / len(scores), 2)
    feat_df["course_fit"] = feat_df.apply(_course_fit, axis=1)

    if "odds_win" in feat_df.columns:
        feat_df["expected_value"] = (feat_df["pred_win_norm"] * feat_df["odds_win"]).round(2)

    out_cols = ["race_id", "race_date", "track_code", "horse", "umaban", "waku", "jockey",
                "pred_win_norm", "pred_top3_norm", "pred_win_rank", "pred_top3_rank",
                "confidence", "stability", "running_style", "gap_from_top", "pace_edge", "course_fit"]
    for optional_col in ["track_name", "race_number", "race_name", "post_time"]:
        if optional_col in feat_df.columns:
            out_cols.append(optional_col)
    if "odds_win" in feat_df.columns:
        out_cols += ["odds_win", "expected_value"]
    result = feat_df[out_cols].sort_values(["race_id", "pred_win_rank"])
    result.to_csv(out_csv, index=False)
    print(f"予測結果を {out_csv} に出力しました({len(result)}頭)")
    return result


if __name__ == "__main__":
    entries_csv = sys.argv[1] if len(sys.argv) > 1 else "data/sample_entries.csv"
    predict(entries_csv)
