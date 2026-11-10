"""
JRA: レースごとの「万馬券率(3連単が1万円以上になる確率)」と「3連単の配当の目安(中央値・25〜75%の範囲)」。

JRAは3連単オッズの過去データが無いので、単勝オッズから作る:
  1) 単勝オッズ → 各馬の1着率 p ∝ (1/オッズ)^β
  2) ハーヴィル式(2着・3着は p^γ2, p^γ3 で割り引く。ev_board と同じ γ)で3連単の全組の確率 P(組)
  3) 組の払戻の見込み: log(払戻) = a + b·log(1/P(組)) (+ばらつき σ)… 過去の的中組で回帰
  4) 万馬券率 = Σ P(組) × P(その組の払戻 ≥ 1万円) → 過去の結果でロジスティック補正
  5) 配当の目安 = P(組) で重み付けした払戻見込みの中央値と、確率 mass を含む一番狭い範囲
     (mass=0.55 で実際の払戻が範囲に入る割合が約50%。25〜75%点より少し狭い 6.7倍→6.3倍。
      単勝オッズからの推定なので、競輪(3連単オッズから直接)ほどは狭くならない)
学習: --fit-until より前、検証: 以降。係数は data/manbaken.json に保存し、generate_html.py が読む。

使い方: python src/manbaken.py features.parquet payouts.csv --fit-until 2025-01-01 --out data/manbaken.json
"""
import argparse
import json
import math
import os
from itertools import permutations

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = dict(beta=1.0, g2=0.838, g3=0.713, a=0.0, b=1.0, sigma=0.5, la=0.0, lb=1.0)
_PERM = {}


def load_params(path=os.path.join(HERE, "..", "data", "manbaken.json")):
    try:
        with open(path, encoding="utf-8") as f:
            return {**DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT)


def _perm(n):
    if n not in _PERM:
        _PERM[n] = np.array(list(permutations(range(n), 3)), dtype=np.int16)
    return _PERM[n]


def trifecta_probs(odds_win, beta, g2, g3):
    """単勝オッズの配列 -> (組の添字[k,3], 確率[k])"""
    o = np.asarray(odds_win, dtype=float)
    p = (1 / o) ** beta
    p /= p.sum()
    P = _perm(len(p))
    a, b, c = P[:, 0], P[:, 1], P[:, 2]
    q2, q3 = p ** g2, p ** g3
    s2 = q2.sum() - q2[a]
    pab = p[a] * q2[b] / s2
    s3 = q3.sum() - q3[a] - q3[b]
    pr = pab * q3[c] / s3
    return P, pr / pr.sum()


try:
    from scipy.special import ndtr as _phi
except ImportError:  # scipy が無い環境では math.erf で代用(遅いが同じ値)
    def _phi(x):
        return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def shortest_interval(values, weights, mass=0.5):
    """重み付き分布で確率 mass を含む「対数で一番狭い」区間(25〜75%点より狭い)"""
    idx = np.argsort(values)
    v = np.asarray(values, dtype=float)[idx]
    lv = np.log(v)
    c = np.concatenate([[0.0], np.cumsum(np.asarray(weights, dtype=float)[idx])])
    best, j = (np.inf, v[0], v[-1]), 0
    for i in range(len(v)):
        while j < len(v) and c[j + 1] - c[i] < mass:
            j += 1
        if j >= len(v):
            break
        if lv[j] - lv[i] < best[0]:
            best = (lv[j] - lv[i], v[i], v[j])
    return float(best[1]), float(best[2])


def market_view(odds_win, prm=None):
    """単勝オッズ(出走全馬) -> dict(man=万馬券率, q25, q50, q75=3連単の配当の目安(円))。オッズが揃わなければ None"""
    prm = prm or load_params()
    o = np.asarray([x for x in odds_win if x and x > 0], dtype=float)
    if len(o) < 5 or len(o) != len(odds_win):
        return None
    _, pr = trifecta_probs(o, prm["beta"], prm["g2"], prm["g3"])
    est = prm["a"] + prm["b"] * np.log(1 / pr)              # log(払戻の見込み)
    raw = float((pr * _phi((est - math.log(10000)) / prm["sigma"])).sum())
    lg = math.log(max(raw, 1e-6) / max(1 - raw, 1e-6))
    man = 1 / (1 + math.exp(-(prm["la"] + prm["lb"] * lg)))
    idx = np.argsort(est)
    cum = np.cumsum(pr[idx])
    q50 = float(math.exp(est[idx][min(int(np.searchsorted(cum, 0.5)), len(cum) - 1)]))
    lo, hi = shortest_interval(np.exp(est), pr, prm.get("mass", 0.5))
    # q25/q75 の名前のまま「半分のレースが入る一番狭い範囲」を入れる
    return dict(man=man, q25=lo, q50=q50, q75=hi, raw=raw)


def main():
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss, roc_auc_score
    ap = argparse.ArgumentParser()
    ap.add_argument("features")
    ap.add_argument("payouts")
    ap.add_argument("--fit-until", default="2025-01-01")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "manbaken.json"))
    a = ap.parse_args()
    F = pd.read_parquet(a.features, columns=["race_id", "race_date", "umaban", "odds_win", "finish"])
    F["race_id"] = F.race_id.astype(str)
    pay = pd.read_csv(a.payouts, dtype={"race_id": str, "combo": str})
    pay = pay[pay.bet_type == "3連単"].copy()
    pay["payout"] = pd.to_numeric(pay.payout.astype(str).str.replace(",", ""), errors="coerce")
    pay = pay.sort_values("payout").drop_duplicates("race_id", keep="last").set_index("race_id")
    F = F[F.race_id.isin(pay.index)]
    prm = dict(DEFAULT)
    rows = []
    for rid, g in F.groupby("race_id"):
        g = g.sort_values("umaban")
        if g.odds_win.isna().any() or (g.odds_win <= 0).any() or len(g) < 5:
            continue
        combo = [int(x) for x in str(pay.at[rid, "combo"]).split("-")]
        um = g.umaban.astype(int).tolist()
        if any(c not in um for c in combo):
            continue
        rows.append(dict(race_id=rid, date=str(g.race_date.iloc[0])[:10], odds=g.odds_win.to_numpy(float),
                         win=tuple(um.index(c) for c in combo), payout=float(pay.at[rid, "payout"])))
    print(f"レース {len(rows):,}")
    fit = np.array([r["date"] < a.fit_until for r in rows])

    # β: 的中組の確率の当てはまりが最大になる値
    def nll(beta):
        s, k = 0.0, 0
        for r, m in zip(rows, fit):
            if m:
                P, pr = trifecta_probs(r["odds"], beta, prm["g2"], prm["g3"])
                i = np.where((P[:, 0] == r["win"][0]) & (P[:, 1] == r["win"][1]) & (P[:, 2] == r["win"][2]))[0][0]
                s -= math.log(pr[i])
                k += 1
        return s / k
    sc = {b: nll(b) for b in (0.9, 1.0, 1.1, 1.2)}
    prm["beta"] = min(sc, key=sc.get)
    print("β:", {k: round(v, 4) for k, v in sc.items()}, "→", prm["beta"])
    # 払戻の回帰
    X, Y = [], []
    for r in rows:
        P, pr = trifecta_probs(r["odds"], prm["beta"], prm["g2"], prm["g3"])
        i = np.where((P[:, 0] == r["win"][0]) & (P[:, 1] == r["win"][1]) & (P[:, 2] == r["win"][2]))[0][0]
        r["lp"] = math.log(1 / pr[i])
        X.append(r["lp"])
        Y.append(math.log(r["payout"]))
    X, Y = np.array(X), np.array(Y)
    b, a0 = np.polyfit(X[fit], Y[fit], 1)
    prm["a"], prm["b"] = float(a0), float(b)
    prm["sigma"] = float(np.std(Y[fit] - (a0 + b * X[fit])))
    print(f"払戻 = exp({a0:.3f} + {b:.3f}·log(1/P)) ± σ{prm['sigma']:.3f}")
    # ロジスティック補正
    raw = np.array([market_view(r["odds"], {**prm, "la": 0.0, "lb": 1.0})["raw"] for r in rows])
    y = np.array([r["payout"] >= 10000 for r in rows]).astype(int)
    lg = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / np.clip(1 - raw, 1e-6, 1))
    lr = LogisticRegression().fit(lg[fit].reshape(-1, 1), y[fit])
    prm["la"], prm["lb"] = float(lr.intercept_[0]), float(lr.coef_[0][0])
    # 検証
    V = [(r, market_view(r["odds"], prm)) for r, m in zip(rows, fit) if not m]
    pm = np.array([v["man"] for _, v in V])
    yv = np.array([r["payout"] >= 10000 for r, _ in V]).astype(int)
    print(f"検証 {len(V):,}レース(実際の万馬券率 {yv.mean():.3f}): 対数損失 {log_loss(yv, pm):.4f}"
          f"(平均値だけ {log_loss(yv, np.full_like(pm, yv.mean())):.4f}) AUC {roc_auc_score(yv, pm):.3f}")
    T = pd.DataFrame(dict(pred=pm, y=yv, q25=[v["q25"] for _, v in V], q50=[v["q50"] for _, v in V],
                          q75=[v["q75"] for _, v in V], pay=[r["payout"] for r, _ in V]))
    T["bin"] = pd.cut(T.pred, [0, .1, .2, .3, .4, .5, .6, .7, .8, 1])
    print(T.groupby("bin", observed=True).agg(レース=("y", "size"), 予測=("pred", "mean"), 実際=("y", "mean"),
                                              目安の中央値=("q50", "median"), 実際の中央値=("pay", "median")).to_string(float_format="%.3f"))
    print(f"実際の払戻が目安の範囲に入った割合 {((T.pay >= T.q25) & (T.pay <= T.q75)).mean():.3f}"
          f" / 幅(上限÷下限)の中央値 {(T.q75 / T.q25).median():.1f}倍")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(prm, f, ensure_ascii=False, indent=1)
    print("保存:", a.out, prm)


if __name__ == "__main__":
    main()
