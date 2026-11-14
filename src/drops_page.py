"""
JRA「オッズ急落」(site/drops.html): 発走が近いレースで、オッズが大きく下がった(=直前に大きく買われた)馬・組を出す。

odds_snapshot.py が記録する site/odds_history/odds_<日付>.csv(単勝・馬連・ワイドの時系列)から、
  基準 = 発走10分以上前で、一番発走に近い記録(11/14: 30分前→10分前に。直前の動きを見るため)
  今   = 一番新しい記録
  下落率 = 今 ÷ 基準 − 1
単勝: 今5倍以上・−20%以下 / 馬連: 今10〜300倍・−25%以下 / ワイド: 今(下限)5〜100倍・−25%以下 を(単勝−30%・馬連ワイド−40%以下は赤で強調)、下落の大きい順に並べる。
※ 直前に下がった馬は来やすいが、下がった分だけ回収率は下がるという話もある。記録がたまったら回収率を検証する。

使い方: python src/drops_page.py site/predictions.csv site
"""
import datetime
import html
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
BASE_MIN = 10
RULES = {"単勝": (5.0, 1e9, -0.20), "馬連": (10.0, 300.0, -0.25), "ワイド": (5.0, 100.0, -0.25)}
STRONG = {"単勝": -0.30, "馬連": -0.40, "ワイド": -0.40}   # これ以下は「大きく下落」として赤で強調(11/13に下限を広げた)
SHOW = 8


def race_drops(h, post_dt):
    """1レースの記録 h(ts, bet, combo, odds) -> (急落リスト[(券種, 組, 基準, 今, 下落率)], 基準の分, 今の分)"""
    h = h.copy()
    h["mins"] = [(post_dt - t).total_seconds() / 60 for t in h["t"]]
    base_ts = h.loc[h.mins >= BASE_MIN, "t"]
    if base_ts.empty:
        return [], None, None
    bt, ct = base_ts.max(), h["t"].max()
    if ct <= bt:
        return [], None, None
    b = h[h.t == bt].set_index(["bet", "combo"]).odds
    c = h[h.t == ct].set_index(["bet", "combo"]).odds
    out = []
    for (bet, combo), o in c.items():
        if bet not in RULES or (bet, combo) not in b.index or not o or o <= 0:
            continue
        lo, hi, th = RULES[bet]
        bo = b[(bet, combo)]
        if isinstance(bo, pd.Series):
            bo = bo.iloc[0]
        if not bo or bo <= 0 or not (lo <= o <= hi):
            continue
        d = o / bo - 1
        if d <= th:
            out.append((bet, combo, float(bo), float(o), float(d)))
    out.sort(key=lambda x: x[4])
    mins = lambda t: (post_dt - t).total_seconds() / 60  # noqa: E731
    return out, mins(bt), mins(ct)


def build(pred, hist, now):
    races = []
    for rid, g in pred.groupby("race_id"):
        g = g.sort_values("umaban")
        f0 = g.iloc[0]
        pt = str(f0.get("post_time", ""))
        if ":" not in pt:
            continue
        d = pd.to_datetime(str(f0["race_date"])[:10]).date()
        hh, mm = map(int, pt.split(":")[:2])
        post = datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST)
        h = hist[hist.race_id == str(rid)]
        drops, bmin, cmin = race_drops(h, post) if not h.empty else ([], None, None)
        names = dict(zip(g.umaban.astype(int).astype(str), g.horse))
        races.append(dict(rid=str(rid), post=post, track=str(f0.get("track_name", "")), rno=int(f0["race_number"]),
                          rname=str(f0.get("race_name", "") or ""), drops=drops, bmin=bmin, cmin=cmin, names=names))
    return sorted(races, key=lambda r: r["post"])


def render(races, now, date_label):
    def card(r, passed):
        rows = ""
        for bet, combo, bo, o, d in r["drops"][:SHOW]:
            nm = r["names"].get(combo, "") if bet == "単勝" else ""
            rows += (f'<tr class="{"strong" if d <= STRONG.get(bet, -1) else ""}"><td>{bet}</td><td><b>{html.escape(combo)}</b> {html.escape(nm)}</td><td>{bo:.1f}</td>'
                     f'<td>→ <b>{o:.1f}</b></td><td class="d">{d:.0%}</td></tr>')
        body = (f'<table><tr><th>券種</th><th>馬番</th><th>基準</th><th>今</th><th>下落</th></tr>{rows}</table>' if rows
                else '<p class="dim">急落した馬・組はありません</p>')
        left = (r["post"] - now).total_seconds() / 60
        when = "発走済み" if passed else f'発走 {r["post"]:%H:%M}(あと{max(left, 0):.0f}分)'
        basis = (f'基準=発走{r["bmin"]:.0f}分前 → 今=発走{r["cmin"]:.0f}分前の記録' if r["bmin"] is not None
                 else "比べる記録がまだありません")
        return (f'<article class="race{" passed" if passed else ""}"><div class="top"><span class="nm">{html.escape(r["track"])}'
                f'{r["rno"]}R</span><span class="when">{when}</span></div><div class="dim">{html.escape(r["rname"])} ・ {basis}</div>'
                f'{body}</article>')

    up = [r for r in races if r["post"] > now - datetime.timedelta(minutes=1)]
    soon = [r for r in up if (r["post"] - now).total_seconds() <= 30 * 60]
    later = [r for r in up if (r["post"] - now).total_seconds() > 30 * 60]
    done = [r for r in races if r["post"] <= now - datetime.timedelta(minutes=1) and r["drops"]]
    main = "".join(card(r, False) for r in soon) or '<p class="none">発走30分以内のレースはありません。</p>'
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60"><title>オッズ急落</title>
<style>
  :root {{ --turf:#1F4D3A; --paper:#F6F1E4; --line:#E4DAC0; --ink:#2A2118; --soft:#6B5F4D; --red:#B3261E; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font-family:"Noto Sans JP",system-ui,sans-serif; font-size:15px; line-height:1.6; }}
  header {{ background:var(--turf); color:#fff; padding:16px; }} header h1 {{ margin:0; font-size:1.3rem; }}
  header p {{ margin:4px 0 0; font-size:.8rem; opacity:.9; }} header a {{ color:#F0D896; }}
  main {{ max-width:720px; margin:0 auto; padding:12px 16px 32px; }}
  .race {{ background:#fff; border:1px solid var(--line); border-left:5px solid var(--red); border-radius:8px; padding:10px 12px; margin:0 0 12px; }}
  .race.passed {{ opacity:.55; }} .top {{ display:flex; justify-content:space-between; gap:8px; }}
  .nm {{ font-weight:800; font-size:1.1rem; color:var(--turf); }} .when {{ font-weight:700; color:var(--red); }}
  .dim {{ font-size:.75rem; color:var(--soft); }}
  table {{ width:100%; border-collapse:collapse; margin-top:6px; font-size:.9rem; font-variant-numeric:tabular-nums; }}
  th, td {{ border-bottom:1px solid var(--line); padding:4px 6px; text-align:left; }} td.d {{ color:var(--soft); font-weight:700; }}
  tr.strong td {{ background:#FDECEA; }} tr.strong td.d {{ color:var(--red); font-weight:800; }}
  h2 {{ font-size:1rem; margin:20px 0 8px; }} .none {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; text-align:center; }}
  details {{ margin-top:12px; }} summary {{ cursor:pointer; font-weight:700; color:var(--soft); }}
</style></head><body>
<header><h1>オッズ急落</h1>
<p>{html.escape(date_label)} ・ {now:%H:%M} 時点(5分おきに記録・1分おきに再読み込み) ・ <a href="index.html">全レースの予想</a> ・ <a href="picks.html">買うべきレース</a></p></header>
<main>
<p class="dim">発走10分前に一番近い記録から、直前に大きく下がった馬・組(単勝: 5倍以上で−20%以上、馬連: 10〜300倍・ワイド: 5〜100倍で−25%以上)。赤い行は大きな下落(単勝−30%・馬連ワイド−40%以上)。
直前に大きく買われたものです。来やすくなる一方、下がった分だけ割安さは減ります(回収率は記録がたまり次第検証)。</p>
<h2>発走30分以内</h2>{main}
{f'<details><summary>このあとのレース({len(later)})</summary>{"".join(card(r, False) for r in later)}</details>' if later else ''}
{f'<details><summary>発走済みで急落があったレース</summary>{"".join(card(r, True) for r in done[::-1])}</details>' if done else ''}
</main></body></html>"""


def main():
    pred_path, site = sys.argv[1], sys.argv[2]
    now = datetime.datetime.now(JST)
    pred = pd.read_csv(pred_path, dtype={"race_id": str})
    dates = sorted(pred["race_date"].astype(str).str[:10].unique())
    today = now.date().isoformat()
    date = today if today in dates else dates[-1]
    pred = pred[pred["race_date"].astype(str).str[:10] == date]
    p = os.path.join(site, "odds_history", f"odds_{date}.csv")
    hist = pd.read_csv(p, dtype={"race_id": str, "combo": str}) if os.path.exists(p) else \
        pd.DataFrame(columns=["ts", "race_id", "bet", "combo", "odds"])
    hist["t"] = pd.to_datetime(hist["ts"]).dt.tz_localize(JST) if len(hist) else pd.Series(dtype="datetime64[ns, Asia/Tokyo]")
    races = build(pred, hist, now)
    with open(os.path.join(site, "drops.html"), "w", encoding="utf-8") as f:
        f.write(render(races, now, date))
    print(f"[INFO] オッズ急落: {len(races)}レース中 {sum(1 for r in races if r['drops'])}レースに急落あり → {site}/drops.html")


if __name__ == "__main__":
    main()
