"""
AI買い目のルール(ダッシュボード・成績ページ・検証で共通に使う)。

【ルール】2019〜2026年の払戻で検証して選んだもの(src/strategy_search.py)
  対象レース: オープンクラス以上(重賞・リステッド・OP特別)で、AIの◎の単勝オッズが6倍以上
  買い目    : 3連複 ◎軸 - AI2〜5位への流し(6点)
  検証結果  : 555レース(年約70レース)、的中率11.4%、回収率160%(2019-2023: 148% / 2024-2026: 183%)、
              8年中7年で100%超。ただし大きな当たり数回に支えられており(上位5回を除くと84%)、
              最大32連敗・95%区間は90〜241%で、まだ「確実な勝ち」とは言えない。
  注意      : 検証は確定オッズで判定している。実際に買う時点のオッズが6倍前後の馬は、確定時に条件が変わることがある。
"""
from itertools import combinations

import pandas as pd

RULE_NAME = "3連複 ◎軸-AI2〜5位流し(OP以上・◎単勝6倍以上)"
CLASS_MIN = 20       # race_class: 新馬0 / 未勝利1 / 1勝5 / 2勝9 / 3勝14 / オープン20
ODDS_MIN = 6.0       # ◎の単勝オッズがこれ以上(より大きい)
PARTNERS = 4         # 相手 = AI2〜5位
BET_TYPE = "3連複"


def evaluate_race(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    """1レース分の予測(馬ごとの行)から、AI買い目の対象かどうかと買い目を返す。
    戻り値: {"status": "対象"|"対象外"|"オッズ待ち"|"クラス不明", "reason": str, "honmei": int|None,
            "partners": [int], "tickets": ["a-b-c", ...]}"""
    g = g.sort_values(rank_col)
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", "honmei": None, "partners": [], "tickets": []}
    cls = g["race_class"].iloc[0] if "race_class" in g.columns else None
    honmei = int(g["umaban"].iloc[0])
    partners = [int(x) for x in g["umaban"].iloc[1:1 + PARTNERS]]
    tickets = ["-".join(map(str, sorted((honmei, a, b)))) for a, b in combinations(partners, 2)]
    base = {"honmei": honmei, "partners": partners, "tickets": tickets}
    if cls is None or pd.isna(cls):
        return {"status": "クラス不明", "reason": "クラスが取れていない", **base}
    if cls < CLASS_MIN:
        return {"status": "対象外", "reason": "OP未満のレース", **base}
    odds = g["odds_win"].iloc[0] if "odds_win" in g.columns else None
    if odds is None or pd.isna(odds):
        return {"status": "オッズ待ち", "reason": f"◎の単勝が{ODDS_MIN:.0f}倍以上なら対象", **base}
    if odds <= ODDS_MIN:
        return {"status": "対象外", "reason": f"◎の単勝{odds:.1f}倍(人気すぎる)", **base}
    return {"status": "対象", "reason": f"OP以上・◎単勝{odds:.1f}倍", **base}


def settle(tickets, payouts_for_race: pd.DataFrame) -> int:
    """買い目と、そのレースの払戻(bet_type, combo, payout)から払戻合計(100円ずつ買った場合)を返す"""
    if payouts_for_race is None or payouts_for_race.empty:
        return 0
    p = payouts_for_race[payouts_for_race["bet_type"] == BET_TYPE]
    hit = p[p["combo"].astype(str).isin(tickets)]
    return int(hit["payout"].sum())
