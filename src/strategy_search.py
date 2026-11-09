"""
AIを使った買い方の総当たり探索(組み方 × 買うレースの条件)。

1. 各レースについて、組み方(券種と買い目の作り方)ごとに「点数」と「払戻」を計算する
2. 買う時点で分かる情報だけでレースを絞る条件(AIの自信度・◎の人気・AIと市場のずれ・頭数・クラスなど)を作る
3. 「組み方 × 条件(1つ or 2つの組み合わせ)」の回収率を、選定期間で計算し、検証期間で確かめる

使い方:
    python src/strategy_search.py data/wf_preds.parquet data/payouts.csv --select-until 2024-01-01 --out strategy_search.csv

preds に必要な列: race_id, race_date, umaban, ai_win, ai_rank, popularity, odds_win, race_class, surface
(ai_win はレース内で合計1に正規化したAIの1着率。odds_win・popularity は確定値なので、実際に買う時点とは多少ずれる)
"""
import argparse
from itertools import combinations

import numpy as np
import pandas as pd

from backtest_bets import _key, axis_flow, box, formation3


def structures(A, M, value):
    """組み方 -> 買い目リスト。A=AI順の馬番, M=人気順の馬番, value=AI1着率÷市場確率が最大の馬(AI上位5頭から)"""
    a1, a2 = A[0], A[1]
    return {
        "単勝 ◎": [("単勝", _key("単勝", [a1]))],
        "単勝 妙味馬": [("単勝", _key("単勝", [value]))],
        "複勝 ◎": [("複勝", _key("複勝", [a1]))],
        "複勝 妙味馬": [("複勝", _key("複勝", [value]))],
        "ワイド ◎-○": [("ワイド", _key("ワイド", [a1, a2]))],
        "ワイド ◎-AI2,3": axis_flow("ワイド", a1, A[:3], 2),
        "ワイド ◎-AI2〜4": axis_flow("ワイド", a1, A[:4], 2),
        "ワイド AI上位3頭BOX": box("ワイド", A[:3], 2),
        "ワイド ◎-1〜3人気": axis_flow("ワイド", a1, [m for m in M if m != a1][:3] + [a1], 2),
        "ワイド 妙味馬-AI上位2頭": axis_flow("ワイド", value, [x for x in A if x != value][:2] + [value], 2),
        "馬連 ◎-○": [("馬連", _key("馬連", [a1, a2]))],
        "馬連 ◎-AI2〜4": axis_flow("馬連", a1, A[:4], 2),
        "馬連 AI上位3頭BOX": box("馬連", A[:3], 2),
        "馬連 AI上位4頭BOX": box("馬連", A[:4], 2),
        "馬単 ◎→AI2〜4": [("馬単", _key("馬単", [a1, x])) for x in A[1:4]],
        "3連複 AI上位3頭BOX": box("3連複", A[:3], 3),
        "3連複 AI上位4頭BOX": box("3連複", A[:4], 3),
        "3連複 AI上位5頭BOX": box("3連複", A[:5], 3),
        "3連複 ◎軸-AI2〜5": axis_flow("3連複", a1, A[:5], 3),
        "3連複 ◎軸-AI2〜6": axis_flow("3連複", a1, A[:6], 3),
        "3連複 ◎軸-AI2〜7": axis_flow("3連複", a1, A[:7], 3),
        "3連複 ◎○2頭軸-AI3〜8": [("3連複", _key("3連複", (a1, a2, x))) for x in A[2:8]],
        "3連複 ◎-AI2,3-AI2〜6": formation3("3連複", [a1], A[1:3], A[1:6]),
        "3連複 ◎-AI2,3-AI2〜8": formation3("3連複", [a1], A[1:3], A[1:8]),
        "3連複 ◎軸-1〜5人気": axis_flow("3連複", a1, [m for m in M if m != a1][:5] + [a1], 3),
        "3連単 ◎→○→AI3": [("3連単", _key("3連単", A[:3]))],
        "3連単 ◎→AI2,3→AI2〜5": [("3連単", _key("3連単", (a1, b, c))) for b in A[1:3] for c in A[1:5] if c != b],
        "3連単 ◎→AI2〜4→AI2〜6": [("3連単", _key("3連単", (a1, b, c))) for b in A[1:4] for c in A[1:6] if c != b],
        "3連単 ◎,○→◎,○→AI3〜6": [("3連単", _key("3連単", (x, y, c))) for x, y in [(a1, a2), (a2, a1)] for c in A[2:6]],
    }


def race_table(preds: pd.DataFrame, pay: dict):
    """レース × 組み方 の点数・払戻と、レース単位の特徴(条件づくり用)"""
    recs, feats = [], []
    for rid, g in preds.groupby("race_id", sort=False):
        if len(g) < 6 or g["popularity"].isna().any() or g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
            continue
        g = g.sort_values("ai_rank")
        A = g["umaban"].astype(int).tolist()
        M = g.sort_values(["popularity", "umaban"])["umaban"].astype(int).tolist()
        inv = 1 / g["odds_win"].to_numpy(dtype=float)
        p_mkt = inv / inv.sum()
        ratio = g["ai_win"].to_numpy() / p_mkt
        value = A[int(np.argmax(ratio[:5]))]
        top = g.iloc[0]
        feats.append(dict(
            race_id=rid, race_date=top["race_date"],
            ai1_win=top["ai_win"], ai_gap=top["ai_win"] - g.iloc[1]["ai_win"],
            ai1_pop=top["popularity"], ai1_odds=top["odds_win"], ai1_ratio=ratio[0],
            ai_top3_in_pop5=sum(p <= 5 for p in g.iloc[:3]["popularity"]),
            field_size=len(g), race_class=top.get("race_class"), surface=top.get("surface"),
            fav_odds=g["odds_win"].min(),
        ))
        for name, tickets in structures(A, M, value).items():
            if not tickets:
                continue
            ret = sum(pay.get((rid, b, c), 0) for b, c in tickets)
            recs.append((rid, name, len(tickets), ret))
    R = pd.DataFrame(recs, columns=["race_id", "structure", "n", "ret"])
    Fx = pd.DataFrame(feats).set_index("race_id")
    return R, Fx


def conditions(F: pd.DataFrame) -> dict:
    """買う時点で分かる情報だけで作るレースの絞り込み条件(名前 -> 真偽のSeries)"""
    c = {"全レース": pd.Series(True, index=F.index)}
    c |= {
        "◎1着率30%以上": F.ai1_win >= 0.30, "◎1着率20〜30%": F.ai1_win.between(0.20, 0.30), "◎1着率20%未満": F.ai1_win < 0.20,
        "◎と○の差10pt以上": F.ai_gap >= 0.10, "◎と○の差5pt未満": F.ai_gap < 0.05,
        "◎=1人気": F.ai1_pop == 1, "◎=2〜3人気": F.ai1_pop.between(2, 3), "◎=4人気以下": F.ai1_pop >= 4,
        "◎単勝3倍未満": F.ai1_odds < 3, "◎単勝3〜6倍": F.ai1_odds.between(3, 6), "◎単勝6倍以上": F.ai1_odds > 6,
        "◎が過小評価(AI/市場≧1.3)": F.ai1_ratio >= 1.3, "◎が過大人気(AI/市場<0.8)": F.ai1_ratio < 0.8,
        "AI上位3頭が全員5人気以内": F.ai_top3_in_pop5 == 3, "AI上位3頭に6人気以下あり": F.ai_top3_in_pop5 < 3,
        "1番人気2倍未満": F.fav_odds < 2, "1番人気3倍以上(混戦)": F.fav_odds >= 3,
        "〜10頭": F.field_size <= 10, "11〜14頭": F.field_size.between(11, 14), "15頭〜": F.field_size >= 15,
        "芝": F.surface == "芝", "ダート": F.surface == "ダート",
        "新馬・未勝利": F.race_class <= 1, "1〜2勝クラス": F.race_class.between(5, 9), "3勝・OP": F.race_class >= 14,
    }
    return c


def evaluate(R, F, select_until):
    conds = conditions(F)
    names = list(conds)
    is_sel = pd.to_datetime(F.race_date) < select_until
    pivot_n = R.pivot(index="race_id", columns="structure", values="n").reindex(F.index).fillna(0)
    pivot_r = R.pivot(index="race_id", columns="structure", values="ret").reindex(F.index).fillna(0)
    rows = []
    combos = [(x,) for x in names] + list(combinations(names[1:], 2))
    for cond in combos:
        mask = conds[cond[0]].copy()
        for x in cond[1:]:
            mask &= conds[x]
        mask = mask.fillna(False).astype(bool)
        for period, pm in [("選定", is_sel), ("検証", ~is_sel)]:
            m = (mask & pm).to_numpy()
            if m.sum() == 0:
                continue
            n = pivot_n[m].sum()
            r = pivot_r[m].sum()
            for s in pivot_n.columns:
                if n[s] == 0:
                    continue
                rows.append(dict(cond=" & ".join(cond), structure=s, period=period, races=int(m.sum()),
                                 tickets=int(n[s]), roi=r[s] / (n[s] * 100)))
    res = pd.DataFrame(rows)
    wide = res.pivot_table(index=["cond", "structure"], columns="period", values=["roi", "races", "tickets"])
    wide.columns = [f"{x}_{y}" for x, y in wide.columns]
    return wide.reset_index()


def bootstrap_ci(R, F, cond_name, structure, period, select_until, n_boot=2000, seed=0):
    """指定の組み方×条件の回収率の95%区間(レース単位のブートストラップ)"""
    conds = conditions(F)
    mask = pd.Series(True, index=F.index)
    for x in cond_name.split(" & "):
        mask &= conds[x].fillna(False)
    is_sel = pd.to_datetime(F.race_date) < select_until
    mask &= is_sel if period == "選定" else ~is_sel
    sub = R[(R.structure == structure) & R.race_id.isin(F.index[mask])]
    if sub.empty:
        return np.nan, np.nan
    n, ret = sub.n.to_numpy(), sub.ret.to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sub), (n_boot, len(sub)))
    bs = ret[idx].sum(1) / (n[idx].sum(1) * 100)
    return np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("payouts")
    ap.add_argument("--select-until", default="2024-01-01")
    ap.add_argument("--out", default="strategy_search.csv")
    a = ap.parse_args()
    preds = pd.read_parquet(a.preds) if a.preds.endswith(".parquet") else pd.read_csv(a.preds)
    preds["race_id"] = preds["race_id"].astype(str)
    payouts = pd.read_csv(a.payouts, dtype={"race_id": str, "combo": str})
    preds = preds[preds.race_id.isin(set(payouts.race_id))]
    pay = payouts.groupby(["race_id", "bet_type", "combo"])["payout"].sum().to_dict()
    R, F = race_table(preds, pay)
    R.to_parquet(a.out.replace(".csv", "_races.parquet"))
    F.to_parquet(a.out.replace(".csv", "_feats.parquet"))
    W = evaluate(R, F, a.select_until)
    W.to_csv(a.out, index=False)
    print(f"レース数 {len(F)} / 組み方×条件 {len(W)}")


if __name__ == "__main__":
    main()
