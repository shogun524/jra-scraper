"""
◎(新AI)と旧◎(旧AI)に付ける一言(狙い目!/普通/注意/危険!)。

2023〜2026年(1万2,8百レース、その年より前のデータだけで学習したAI)で、
人気(単勝の順位) × 新AIと旧AIの◎が同じ馬か で、実際の勝率がオッズの見込みの何倍だったか:
  旧◎ が4番人気以下で、新◎と違う馬      1.18〜1.32倍(単勝回収率 96〜100%)  → 狙い目!
  新◎ が6番人気以下で、旧◎と違う馬      1.90倍(181レースと少なめ・単回158%) → 狙い目!
  ◎・旧◎ が1〜3番人気                    0.91〜1.08倍(オッズどおり)        → 普通(普通に買ってよい)
  新◎ が4〜5番人気で、旧◎と違う馬      0.89倍                            → 注意
  ◎と旧◎が同じ馬で、4番人気以下         0.61倍(単回 約50%)                → 危険!
"""
import pandas as pd

TAGS = {
    "狙い目": ("狙い目！", "tg-go"),
    "普通": ("普通", "tg-ok"),
    "注意": ("注意", "tg-warn"),
    "危険": ("危険！", "tg-ng"),
}
EXPLAIN = ("◎・旧◎の一言(2023〜26年の検証で、実際の勝率がオッズの見込みの何倍か): "
           "狙い目！=旧◎が4番人気以下で◎と違う(1.2〜1.3倍)・◎が6番人気以下で旧◎と違う(1.9倍) / "
           "普通=1〜3番人気(ほぼオッズどおり。普通に買ってよい) / 注意=◎が4〜5番人気で旧◎と違う(0.9倍) / "
           "危険！=◎と旧◎が同じ人気薄(4番人気以下、0.6倍)")


def race_tags(g: pd.DataFrame) -> dict:
    """1レース分 -> {馬番: (表示, cssクラス, 対象('◎'/'旧◎'/'◎旧◎'))}"""
    if "odds_win" not in g.columns or pd.to_numeric(g["odds_win"], errors="coerce").isna().all():
        return {}
    pop = pd.to_numeric(g["odds_win"], errors="coerce").rank(method="min")
    pop = dict(zip(g["umaban"].astype(int), pop))
    new1 = g.loc[g["pred_win_rank"] == 1, "umaban"]
    old1 = g.loc[g["rule_rank"] == 1, "umaban"] if "rule_rank" in g.columns else pd.Series(dtype=int)
    if new1.empty:
        return {}
    n = int(new1.iloc[0])
    o = int(old1.iloc[0]) if not old1.empty else None
    out = {}
    if o == n:
        p = pop.get(n, 99)
        out[n] = (*TAGS["危険" if p >= 4 else "普通"], "◎旧◎")
        return out
    pn = pop.get(n, 99)
    out[n] = (*TAGS["普通" if pn <= 3 else ("注意" if pn <= 5 else "狙い目")], "◎")
    if o is not None:
        po = pop.get(o, 99)
        out[o] = (*TAGS["普通" if po <= 3 else "狙い目"], "旧◎")
    return out
