"""
過去走の中身から作る特徴量(学習 train_model.py と 予想 predict.py の両方がここを通る = 作り方が必ず一致する)。

旧AIは「直近5走の平均着順・勝率」などの集計しか見ておらず、オッズを使わない予想としても粗かった。
ここでは前1〜5走の中身を足す(今のオッズは使わない = 人気に寄せない、馬そのものの評価):
  - 前走・前3走の 人気・単勝オッズ(その時点の市場の評価)・勝ち馬とのタイム差・上がりの順位・位置取り・着順
  - スピード指数: 走破タイムを同じ競馬場・芝ダ・距離・馬場の勝ちタイムの基準と比べた値(斤量で少し補正)
  - 乗り替わり・芝ダ替わり・競馬場替わり・クラスの上下・頭数の変化
  - レース内の比較: 主な値について「このレースのメンバーの中で何番目か」「平均との差」
検証(2012〜2021年で学習・2022年で調整・2023〜2026年で確認、オッズは使わない):
  ◎の勝率 27.0% → 28.8%、3連対率 57.9% → 59.6%、対数損失 2.133 → 2.072(市場は1.911)

使い方:
  table = run_table("data/race_result_merged.csv")      # 1走ごとの表(重いので1回だけ作る)
  df = add(df, table)                                    # df: 1行1頭(horse, race_date, race_id, umaban, jockey, surface, track_code, race_class, field_size, weight_diff)
"""
import re

import numpy as np
import pandas as pd

from scrape_race_result import jockey_key, race_class_value

SINCE = "2005-01-01"

PREV = ["prev_popularity", "prev_l3rank_rel", "prev_c4_rel", "prev_c1_rel", "prev_fin_rel", "prev_pop_minus_fin",
        "prev_c4_minus_fin", "jockey_change", "surface_change", "track_change", "class_change", "field_size_change",
        "prev2_finish", "weight_diff_abs", "umaban_rel"]
HIST = ["h1_margin", "h3_margin", "h3_best_margin", "h1_l3rel", "h3_l3rel", "h3_c4rel", "h1_lodds", "h3_lodds",
        "h3_poprel", "h5_win_by_pop", "h3_finrel"]
SPD = ["s1", "s_best3", "s_mean5", "l3z1", "l3z_best3", "s_best_sf"]
REL_BASE = ["s_best3", "s_mean5", "s_best_sf", "h3_lodds", "h1_lodds", "h3_finrel", "jockey_winrate", "recent5_top3rate",
            "dist_aptitude_top3rate", "h3_best_margin", "l3z_best3"]
REL_ASC = {"h3_lodds", "h1_lodds", "h3_finrel", "h3_best_margin"}   # 小さいほど良い値
REL = [c + s for c in REL_BASE for s in ("_rk", "_dm")]
FEATURES = PREV + HIST + SPD + REL


def _secs(x):
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", str(x or "").strip())
    return np.nan if not m else (int(m.group(1)) * 60 if m.group(1) else 0) + float(m.group(2))


def run_table(merged_csv, since=SINCE, horses=None):
    """race_result_merged.csv -> 1走ごとの表(horses を渡すとその馬の分だけ)。各行の a_* / b_* は「そのレースを走り終えた時点」の値
    (次のレースの特徴量として使うので、今のレースを含めて集計してよい)"""
    import duckdb
    M = duckdb.connect().execute(f"""
        select "レースID" rid, "レース日付" d, "馬番" ub, "馬名" horse, "着順" fin, "タイム" t,
               "1コーナー" c1, "2コーナー" c2, "3コーナー" c3, "4コーナー" c4, "上り" l3, "単勝" odds, "人気" pop,
               "競争条件" cls, "競馬場コード" tc, "芝・ダート区分" sf, "距離(m)" dist, "馬場状態1" baba, "斤量" kin, "騎手" jky
        from read_csv_auto('{merged_csv}', all_varchar=true) where "レース日付" >= '{since}' and "馬名" is not null""").df()
    for k in ["fin", "ub", "c1", "c2", "c3", "c4", "l3", "odds", "pop", "dist", "kin"]:
        M[k] = pd.to_numeric(M[k], errors="coerce")
    M["ts"] = M["t"].map(_secs)
    M["dd"] = pd.to_datetime(M["d"], errors="coerce")
    M = M[M["dd"].notna()].copy()
    M["n"] = M.groupby("rid")["ub"].transform("size")
    M["margin"] = (M["ts"] - M.groupby("rid")["ts"].transform("min")).clip(upper=5)
    M["l3rel"] = M.groupby("rid")["l3"].rank(method="min") / M["n"]
    M["c4rel"] = M["c4"] / M["n"]
    M["c_first"] = M[["c1", "c2", "c3"]].bfill(axis=1).iloc[:, 0]
    M["c1rel"] = M["c_first"] / M["n"]
    M["finrel"] = M["fin"] / M["n"]
    M["lodds"] = np.log(M["odds"].clip(1, 999))
    M["poprel"] = M["pop"] / M["n"]
    key = ["tc", "sf", "dist", "baba"]
    W = M[M["fin"] == 1]
    std = W.groupby(key)["ts"].median().rename("std_t")
    sd = W.groupby(key)["ts"].std().rename("sd_t")
    M = M.join(std, on=key).join(sd, on=key)
    M["spd"] = -(M["ts"] - M["std_t"]) / M["sd_t"].clip(lower=0.5) + (M["kin"] - 55) * 0.05
    M["l3z"] = -(M["l3"] - M.groupby(key)["l3"].transform("median"))
    M["cls_v"] = M["cls"].map(race_class_value)
    M["jkey"] = M["jky"].map(jockey_key)
    if horses is not None:   # 予想のときは出走馬の分だけ(基準タイムは全レースで作ったあと)
        M = M[M["horse"].isin(set(map(str, horses)))]
    M = M.sort_values(["horse", "dd", "rid"]).reset_index(drop=True)
    g = M.groupby("horse")

    def roll(col, n, fn):
        return g[col].transform(lambda x: getattr(x.rolling(n, min_periods=1), fn)())
    T = pd.DataFrame({"horse": M["horse"], "dd": M["dd"], "rid": M["rid"]})
    # 前走(=この走り)の中身
    T["prev_popularity"] = M["pop"]
    T["prev_l3rank_rel"] = M["l3rel"]
    T["prev_c4_rel"] = M["c4rel"]
    T["prev_c1_rel"] = M["c1rel"]
    T["prev_fin_rel"] = M["finrel"]
    T["prev_pop_minus_fin"] = M["pop"] - M["fin"]
    T["prev_c4_minus_fin"] = M["c4"] - M["fin"]
    T["prev2_finish"] = g["fin"].shift(1)          # この走りの1つ前 = 次のレースから見て前々走
    T["p_jkey"], T["p_sf"], T["p_tc"], T["p_cls"], T["p_n"] = M["jkey"], M["sf"], M["tc"], M["cls_v"], M["n"]
    # 前1・3・5走
    T["h1_margin"] = M["margin"]
    T["h3_margin"] = roll("margin", 3, "mean")
    T["h3_best_margin"] = roll("margin", 3, "min")
    T["h1_l3rel"] = M["l3rel"]
    T["h3_l3rel"] = roll("l3rel", 3, "mean")
    T["h3_c4rel"] = roll("c4rel", 3, "mean")
    T["h1_lodds"] = M["lodds"]
    T["h3_lodds"] = roll("lodds", 3, "mean")
    T["h3_poprel"] = roll("poprel", 3, "mean")
    T["h5_win_by_pop"] = roll("pop", 5, "min")
    T["h3_finrel"] = roll("finrel", 3, "mean")
    T["s1"] = M["spd"]
    T["s_best3"] = roll("spd", 3, "max")
    T["s_mean5"] = roll("spd", 5, "mean")
    T["l3z1"] = M["l3z"]
    T["l3z_best3"] = roll("l3z", 3, "max")
    # 芝・ダートそれぞれの直近5走の最高スピード(走っていない方は前の値を持ち越す)
    for s in ("芝", "ダート"):
        v = M["spd"].where(M["sf"] == s)
        T["sbest_" + s] = v.groupby(M["horse"]).transform(lambda x: x.rolling(5, min_periods=1).max()).groupby(M["horse"]).ffill()
    return T


def add(df: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """1行1頭の df に、そのレースより前の最後の走りから作った特徴量を足す(レース当日の走りは使わない)"""
    df = df.copy()
    df["_i"] = np.arange(len(df))
    df["_dd"] = pd.to_datetime(df["race_date"]).astype("datetime64[ns]")
    left = df[["_i", "_dd", "horse"]].dropna().sort_values("_dd")
    left["horse"] = left["horse"].astype(str)
    right = table.dropna(subset=["dd"]).sort_values("dd").copy()
    right["dd"] = right["dd"].astype("datetime64[ns]")
    right["horse"] = right["horse"].astype(str)
    m = pd.merge_asof(left, right, left_on="_dd", right_on="dd", by="horse", allow_exact_matches=False)
    m = m.set_index("_i").reindex(df["_i"])
    for c in [c for c in table.columns if c not in ("horse", "dd", "rid")]:
        df[c] = m[c].values
    fs = pd.to_numeric(df.get("field_size"), errors="coerce")
    jk = df["jockey"].map(jockey_key) if "jockey" in df.columns else pd.Series(None, index=df.index)
    df["jockey_change"] = (jk != df["p_jkey"]).astype(float).where(df["p_jkey"].notna() & jk.notna())
    df["surface_change"] = (df["surface"].astype(str) != df["p_sf"]).astype(float).where(df["p_sf"].notna())
    df["track_change"] = (df["track_code"].astype(str).str.zfill(2) != df["p_tc"].astype(str).str.zfill(2)).astype(float).where(df["p_tc"].notna())
    rc = pd.to_numeric(df.get("race_class"), errors="coerce")
    df["class_change"] = (rc != df["p_cls"]).astype(float).where(df["p_cls"].notna() & rc.notna())
    df["field_size_change"] = fs - df["p_n"]
    df["weight_diff_abs"] = pd.to_numeric(df.get("weight_diff"), errors="coerce").abs()
    df["umaban_rel"] = pd.to_numeric(df["umaban"], errors="coerce") / fs
    sf = df["surface"].astype(str)
    df["s_best_sf"] = np.where(sf == "芝", df["sbest_芝"], np.where(sf == "ダート", df["sbest_ダート"], np.nan))
    g = df.groupby("race_id")
    for c in REL_BASE:
        x = pd.to_numeric(df[c], errors="coerce")
        df[c + "_rk"] = x.groupby(df["race_id"]).rank(ascending=c in REL_ASC, pct=True)
        df[c + "_dm"] = x - x.groupby(df["race_id"]).transform("mean")
    del g
    return df.drop(columns=["_i", "_dd", "p_jkey", "p_sf", "p_tc", "p_cls", "p_n", "sbest_芝", "sbest_ダート"])
