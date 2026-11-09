"""
EV(期待値)計算に使う「AIと市場の確率の混ぜ方」を、過去の結果から推定して data/ev_calibration.json に保存する。

  p ∝ p_ai^a × p_mkt^b × exp(c × オッズの動き)

- p_ai : AIの1着率(レース内で合計1)
- p_mkt: 単勝オッズから計算した市場の確率(控除率分を除いて合計1)
- オッズの動き: その日の最初の記録から直前の記録までの単勝オッズの変化 log(最初/直前)。
  プラス = 買われて下がった馬。odds_history(odds_snapshot.py が10分ごとに記録)が十分たまるまでは c=0。

(1) a, b はウォークフォワード予測(各年をその年より前だけで学習)と確定オッズで推定する。
(2) c はオッズ履歴と結果がそろったレースが --min-races 以上あるときだけ、(a, b も含めて)推定し直す。
    このときの p_mkt は「直前の記録のオッズ」で、実際にページで表示・判断するときと同じ条件になる。

使い方:
    python src/fit_ev_calibration.py --wf data/wf_preds.parquet --site site --out data/ev_calibration.json
"""
import argparse
import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def _nll(theta, rid, X, win):
    s = X @ theta
    n = rid.max() + 1
    mx = np.full(n, -np.inf)
    np.maximum.at(mx, rid, s)
    z = np.zeros(n)
    np.add.at(z, rid, np.exp(s - mx[rid]))
    return -(s[win] - mx[rid[win]] - np.log(z[rid[win]])).mean()


def fit(df, cols):
    """条件付きロジット(レース内で勝ち馬を当てる尤度)を最大化する係数を返す"""
    rid = pd.factorize(df["race_id"])[0]
    X = df[cols].to_numpy(dtype=float)
    win = df["win"].to_numpy() == 1
    x0 = np.array([0.0, 1.0] + [0.0] * (len(cols) - 2))
    r = minimize(_nll, x0, args=(rid, X, win), method="BFGS")
    base = _nll(np.array([0.0, 1.0] + [0.0] * (len(cols) - 2)), rid, X, win)  # 市場だけ
    return r.x, float(r.fun), float(base), int(df["race_id"].nunique())


def market_cols(df, odds_col):
    inv = 1 / df[odds_col]
    df["p_mkt"] = inv / inv.groupby(df["race_id"]).transform("sum")
    df["l_ai"] = np.log(df["p_ai"].clip(1e-5))
    df["l_mkt"] = np.log(df["p_mkt"].clip(1e-5))
    return df


def from_walkforward(path):
    p = pd.read_parquet(path)
    p = p[p["odds_win"].notna() & (p["odds_win"] > 0) & p["finish"].notna()].copy()
    ok = p.groupby("race_id").agg(n=("odds_win", "size"), w=("finish", lambda s: (s == 1).sum()))
    p = p[p["race_id"].isin(ok[ok.w == 1].index)].copy()
    p["win"] = (p["finish"] == 1).astype(int)
    p["p_ai"] = p["ai_win"]
    return market_cols(p, "odds_win")


def from_odds_history(site):
    """gh-pages の odds_history と history(予測・結果)から、オッズの動き込みの学習データを作る"""
    frames = []
    for f in sorted(glob.glob(os.path.join(site, "odds_history", "odds_*.csv"))):
        d = os.path.basename(f)[5:-4]
        pf = os.path.join(site, "history", f"predictions_{d}.csv")
        rf = os.path.join(site, "history", f"results_{d}.csv")
        if not (os.path.exists(pf) and os.path.exists(rf)):
            continue
        oh = pd.read_csv(f, dtype={"race_id": str, "combo": str})
        oh = oh[oh["bet"] == "単勝"].copy()
        if oh.empty:
            continue
        oh["umaban"] = oh["combo"].astype(int)
        oh = oh.sort_values("ts")
        first = oh.groupby(["race_id", "umaban"])["odds"].first().rename("odds_first")
        last = oh.groupby(["race_id", "umaban"])["odds"].last().rename("odds_last")
        pred = pd.read_csv(pf, dtype={"race_id": str})[["race_id", "umaban", "pred_win_norm"]]
        res = pd.read_csv(rf, dtype={"race_id": str})[["race_id", "umaban", "finish"]]
        m = pred.merge(res, on=["race_id", "umaban"]).join(first, on=["race_id", "umaban"]).join(last, on=["race_id", "umaban"])
        frames.append(m)
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    d = d[d["odds_last"].notna() & d["odds_first"].notna()]
    ok = d.groupby("race_id").agg(w=("finish", lambda s: (s == 1).sum()))
    d = d[d["race_id"].isin(ok[ok.w == 1].index)].copy()
    d["win"] = (d["finish"] == 1).astype(int)
    d["p_ai"] = d["pred_win_norm"] / d.groupby("race_id")["pred_win_norm"].transform("sum")
    d["move"] = np.log(d["odds_first"] / d["odds_last"])
    return market_cols(d, "odds_last")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", default="data/wf_preds.parquet")
    ap.add_argument("--site", default="")
    ap.add_argument("--out", default="data/ev_calibration.json")
    ap.add_argument("--min-races", type=int, default=300)
    a = ap.parse_args()

    cal = {"a": 0.0, "b": 1.0, "c_move": 0.0, "updated": datetime.now().strftime("%Y-%m-%d")}
    if a.wf and os.path.exists(a.wf):
        d = from_walkforward(a.wf)
        (ca, cb), ll, base, n = fit(d, ["l_ai", "l_mkt"])
        cal.update(a=round(float(ca), 4), b=round(float(cb), 4), source_ab=f"ウォークフォワード予測+確定オッズ {n}レース",
                   logloss_market=round(base, 5), logloss_blend=round(ll, 5))
        print(f"[確定オッズ] {n}レース a(AI)={ca:.3f} b(市場)={cb:.3f}  対数損失 市場のみ {base:.5f} -> 混合 {ll:.5f}")
    if a.site:
        h = from_odds_history(a.site)
        n = h["race_id"].nunique() if not h.empty else 0
        cal["odds_history_races"] = int(n)
        if n >= a.min_races:
            (ca, cb, cc), ll, base, n = fit(h, ["l_ai", "l_mkt", "move"])
            cal.update(a=round(float(ca), 4), b=round(float(cb), 4), c_move=round(float(cc), 4),
                       source_move=f"オッズ履歴+結果 {n}レース", logloss_market_live=round(base, 5),
                       logloss_blend_live=round(ll, 5))
            print(f"[オッズ履歴] {n}レース a={ca:.3f} b={cb:.3f} c(動き)={cc:.3f}  対数損失 {base:.5f} -> {ll:.5f}")
        else:
            print(f"[オッズ履歴] {n}レース(<{a.min_races})。動きの係数はまだ推定しない(c=0)")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
    print(json.dumps(cal, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
