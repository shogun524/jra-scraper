"""
的中率重視の買い目(回収率は見ない)。

2023年〜2026年10月の 12,710レースで、人気順の買い方ごとの的中率を数えた(data/hitrate.json)。
的中率を一番上げるのは「オッズの人気順」そのもので、AIを混ぜると下がる
(1頭目の3着内率: 人気順 65.5%、AI 58.5%、半々 64.6%。ワイド・3連複も同じ)。
レースの「1番人気の単勝オッズ」と「頭数」で的中率が大きく変わるので、その表から今のオッズで見込みを出す。
  複勝 1番人気(1点)                     全体65.5% / 1.5倍以下・8頭以下なら94%
  ワイド 1番人気→2〜5番人気(4点)        全体60.1% / 同 94%
  3連複 1番人気軸→2〜6番人気(10点)      全体39.2%
"""
import json
import os

import pandas as pd

_T = None


def table():
    global _T
    if _T is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "hitrate.json"), encoding="utf-8") as f:
                _T = json.load(f)
        except (OSError, ValueError):
            _T = {}
    return _T


def _bands(fav, n):
    fb = "0-1.5" if fav <= 1.5 else "1.6-2.0" if fav <= 2.0 else "2.1-2.5" if fav <= 2.5 else "2.6-3.5" if fav <= 3.5 else "3.6-"
    nb = "-8" if n <= 8 else "9-11" if n <= 11 else "12-14" if n <= 14 else "15-18"
    return fb, nb


def plan(g: pd.DataFrame):
    """1レース分の出走表(odds_win・umaban) -> dict(fav=1番人気の馬番, items=[(買い方, 組, 見込み的中率)]) / オッズが無ければ None"""
    t = table()
    o = g.dropna(subset=["odds_win"])
    o = o[o["odds_win"] > 0].sort_values("odds_win")
    if len(o) < 5 or not t:
        return None
    um = [int(x) for x in o["umaban"]]
    fb, nb = _bands(float(o["odds_win"].iloc[0]), len(g))
    f = um[0]
    items = [("複勝", f"{f}", t["fukusho"][fb][nb]),
             ("ワイド", f"{f} → {'・'.join(map(str, um[1:5]))}(4点)", t["wide4"][fb][nb]),
             ("3連複", f"{f}軸 → {'・'.join(map(str, um[1:6]))}(10点)", t["trio10"][fb][nb])]
    return dict(fav=f, fav_odds=float(o["odds_win"].iloc[0]), items=items)
