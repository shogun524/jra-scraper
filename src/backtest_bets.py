"""
買い方(馬券の組み方)ごとの回収率を、実際の払戻で検証する。

入力:
  - 予測: race_id, race_date, umaban, ai_win, ai_rank(AIの1着率順位), popularity(確定人気) を持つ parquet/CSV
  - 払戻: fetch_payouts.py の payouts.csv(race_id, bet_type, combo, payout)
出力: 買い方 × 期間 ごとの 点数・的中率・回収率・95%信頼区間(レース単位のブートストラップ)

「A」はAIの順位、「人」は人気順。例: 「3連複 A1軸-A2〜A5流し」= AI1位を軸に、AI2〜5位へ2頭流し(6点)。
人気は確定時点のもので、実際に買う時点の人気とは多少ずれる点に注意。

使い方:
    python src/backtest_bets.py preds.parquet data/payouts.csv --split 2025-01-01 --out backtest_bets.csv
"""
import argparse
from itertools import combinations, permutations

import numpy as np
import pandas as pd


def _key(bet, horses):
    if bet in ("馬連", "ワイド", "3連複"):
        horses = sorted(horses)
    return "-".join(str(int(h)) for h in horses)


def box(bet, hs, k):
    return [(bet, _key(bet, c)) for c in combinations(hs, k)]


def axis_flow(bet, axis, others, k):
    """軸1頭 + 相手から(k-1)頭"""
    return [(bet, _key(bet, (axis,) + c)) for c in combinations([o for o in others if o != axis], k - 1)]


def formation3(bet, first, second, third):
    """3連複/3連単フォーメーション(重複・同一組は除く)"""
    out = set()
    for a in first:
        for b in second:
            for c in third:
                if len({a, b, c}) < 3:
                    continue
                out.add((bet, _key(bet, (a, b, c))))
    return sorted(out)


def strategies(g: pd.DataFrame) -> dict:
    """1レース分の出走馬(g)から、買い方ごとの買い目リストを返す"""
    A = g.sort_values("ai_rank")["umaban"].tolist()
    M = g.sort_values(["popularity", "umaban"])["umaban"].tolist()
    a1, m1 = A[0], M[0]
    agree = a1 in M[:1]            # AIの◎が1番人気
    a1_top3pop = a1 in M[:3]       # AIの◎が3番人気以内
    # 人気上位3頭のうちAI評価が最も高い馬(=人気馬の中でAIが推す馬)
    h = min(M[:3], key=lambda u: A.index(u))
    S = {
        "単勝 A1": [("単勝", _key("単勝", [a1]))],
        "単勝 1人気": [("単勝", _key("単勝", [m1]))],
        "単勝 人気上位3頭でAI最上位": [("単勝", _key("単勝", [h]))],
        "複勝 A1": [("複勝", _key("複勝", [a1]))],
        "複勝 1人気": [("複勝", _key("複勝", [m1]))],
        "複勝 人気上位3頭でAI最上位": [("複勝", _key("複勝", [h]))],
        "ワイド A1-A2": [("ワイド", _key("ワイド", A[:2]))],
        "ワイド A上位3頭BOX": box("ワイド", A[:3], 2),
        "ワイド 1人気-2人気": [("ワイド", _key("ワイド", M[:2]))],
        "ワイド 人気上位3頭BOX": box("ワイド", M[:3], 2),
        "ワイド 人気上位でAI最上位-A上位2頭": axis_flow("ワイド", h, A[:3], 2)[:2],
        "馬連 A1-A2": [("馬連", _key("馬連", A[:2]))],
        "馬連 A上位3頭BOX": box("馬連", A[:3], 2),
        "馬連 A1-A2〜A4流し": axis_flow("馬連", a1, A[:4], 2),
        "馬連 1人気-2人気": [("馬連", _key("馬連", M[:2]))],
        "馬連 人気上位3頭BOX": box("馬連", M[:3], 2),
        "3連複 A上位3頭BOX": box("3連複", A[:3], 3),
        "3連複 A上位4頭BOX": box("3連複", A[:4], 3),
        "3連複 A上位5頭BOX": box("3連複", A[:5], 3),
        "3連複 A1軸-A2〜A5流し": axis_flow("3連複", a1, A[:5], 3),
        "3連複 A1軸-A2〜A7流し": axis_flow("3連複", a1, A[:7], 3),
        "3連複 A1-A2,A3-A2〜A8": formation3("3連複", [a1], A[1:3], A[1:8]),
        "3連複 人気上位3頭BOX": box("3連複", M[:3], 3),
        "3連複 人気上位5頭BOX": box("3連複", M[:5], 3),
        "3連複 1人気軸-2〜5人気流し": axis_flow("3連複", m1, M[:5], 3),
        "3連複 人気上位でAI最上位軸-A上位5頭流し": axis_flow("3連複", h, [u for u in A if u != h][:5], 3),
        "3連単 A1→A2→A3": [("3連単", _key("3連単", A[:3]))],
        "3連単 A1→A2,A3→A2〜A5": [("3連単", _key("3連単", (a1, b, c)))
                                  for b in A[1:3] for c in A[1:5] if c != b],
        "3連単 A上位3頭BOX": [("3連単", _key("3連単", p)) for p in permutations(A[:3])],
    }
    # 条件付き: AIと市場の◎が一致したレースだけ買う
    for name in ["単勝 A1", "複勝 A1", "馬連 A1-A2〜A4流し", "3連複 A1軸-A2〜A5流し", "3連複 A上位4頭BOX"]:
        S[f"[◎=1人気のときだけ] {name}"] = S[name] if agree else []
        S[f"[◎が3人気以内のときだけ] {name}"] = S[name] if a1_top3pop else []
    return S


def run(preds: pd.DataFrame, payouts: pd.DataFrame, split: str, n_boot: int = 1000, seed: int = 0):
    pay = payouts.groupby(["race_id", "bet_type", "combo"])["payout"].sum().to_dict()
    have = set(payouts["race_id"].unique())
    preds = preds[preds["race_id"].isin(have) & preds["popularity"].notna() & preds["ai_rank"].notna()]
    rec = []
    for rid, g in preds.groupby("race_id", sort=False):
        if len(g) < 5:
            continue
        period = "検証(〜" + split + ")" if str(g["race_date"].iloc[0])[:10] < split else "本番テスト(" + split + "〜)"
        for name, tickets in strategies(g).items():
            if not tickets:
                continue
            ret = sum(pay.get((rid, b, c), 0) for b, c in tickets)
            rec.append((period, name, rid, len(tickets), ret, int(ret > 0)))
    r = pd.DataFrame(rec, columns=["period", "strategy", "race_id", "n", "ret", "hit"])
    rng = np.random.default_rng(seed)
    rows = []
    for (period, name), d in r.groupby(["period", "strategy"]):
        n, ret = d["n"].to_numpy(), d["ret"].to_numpy()
        roi = ret.sum() / (n.sum() * 100)
        idx = rng.integers(0, len(d), (n_boot, len(d)))
        bs = ret[idx].sum(1) / (n[idx].sum(1) * 100)
        rows.append(dict(period=period, strategy=name, races=len(d), tickets=int(n.sum()),
                         pts_per_race=round(n.mean(), 1), hit_rate=round(d["hit"].mean(), 3),
                         roi=round(roi * 100, 1), ci_low=round(np.percentile(bs, 2.5) * 100, 1),
                         ci_high=round(np.percentile(bs, 97.5) * 100, 1)))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("payouts")
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--out", default="backtest_bets.csv")
    a = ap.parse_args()
    preds = pd.read_parquet(a.preds) if a.preds.endswith(".parquet") else pd.read_csv(a.preds)
    preds["race_id"] = preds["race_id"].astype(str)
    payouts = pd.read_csv(a.payouts, dtype={"race_id": str, "combo": str})
    res = run(preds, payouts, a.split)
    res.to_csv(a.out, index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    wide = res.pivot_table(index="strategy", columns="period", values=["roi", "ci_low", "ci_high", "hit_rate"], aggfunc="first")
    print(wide.round(1).to_string())


if __name__ == "__main__":
    main()
