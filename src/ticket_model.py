"""
馬券1点ごとの期待値(払戻の平均)を予測するモデル。

各レースで、AI上位6頭と人気上位5頭から候補の買い目を作る(単勝・複勝・馬連・ワイド・3連複)。
各買い目について、
  - AIの1着率から計算した的中確率(Harville: 1着率を順に条件付きで掛け合わせる方法)
  - 市場(単勝オッズ)から計算した的中確率
  - 券種・頭数・クラス・◎の自信度などのレース情報
を特徴量にして、「100円あたりの払戻(外れは0)」をTweedie回帰で学習する。
予測値が閾値以上の買い目だけを買ったときの回収率を、学習に使っていない期間で確かめる。

使い方:
    python src/ticket_model.py data/wf_preds.parquet data/payouts.csv --train-until 2023-01-01 --tune-until 2024-01-01
"""
import argparse
import os
from itertools import combinations

import lightgbm as lgb
import numpy as np
import pandas as pd

BET_CODE = {"単勝": 0, "複勝": 1, "馬連": 2, "ワイド": 3, "3連複": 4}
FEATS = ["bet", "lp_ai", "lp_mkt", "lratio", "ai_rank_min", "ai_rank_max", "pop_min", "pop_max",
         "field_size", "race_class", "turf", "fav_odds", "ai1_win", "ai_gap"]


def harville_tables(p):
    """p: 1着率(合計1)。戻り値: 3着内確率P3[i]、馬連Q[i,j]、ワイドW[i,j]、3連複S[i,j,k](いずれも順不同の組の確率)"""
    n = len(p)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, None)
    p = p / p.sum()
    a = p[:, None, None]
    b = p[None, :, None] / np.clip(1 - p[:, None, None], 1e-9, None)
    c = p[None, None, :] / np.clip(1 - p[:, None, None] - p[None, :, None], 1e-9, None)
    T = a * b * c  # 1着i・2着j・3着kの確率
    idx = np.arange(n)
    T[idx, idx, :] = 0
    T[idx, :, idx] = 0
    T[:, idx, idx] = 0
    # 3頭の組(順不同)の確率 = 6通りの着順の和
    S = T + T.transpose(0, 2, 1) + T.transpose(1, 0, 2) + T.transpose(1, 2, 0) + T.transpose(2, 0, 1) + T.transpose(2, 1, 0)
    P12 = p[:, None] * p[None, :] / np.clip(1 - p[:, None], 1e-9, None)
    np.fill_diagonal(P12, 0)
    Q = P12 + P12.T          # 馬連: 1・2着がi,j
    W = S.sum(axis=2)        # ワイド: i,jがともに3着内(3頭目kで和)
    P3 = S.sum(axis=(1, 2)) / 2.0  # 3着内: 残り2頭の順序付きペアで2回数えるので半分
    return P3, Q, W, S


def build_tickets(preds: pd.DataFrame, pay: dict | None):
    rows = []
    for rid, g in preds.groupby("race_id", sort=False):
        if len(g) < 6 or g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
            continue
        g = g.reset_index(drop=True)
        u = g["umaban"].astype(int).to_numpy()
        p_ai = g["ai_win"].to_numpy(dtype=float)
        inv = 1 / g["odds_win"].to_numpy(dtype=float)
        p_mk = inv / inv.sum()
        ai_rank = g["ai_rank"].to_numpy()
        pop = g["popularity"].fillna(pd.Series(p_mk).rank(ascending=False)).to_numpy()
        P3a, Qa, Wa, Sa = harville_tables(p_ai)
        P3m, Qm, Wm, Sm = harville_tables(p_mk)
        cand = sorted(set(np.where(ai_rank <= 6)[0]) | set(np.where(pop <= 5)[0]))
        top = g.iloc[int(np.argmin(ai_rank))]
        srt = np.sort(p_ai)
        race = (len(g), top.get("race_class"), float(top.get("surface") == "芝"), float(g["odds_win"].min()),
                float(srt[-1]), float(srt[-1] - srt[-2]))
        date = g["race_date"].iloc[0]

        def add(bt, members, pa, pm):
            key = "-".join(map(str, sorted(int(u[i]) for i in members)))
            ret = pay.get((rid, bt, key), 0) / 100.0 if pay is not None else 0.0
            rk, pp = ai_rank[members], pop[members]
            rows.append((rid, date, BET_CODE[bt], key, np.log(pa + 1e-9), np.log(pm + 1e-9),
                         np.log((pa + 1e-9) / (pm + 1e-9)), rk.min(), rk.max(), pp.min(), pp.max(), *race, ret))

        for i in cand:
            add("単勝", [i], p_ai[i], p_mk[i])
            add("複勝", [i], P3a[i], P3m[i])
        for i, j in combinations(cand, 2):
            add("馬連", [i, j], Qa[i, j], Qm[i, j])
            add("ワイド", [i, j], Wa[i, j], Wm[i, j])
        for i, j, k in combinations(cand, 3):
            add("3連複", [i, j, k], Sa[i, j, k], Sm[i, j, k])
    cols = ["race_id", "race_date", "bet", "combo", "lp_ai", "lp_mkt", "lratio", "ai_rank_min", "ai_rank_max",
            "pop_min", "pop_max", "field_size", "race_class", "turf", "fav_odds", "ai1_win", "ai_gap", "ret"]
    T = pd.DataFrame(rows, columns=cols)
    T["race_date"] = pd.to_datetime(T["race_date"])
    return T


def train(T):
    P = dict(objective="tweedie", tweedie_variance_power=1.6, learning_rate=0.03, num_leaves=31,
             min_data_in_leaf=2000, feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1,
             lambda_l2=10, verbose=-1, seed=7)
    return lgb.train(P, lgb.Dataset(T[FEATS], T["ret"], categorical_feature=["bet"]), 400)


def roi_table(T, ev_col, thresholds, n_boot=1000, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    groups = [("全券種", T)] + [(k, T[T.bet == v]) for k, v in BET_CODE.items()]
    for name, d in groups:
        for t in thresholds:
            b = d[d[ev_col] >= t]
            if len(b) == 0:
                continue
            per = b.groupby("race_id").agg(n=("ret", "size"), r=("ret", "sum"))
            n, r = per.n.to_numpy(), per.r.to_numpy()
            idx = rng.integers(0, len(per), (n_boot, len(per)))
            bs = r[idx].sum(1) / n[idx].sum(1)
            out.append(dict(券種=name, 閾値=t, 点数=int(n.sum()), レース数=len(per), 的中率=round((b.ret > 0).mean(), 3),
                            回収率=round(r.sum() / n.sum() * 100, 1),
                            下限=round(np.percentile(bs, 2.5) * 100, 1), 上限=round(np.percentile(bs, 97.5) * 100, 1)))
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("payouts")
    ap.add_argument("--train-until", default="2023-01-01")
    ap.add_argument("--tune-until", default="2024-01-01")
    ap.add_argument("--cache", default="")
    ap.add_argument("--out", default="ticket_test_preds.parquet")
    a = ap.parse_args()
    if a.cache and os.path.exists(a.cache):
        T = pd.read_parquet(a.cache)
    else:
        preds = pd.read_parquet(a.preds) if a.preds.endswith(".parquet") else pd.read_csv(a.preds)
        preds["race_id"] = preds["race_id"].astype(str)
        payouts = pd.read_csv(a.payouts, dtype={"race_id": str, "combo": str})
        preds = preds[preds.race_id.isin(set(payouts.race_id))]
        pay = payouts.groupby(["race_id", "bet_type", "combo"])["payout"].sum().to_dict()
        T = build_tickets(preds, pay)
        if a.cache:
            T.to_parquet(a.cache)
    print(f"買い目 {len(T):,} 点 / {T.race_id.nunique():,} レース")
    th = [0.8, 0.9, 1.0, 1.1, 1.2, 1.5]
    tr = T[T.race_date < a.train_until]
    tu = T[(T.race_date >= a.train_until) & (T.race_date < a.tune_until)].copy()
    te = T[T.race_date >= a.tune_until].copy()
    m = train(tr)
    tu["ev"] = m.predict(tu[FEATS])
    print("\n[調整期間] 予測期待値の閾値ごとの回収率")
    print(roi_table(tu, "ev", th).to_string(index=False))
    m2 = train(T[T.race_date < a.tune_until])
    te["ev"] = m2.predict(te[FEATS])
    print("\n[検証期間] 予測期待値の閾値ごとの回収率")
    print(roi_table(te, "ev", th).to_string(index=False))
    te[["race_id", "race_date", "bet", "combo", "ev", "ret", "lp_ai", "lp_mkt"]].to_parquet(a.out)
    imp = pd.Series(m2.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
    print("\n重要度:\n", imp.round(0).to_string())


if __name__ == "__main__":
    main()
