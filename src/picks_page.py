"""
「買うべきレース」ページ(picks.html): AI買い目(本命・準)の対象レースだけを発走時刻順に並べる。
generate_html.py が予想ページを作るたびに一緒に作る(オッズ更新の10分おきにも更新され、判定も最新オッズになる)。
"""
import html as _html
from datetime import datetime, timedelta, timezone

import pandas as pd

from ai_bets import RULES, evaluate_all
from manbaken import load_params as load_manbaken, market_view, market_view_trifecta

JST = timezone(timedelta(hours=9))
MB = load_manbaken()
TRIFECTA = {}  # generate_html.py が3連複オッズ(レースごとの最新値)を入れる


def _passed(post_time, now):
    try:
        h, m = map(int, str(post_time).split(":")[:2])
    except ValueError:
        return False
    return now.replace(hour=h, minute=m, second=0, microsecond=0) < now


def build_picks(df: pd.DataFrame, date_label: str, generated_at: str) -> str:
    now = datetime.now(JST)
    items, waiting = [], []
    if "race_class" in df.columns:
        for rid, g in df.groupby("race_id"):
            g = g.sort_values("pred_win_rank")
            names = dict(zip(g["umaban"].astype(int), g["horse"]))
            f0 = g.iloc[0]
            o = pd.to_numeric(g["odds_win"], errors="coerce") if "odds_win" in g.columns else pd.Series(dtype=float)
            o = o[o > 0]
            mv = market_view_trifecta(TRIFECTA[str(rid)]["odds"]) if str(rid) in TRIFECTA else None
            if mv is None:
                mv = market_view(o.tolist(), MB) if len(o) >= 5 and len(o) >= 0.8 * len(g) else None
            for e in evaluate_all(g):
                rec = (str(f0.get("post_time", "")), f0, e, names, mv)
                if e["status"] == "対象":
                    items.append(rec)
                elif e["status"] == "オッズ待ち":
                    waiting.append(rec)
    items.sort(key=lambda x: x[0])
    waiting.sort(key=lambda x: x[0])

    def card(pt, f0, e, names, mv=None):
        rule = e["rule"]
        passed = _passed(pt, now)
        hon = e["honmei"]
        partners = "・".join(f"{p}{_html.escape(str(names.get(p, '')))}" for p in e["partners"])
        odds = f0.get("odds_win")
        combos = "".join(f'<span class="combo">{c}</span>' for c in e["tickets"])
        return f"""
        <article class="pick{' passed' if passed else ''}{' sub' if rule.key == 'sub' else ''}">
          <div class="top">
            <span class="time">{_html.escape(pt)}</span>
            <span class="race">{_html.escape(str(f0.get('track_name', '')))}{int(f0['race_number'])}R</span>
            <span class="tag">{rule.label}</span>
            {'<span class="done">発走済み</span>' if passed else ''}
          </div>
          <div class="rname">{_html.escape(str(f0.get('race_name', '') or ''))}</div>
          <div class="bet">{_html.escape(e['desc'])}</div>
          <div class="horses">◎ {hon} {_html.escape(str(names.get(hon, '')))}{f'(単勝{odds:.1f}倍)' if pd.notna(odds) else ''} ／ 相手 {partners}</div>
          <div class="combos">{combos}</div>
          <div class="why">条件: {_html.escape(e['reason'])}</div>
          {f'<div class="why">3連複の万馬券率 {mv["man"]:.0%} ・ 3連複の配当の目安 {mv["q50"]:,.0f}円({mv["q25"]:,.0f}〜{mv["q75"]:,.0f}円)</div>' if mv else ''}
        </article>"""

    body = "".join(card(*x) for x in items) or '<p class="none">今日は買うべきレースはありません。</p>'
    wait = ""
    if waiting:
        wait = '<h2>オッズ待ち(条件を満たすか未確定)</h2>' + "".join(
            f'<p class="wait">{_html.escape(pt)} {_html.escape(str(f0.get("track_name", "")))}{int(f0["race_number"])}R '
            f'{e["rule"].label}: {_html.escape(e["reason"])}</p>' for pt, f0, e, _, _ in waiting)
    notes = "".join(f"<li><b>{r.label}</b>: {_html.escape(r.name)}。{_html.escape(r.backtest)}。</li>" for r in RULES)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>買うべきレース</title>
<style>
  :root {{ --turf:#1F4D3A; --paper:#F6F1E4; --line:#E4DAC0; --ink:#2A2118; --soft:#6B5F4D; --gold:#B8862B; --sub:#5E6E7D; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font-family:"Noto Sans JP",system-ui,sans-serif; font-size:15px; line-height:1.6; }}
  header {{ background:var(--turf); color:#fff; padding:18px 16px; }}
  header h1 {{ margin:0; font-size:1.4rem; }}
  header p {{ margin:4px 0 0; font-size:.8rem; opacity:.9; }}
  header a {{ color:#F0D896; }}
  main {{ max-width:720px; margin:0 auto; padding:12px 16px 32px; }}
  .pick {{ background:#fff; border:1px solid var(--line); border-left:5px solid var(--gold); border-radius:8px; padding:12px 14px; margin:0 0 12px; }}
  .pick.sub {{ border-left-color:var(--sub); }}
  .pick.passed {{ opacity:.5; }}
  .top {{ display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }}
  .time {{ font-weight:700; font-size:1.2rem; font-variant-numeric:tabular-nums; }}
  .race {{ font-weight:700; font-size:1.1rem; color:var(--turf); }}
  .tag {{ background:var(--gold); color:#fff; font-size:.72rem; font-weight:700; padding:2px 8px; border-radius:999px; }}
  .sub .tag {{ background:var(--sub); }}
  .done {{ font-size:.72rem; color:var(--soft); }}
  .rname {{ font-size:.82rem; color:var(--soft); }}
  .bet {{ margin-top:6px; font-weight:700; }}
  .horses {{ font-size:.85rem; margin-top:2px; }}
  .combos {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }}
  .combo {{ font-variant-numeric:tabular-nums; font-weight:700; border:1px solid var(--line); background:#FBF6E8; border-radius:6px; padding:3px 9px; }}
  .why {{ font-size:.74rem; color:var(--soft); margin-top:6px; }}
  .none {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:20px; text-align:center; }}
  h2 {{ font-size:.95rem; color:var(--soft); margin:22px 0 6px; }}
  .wait {{ font-size:.82rem; margin:2px 0; color:var(--soft); }}
  footer {{ max-width:720px; margin:0 auto; padding:0 16px 24px; font-size:.72rem; color:var(--soft); }}
  footer ul {{ padding-left:18px; }}
</style></head>
<body>
<header><h1>買うべきレース</h1>
<p>{_html.escape(date_label)} ・ 更新 {_html.escape(generated_at)}(10分おきに自動更新) ・ <a href="index.html">全レースの予想</a> ・ <a href="drops.html">オッズ急落</a> ・ <a href="performance.html">成績</a></p></header>
<main>{body}{wait}</main>
<footer><ul>{notes}</ul>
<p>各100円で6点。条件(人気・オッズ)は締切直前で変わることがあるので、買う直前にもう一度このページを開いて確認してください。
検証は過去の確定オッズで行ったもので、回収率のプラスを保証するものではありません。</p></footer>
</body></html>"""
