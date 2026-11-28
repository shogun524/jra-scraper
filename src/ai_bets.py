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
import re
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
# クラス別(11/15追加。新AI=今の◎で判定。strategy_search をクラスごとに回して、選定2019-23・検証2024-26 の両方で100%超、
#            上位5本を除いても100%超、8年中6年以上で100%超 のものだけ)
C1 = Rule("c1", "1勝クラス 単勝", "1勝クラス・◎単勝6倍以上 → 妙味馬(AI上位5頭でオッズより一番高く見ている馬)の単勝(1点)", "単勝",
          "2019〜2026年の検証で回収率133%(年約76レース・的中率10%・上位5本を除いても104%・8年中6年で100%超)")
GR = Rule("gr", "重賞 ワイド", "重賞(G1〜G3)・◎の1着率30%以上 → ワイド AI上位3頭BOX(3点)", "ワイド",
          "2019〜2026年の検証で回収率139%・的中率60%(年約16レース・上位5本を除いても101%・8年中7年で100%超)")
OP = Rule("op", "OP 単勝", "OP・L(重賞以外)・新AIと旧AIの◎が違う → 旧AIの妙味馬の単勝(1点)", "単勝",
          "2019〜2026年の検証で回収率141%(年約69レース・的中率8%・上位5本を除いても102%・8年中7年で100%超)")
# T3(11/20・試験): ボートと同じ考え方(合成オッズ3倍未満は見送り)で3連単フォーメーションを総当たり(2023〜2026年・ウォークフォワード予測)。
#   選定2023-24 → 確認2025-26 の両方で100%超が38通り(偶然版は6〜11通り)。その中で繰り返し出た形。
#   3連単の過去オッズが無いので、合成オッズは単勝オッズから掛け算で推定(控除後72.5%)して判定している。
T3 = Rule("t3", "3連単フォーメーション(試験)", "1勝クラス・1番人気の単勝支持30〜40% → 3連単 人気上位4頭 → AI上位3頭 → AI3着内率上位4頭(推定合成3倍以上)", "3連単",
          "2023〜24年 回収率132%(311レース)・2025〜26年 113%(284レース)、的中率16〜17%。試験中")
RULES = [MAIN, SUB, C1, GR, OP, T3]
SHOWN_RULES = [r for r in RULES if r.key not in ("sub",)]
T3_COMP = 3.0
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


def _new_ai(g):
    """クラス別ルールは新AI(画面の◎ = pred_win_rank)で判定する"""
    g = g.sort_values("pred_win_rank")
    return g, [int(x) for x in g["umaban"]]


def _value_horse(g):
    """AI上位5頭のうち、AIの1着率 ÷ オッズから見た確率 が一番大きい馬(妙味馬)"""
    if "odds_win" not in g.columns or g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
        return None
    inv = 1 / g["odds_win"].astype(float)
    ratio = g["pred_win_norm"].astype(float) / (inv / inv.sum())
    top5 = g.head(5)
    return int(top5.loc[ratio.loc[top5.index].idxmax(), "umaban"])


def evaluate_c1(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g, A = _new_ai(g)
    v = _value_horse(g)
    base = {"rule": C1, "honmei": A[0], "partners": [], "tickets": [str(v)] if v else [],
            "desc": f"単勝 {v}番(妙味馬)" if v else "単勝 妙味馬(オッズが出たら決まる)"}
    cls = g["race_class"].iloc[0] if "race_class" in g.columns else None
    if cls is None or pd.isna(cls) or int(cls) != 5:
        return {"status": "対象外", "reason": "1勝クラスではない", **base}
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", **base}
    odds = g["odds_win"].iloc[0] if "odds_win" in g.columns else None
    if odds is None or pd.isna(odds) or v is None:
        return {"status": "オッズ待ち", "reason": "◎の単勝が6倍超なら対象", **base}
    if odds <= ODDS_MIN:
        return {"status": "対象外", "reason": f"◎の単勝{odds:.1f}倍", **base}
    return {"status": "対象", "reason": f"1勝クラス・◎単勝{odds:.1f}倍", **base}


def _is_graded(g):
    import re
    nm = str(g["race_name"].iloc[0]) if "race_name" in g.columns else ""
    return bool(re.search(r"\(G[123]\)|\(GI{1,3}\)", nm)) and not re.search(r"J\.?G", nm)


def evaluate_gr(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g, A = _new_ai(g)
    top3 = A[:3]
    tickets = ["-".join(map(str, sorted(x))) for x in combinations(top3, 2)]
    base = {"rule": GR, "honmei": A[0], "partners": top3[1:], "tickets": tickets,
            "desc": f"ワイド {'・'.join(map(str, top3))} BOX(3点)"}
    if not _is_graded(g):
        return {"status": "対象外", "reason": "重賞ではない", **base}
    p1 = float(g["pred_win_norm"].iloc[0])
    if p1 < 0.30:
        return {"status": "対象外", "reason": f"◎の1着率{p1:.0%}(30%未満)", **base}
    return {"status": "対象", "reason": f"重賞・◎の1着率{p1:.0%}", **base}


def evaluate_op(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g = g.sort_values("pred_win_rank")
    base = {"rule": OP, "honmei": int(g["umaban"].iloc[0]), "partners": [], "tickets": [], "desc": "単勝 旧AIの妙味馬"}
    cls = g["race_class"].iloc[0] if "race_class" in g.columns else None
    if cls is None or pd.isna(cls) or int(cls) < 20 or _is_graded(g) or re.search(r"J\.?G", str(g["race_name"].iloc[0])):
        return {"status": "対象外", "reason": "OP・L(重賞以外)ではない", **base}
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", **base}
    if "rule_score" not in g.columns or g["rule_score"].isna().any() or "rule_rank" not in g.columns:
        return {"status": "判定待ち", "reason": "旧AIの評価がまだ無い", **base}
    o = g.sort_values("rule_rank")
    if int(o["umaban"].iloc[0]) == int(g["umaban"].iloc[0]):
        return {"status": "対象外", "reason": "新旧AIの◎が同じ", **base}
    if "odds_win" not in g.columns or g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
        return {"status": "オッズ待ち", "reason": "新旧AIの◎が違う(オッズが出たら妙味馬が決まる)", **base}
    inv = 1 / g["odds_win"].astype(float)
    pm = inv / inv.sum()
    sc = g["rule_score"].astype(float)
    ratio = (sc / sc.sum()) / pm
    top5 = o.head(5)
    v = int(top5.loc[ratio.loc[top5.index].idxmax(), "umaban"])
    base.update(tickets=[str(v)], desc=f"単勝 {v}番(旧AIの妙味馬)")
    return {"status": "対象", "reason": f"OP・新AI◎{int(g['umaban'].iloc[0])}・旧AI◎{int(o['umaban'].iloc[0])}", **base}


def _harville_sum(q, tickets):
    """単勝の支持率 q(馬番→確率) から、買い目の3連単が来る確率の合計(掛け算で推定)"""
    t = 0.0
    for tk in tickets:
        a, b, c = (int(x) for x in tk.split("-"))
        pa, pb, pc = q.get(a, 0), q.get(b, 0), q.get(c, 0)
        if 1 - pa <= 0 or 1 - pa - pb <= 0:
            continue
        t += pa * pb / (1 - pa) * pc / (1 - pa - pb)
    return t


def evaluate_t3(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    g = g.copy()
    base = {"rule": T3, "honmei": int(g.sort_values("pred_win_rank")["umaban"].iloc[0]), "partners": [], "tickets": [],
            "desc": "3連単 人気上位4頭 → AI上位3頭 → AI3着内率上位4頭"}
    cls = g["race_class"].iloc[0] if "race_class" in g.columns else None
    if cls is None or pd.isna(cls) or not (3 <= float(cls) < 6):
        return {"status": "対象外", "reason": "1勝クラスではない", **base}
    if len(g) < 6:
        return {"status": "対象外", "reason": "頭数が少ない", **base}
    if "odds_win" not in g.columns or g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
        return {"status": "オッズ待ち", "reason": "単勝オッズが出たら判定", **base}
    inv = 1 / g["odds_win"].astype(float)
    q = dict(zip(g["umaban"].astype(int), inv / inv.sum()))
    q1 = max(q.values())
    pop = [u for u, _ in sorted(q.items(), key=lambda x: -x[1])][:4]
    ai = [int(x) for x in g.sort_values("pred_win_rank")["umaban"]][:3]
    t3col = "pred_top3_rank" if "pred_top3_rank" in g.columns else "pred_win_rank"
    a3 = [int(x) for x in g.sort_values(t3col)["umaban"]][:4]
    tickets = [f"{a}-{b}-{c}" for a in pop for b in ai for c in a3 if len({a, b, c}) == 3]
    ph = _harville_sum(q, tickets)
    comp = 0.725 / ph if ph > 0 else 0
    base.update(tickets=tickets, partners=ai,
                desc=f"3連単 {'・'.join(map(str, pop))} → {'・'.join(map(str, ai))} → {'・'.join(map(str, a3))}({len(tickets)}点・推定合成{comp:.1f}倍)")
    if not (0.30 <= q1 < 0.40):
        return {"status": "対象外", "reason": f"1番人気の単勝支持{q1:.0%}(30〜40%で対象)", **base}
    if comp < T3_COMP:
        return {"status": "対象外", "reason": f"推定合成{comp:.1f}倍(3倍未満は見送り)", **base}
    return {"status": "対象", "reason": f"1勝クラス・1番人気{q1:.0%}・推定合成{comp:.1f}倍", **base}


def evaluate_race(g: pd.DataFrame, rank_col: str = "pred_win_rank") -> dict:
    """互換用: 本命ルールの判定"""
    return evaluate_main(g, rank_col)


# 11/28: 準AI買い目はサイトの買い目に出さない(検証の作り方が甘かった: 2019〜2026年の全期間で総当たりして選んだので
# 選んだ期間と確かめた期間が同じ。2026年は58%・上位5本を除くと79%・掲載後20レース0的中)。成績ページの採点だけ続ける。
HIDDEN = {"sub"}


def evaluate_all(g: pd.DataFrame, rank_col: str = "pred_win_rank", include_hidden: bool = False) -> list[dict]:
    out = [evaluate_main(g, rank_col)] + ([evaluate_sub(g, rank_col)] if include_hidden else [])
    if "pred_win_norm" in g.columns:
        out += [evaluate_c1(g, rank_col), evaluate_gr(g, rank_col), evaluate_op(g, rank_col), evaluate_t3(g, rank_col)]
    return out


def settle(tickets, payouts_for_race: pd.DataFrame, bet_type: str = MAIN.bet_type) -> int:
    """買い目と、そのレースの払戻(bet_type, combo, payout)から払戻合計(100円ずつ買った場合)を返す"""
    if payouts_for_race is None or payouts_for_race.empty:
        return 0
    p = payouts_for_race[payouts_for_race["bet_type"] == bet_type]
    hit = p[p["combo"].astype(str).isin(tickets)]
    return int(hit["payout"].sum())
