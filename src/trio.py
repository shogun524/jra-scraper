"""
3連複の買い目(込%を軸にする形)。

2023〜2026年(約1万2千レース。AI はその年より前のデータだけで学習)の検証:
  E  込%1位を軸 → 込%2〜4位 → AIの3連対率10%以上の馬     平均18.7点・的中率49.5%・回収率79%(毎年78〜80%)
  E' E のうち、その組の確率(込%から計算)が市場(単勝オッズから計算)の1.1倍以上の組だけ
                                                        平均5.2点・的中率15%・回収率92.5%(毎年87〜96%)
  (1.2倍以上だと平均3.2点・的中率8.6%・回収率97.5%)
参考: ◎軸→○▲△→3連対率10%以上 は 的中率43%・回収率73%、印で軸を変えても74%前後。
組の確率は Harville(1着の確率から2・3着を順に配る)で計算する。
"""
import itertools

import pandas as pd

TOP3_MIN = 0.10
EDGE = 1.1


def _harville_trio(q):
    d = {}
    for a, b, c in itertools.permutations(q, 3):
        qa, qb = q[a], q[b]
        if qa >= 1 or qa + qb >= 1:
            continue
        p = qa * qb / (1 - qa) * q[c] / (1 - qa - qb)
        k = tuple(sorted((a, b, c)))
        d[k] = d.get(k, 0) + p
    return d


def plan(g: pd.DataFrame):
    """1レース分 -> dict(axis, second, third, tickets=[(組, 込/市場)], edge=[組...]) か None"""
    if "mm_win" not in g.columns:
        return None
    mm = pd.to_numeric(g["mm_win"], errors="coerce")
    odds = pd.to_numeric(g["odds_win"], errors="coerce")
    if mm.isna().any() or odds.isna().any() or (odds <= 0).any() or len(g) < 6:
        return None
    um = g["umaban"].astype(int).tolist()
    pm = dict(zip(um, mm / mm.sum()))
    inv = 1 / odds
    qm = dict(zip(um, inv / inv.sum()))
    order = sorted(um, key=lambda u: -pm[u])
    axis, second = order[0], order[1:4]
    t3 = dict(zip(um, pd.to_numeric(g["pred_top3_norm"], errors="coerce").fillna(0)))
    third = [u for u in order if t3[u] >= TOP3_MIN]
    T = set()
    for b in second:
        for c in third:
            if len({axis, b, c}) == 3:
                T.add(tuple(sorted((axis, b, c))))
    if not T:
        return None
    hm, hq = _harville_trio(pm), _harville_trio(qm)
    tickets = sorted(((t, hm.get(t, 0) / hq[t] if hq.get(t) else 0) for t in T), key=lambda x: -x[1])
    edge = [t for t, r in tickets if r >= EDGE]
    return dict(axis=axis, second=second, third=third, tickets=tickets, edge=edge)


def fmt(t):
    return "-".join(map(str, t))
