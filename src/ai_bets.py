"""
AI買い目のルール(ダッシュボード・成績ページ・検証で共通に使う)。

2019〜2026年の払戻(約2.6万レース)と、ウォークフォワード予測(各年をその年より前のデータだけで学習)で
「組み方 × 買うレースの条件」を総当たりして選んだ(src/strategy_search.py)。判定は確定オッズで検証している。

【本命】OP以上のレースで、AIの◎の単勝が6倍以上 → 3連複 ◎軸 - AI2〜5位流し(6点)
  555レース(年約70)、的中率11%、回収率160%(2019-23: 148% / 2024-26: 183%)、8年中7年で100%超。
  大きな当たり数回に支えられている(上位5回を除くと84%)。最大32連敗。

【準】◎が2〜3番人気で、AIの上位3頭に6番人気以下がいる → 3連単 ◎1着 → AI2,3位 → AI2〜5位(6点)
  約2,050レース(年約260)、回収率120%(2019-23: 119% / 2024-26: 121%)、8年中5年で100%超(2026年は58%)。
  本命より不安定で、上位5回の当たりを除くと79%。実績を成績ページで別に採点して見極める。

注意: 実際に買う時点のオッズ・人気は確定時と違うことがある。条件の境目にいる馬は直前に確認すること。

11/14〜: この2つのルールは旧AI(data/model_win_v1.txt、オッズを使わない旧特徴量のモデル)の順位でしか回収率が出ていない
(2023〜2026年: 新AIだと本命166%・準39%)。そこで予想に旧AIの順位 rule_rank を残し、ルールはそちらで判定する。
画面の◎(新AI)と違う馬が軸になることがあるので、そのときは「旧AI◎」と書く。新AI用のルールは探し直し中。
"""
from dataclasses import dataclass
from itertools import combinations

import pandas as pd


@dataclass(frozen=True)
class Rule:
    key: str          # "main" / "sub"
    label: str        # 画面に出す名前
    name: str         # ルールの説明
    bet_type: str     # 払戻の券種名
    backtest: str     # 検証結果の要約(画面の注記に使う)


MAIN = Rule("main", "AI買い目", "OP以上・◎単勝6倍以上 → 3連複 ◎軸-AI2〜5位流し(6点)", "3連複",
            "2019〜2026年の検証で回収率160%(年約70レース・的中率11%・最大32連敗)")
SUB = Rule("sub", "準AI買い目", "◎が2〜3人気・AI上位3頭に6人気以下あり → 3連単 ◎→AI2,3位→AI2〜5位(6点)", "3連単",
           "2019〜2026年の検証で回収率120%(年約260レース)。本命より不安定(8年中5年で100%超)")
RULES = [MAIN, SUB]
RULE_NAME = MAIN.name  # 互換用

CLASS_MIN = 20       # race_class: 新馬0 / 未勝利1 / 1勝5 / 2勝9 / 3勝14 / オープン20
ODDS_MIN = 6.0


def _base(g, rank_col):
    # 旧AIの順位(rule_rank)があればそれで判定する(ルールは旧AIで検証したため)
    if rank_col == "pred_win_rank" and "rule_rank" in g.columns and g["rule_rank"].notna().all():
        rank_col = "rule_rank"
    g = g.sort_values(rank_col)
    A = [int(x) for x in g["umaban"]]
    return g, A


def _mk(g):
    """軸の馬の呼び方。画面の◎(新AI)と同じ馬なら「◎」、違えば「旧AI◎」"""
    if "pred_win_rank" in g.columns and "rule_rank" in g.columns and g["rule_rank"].notna().all():
        top = g.loc[g["pred_win_rank"].astype(float).idxmin(), "umaban"]
        return "◎" if int(top) == int(g["umaban"].iloc[0]) else "旧AI◎"
    return "◎"


def _popularity(g):
    """人気順位。popularity列があればそれ、無ければ単勝オッズの順位"""
    if "popularity" in g.columns and g["popularity"].notna().all():
        return g["popularity"].astype(float)
    if "odds_win" in g.columns and g["odds_win"].notna().all():
        return g["odds_win"].rank(method="min")
    return None


def evaluate_main(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g, A = _base(g, rank_col)
    honmei, partners = A[0], A[1:5]
    tickets = ["-".join(map(str, sorted((honmei, a, b)))) for a, b in combinations(partners, 2)]
    base = {"rule": MAIN, "honmei": honmei, "partners": partners, "tickets": tickets,
            "desc": f"3連複 {_mk(g)}{honmei}軸 → {'・'.join(map(str, partners))}(6点)"}
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", **base}
    cls = g["race_class"].iloc[0] if "race_class" in g.columns else None
    if cls is None or pd.isna(cls):
        return {"status": "クラス不明", "reason": "クラスが取れていない", **base}
    if cls < CLASS_MIN:
        return {"status": "対象外", "reason": "OP未満のレース", **base}
    odds = g["odds_win"].iloc[0] if "odds_win" in g.columns else None
    if odds is None or pd.isna(odds):
        return {"status": "オッズ待ち", "reason": f"◎の単勝が{ODDS_MIN:.0f}倍超なら対象", **base}
    if odds <= ODDS_MIN:
        return {"status": "対象外", "reason": f"◎の単勝{odds:.1f}倍(人気すぎる)", **base}
    return {"status": "対象", "reason": f"OP以上・◎単勝{odds:.1f}倍", **base}


def evaluate_sub(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g, A = _base(g, rank_col)
    a1 = A[0]
    tickets = [f"{a1}-{b}-{c}" for b in A[1:3] for c in A[1:5] if c != b]
    base = {"rule": SUB, "honmei": a1, "partners": A[1:5], "tickets": tickets,
            "desc": f"3連単 {_mk(g)}{a1} → {'・'.join(map(str, A[1:3]))} → {'・'.join(map(str, A[1:5]))}(6点)"}
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", **base}
    pop = _popularity(g)
    if pop is None:
        return {"status": "オッズ待ち", "reason": "人気が出たら判定", **base}
    p1 = pop.iloc[0]
    low_in_top3 = (pop.iloc[:3] >= 6).any()
    if not (2 <= p1 <= 3):
        return {"status": "対象外", "reason": f"◎が{int(p1)}番人気", **base}
    if not low_in_top3:
        return {"status": "対象外", "reason": "AI上位3頭がすべて5番人気以内", **base}
    return {"status": "対象", "reason": f"◎{int(p1)}番人気・AI上位に人気薄あり", **base}


def evaluate_race(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    """互換用: 本命ルールの判定"""
    return evaluate_main(g, rank_col)


def evaluate_all(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> list[dict]:
    return [evaluate_main(g, rank_col), evaluate_sub(g, rank_col)]


def settle(tickets, payouts_for_race: pd.DataFrame, bet_type: str = MAIN.bet_type) -> int:
    """買い目と、そのレースの払戻(bet_type, combo, payout)から払戻合計(100円ずつ買った場合)を返す"""
    if payouts_for_race is None or payouts_for_race.empty:
        return 0
    p = payouts_for_race[payouts_for_race["bet_type"] == bet_type]
    hit = p[p["combo"].astype(str).isin(tickets)]
    return int(hit["payout"].sum())
