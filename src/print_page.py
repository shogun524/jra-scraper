"""
JRA「印刷用」(print.html): その日の全レースの予想を、A4の紙に印刷・PDF保存できる形にまとめる。
generate_html.py が予想ページを作るたびに一緒に作る(オッズ更新のたびに最新になる)。

画面で 競馬場・絞り込み(全レース/AI買い目のレース/荒れそうなレース)・レイアウト(1ページ1レース/2レース)・
発走前だけ を選び、「印刷 / PDFに保存」でブラウザの印刷画面を開く。操作パネルは印刷に出ない。
各レース: 発走・レース名・3連複の万馬券率と配当の目安・AI買い目(対象のとき)・出走表(印・枠・馬番・馬名・騎手・
人気・単勝・馬体重・AI1着率・AI3着内率・妙味)と、手書き用の「メモ」欄。
"""
import html as _html
import math

import pandas as pd

from ai_bets import evaluate_all
from manbaken import load_params as load_manbaken, market_view, market_view_trifecta

WAKU_COLORS = {1: ("#fff", "#222"), 2: ("#222", "#fff"), 3: ("#d33", "#fff"), 4: ("#2a6ad1", "#fff"),
               5: ("#e8c31a", "#222"), 6: ("#2c9a4b", "#fff"), 7: ("#f08a24", "#fff"), 8: ("#f19ec2", "#222")}


def _f(v, fmt="{:.1f}", empty=""):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return empty
    return empty if math.isnan(x) else fmt.format(x)


def _manbaken(g, trio, prm):
    rid = str(g["race_id"].iloc[0])
    v = market_view_trifecta(trio[rid]["odds"]) if rid in (trio or {}) else None
    if v is None and "odds_win" in g.columns:
        o = pd.to_numeric(g["odds_win"], errors="coerce")
        o = o[o > 0]
        if len(o) >= 5 and len(o) >= 0.8 * len(g):
            v = market_view(o.tolist(), prm)
    return v


def _card(rid, g, trio, prm):
    g = g.sort_values("umaban")
    f0 = g.iloc[0]
    post = str(f0.get("post_time", "") or "")
    mv = _manbaken(g, trio, prm)
    man_txt = (f'3連複の万馬券率 <b>{mv["man"]:.0%}</b>　配当の目安 <b>{mv["q50"]:,.0f}円</b>'
               f'(半分は {mv["q25"]:,.0f}〜{mv["q75"]:,.0f}円)' if mv else "万馬券率: オッズ待ち")
    names = dict(zip(g["umaban"].astype(int), g["horse"]))
    bets = []
    try:
        for e in evaluate_all(g.sort_values("pred_win_rank")):
            if e["status"] == "対象":
                bets.append(f'<b>[{_html.escape(e["rule"].label)}]</b> {_html.escape(e["desc"])}：'
                            + " ".join(f'<span class="cb">{c}</span>' for c in e["tickets"]))
    except Exception:  # noqa: BLE001
        pass
    bet_html = ('<div class="pp-bet">' + "<br>".join(bets) + "</div>") if bets else ""
    flags = ["bet"] if bets else []
    if mv and mv["man"] >= 0.5:
        flags.append("are")
    rows = ""
    for _, r in g.iterrows():
        w = int(r["waku"]) if pd.notna(r.get("waku")) else 0
        bg, fg = WAKU_COLORS.get(w, ("#eee", "#222"))
        hw = r.get("horse_weight")
        wd = r.get("weight_diff")
        hw_txt = (f'{_f(hw, "{:.0f}")}' + (f'({_f(wd, "{:+.0f}")})' if _f(wd, "{:+.0f}") else "")) if _f(hw, "{:.0f}") else ""
        top = int(r.get("pred_win_rank", 99)) == 1
        mk = r.get("mark")
        mk = "" if mk is None or (isinstance(mk, float) and math.isnan(mk)) else str(mk)
        vt = r.get("value_tag")
        vt = "" if vt is None or (isinstance(vt, float) and math.isnan(vt)) else str(vt)
        rows += (f'<tr class="{"top" if top else ""}"><td class="mk">{_html.escape(mk)}</td>'
                 f'<td><span class="wk" style="background:{bg};color:{fg};">{w or ""}</span></td>'
                 f'<td class="c">{int(r["umaban"])}</td><td class="nm">{_html.escape(str(r["horse"]))}</td>'
                 f'<td class="nw">{_html.escape(str(r.get("jockey") or ""))}</td>'
                 f'<td class="c">{_f(r.get("market_rank"), "{:.0f}")}</td><td class="r">{_f(r.get("odds_win"))}</td>'
                 f'<td class="r sm">{hw_txt}</td>'
                 f'<td class="r b">{_f(float(r.get("pred_win_norm", 0) or 0) * 100)}</td>'
                 f'<td class="r">{_f(float(r.get("pred_top3_norm", 0) or 0) * 100, "{:.0f}")}</td>'
                 f'<td class="c">{int(r.get("pred_win_rank", 0) or 0)}</td>'
                 f'<td class="sm nw">{_html.escape(vt)}</td><td class="memo"></td></tr>')
    venue = str(f0.get("track_name", ""))
    return f"""
    <div class="pp-card" data-venue="{_html.escape(venue)}" data-flags="{' '.join(flags)}" data-post="{_html.escape(post)}">
      <div class="pp-head"><b>{_html.escape(venue)} {int(f0['race_number'])}R</b>
        <span>{_html.escape(str(f0.get('race_name', '') or ''))}</span><span class="pp-post">発走 {_html.escape(post)}</span></div>
      <div class="pp-sub">{man_txt}</div>
      {bet_html}
      <table class="pp-t"><tr><th>印</th><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>人気</th><th>単勝</th><th>馬体重</th>
      <th>AI1着%</th><th>AI3着内%</th><th>AI順</th><th>妙味</th><th class="memo">メモ</th></tr>{rows}</table>
    </div>"""


def build_print(df: pd.DataFrame, date_label: str, generated_at: str, trio: dict | None = None) -> str:
    prm = load_manbaken()
    sections, chips = "", ""
    df = df.copy()
    df["race_number"] = pd.to_numeric(df["race_number"], errors="coerce")
    for venue, gv in df.groupby("track_name", sort=False):
        cards = "".join(_card(rid, g, trio, prm) for rid, g in sorted(gv.groupby("race_id"), key=lambda x: x[1]["race_number"].iloc[0]))
        chips += f'<label><input type="checkbox" class="pp-v" value="{_html.escape(str(venue))}" checked> {_html.escape(str(venue))}</label>'
        sections += f'<section class="pp-venue" data-venue="{_html.escape(str(venue))}"><h2>{_html.escape(str(venue))}　{_html.escape(date_label)}</h2><div class="pp-grid">{cards}</div></section>'
    n = df["race_id"].nunique()
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>印刷用 予想一覧</title>
<style>
  * {{ box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  .nw {{ white-space:nowrap; }}
  body {{ margin:0; background:#F6F1E4; color:#2A2118; font-family:"Noto Sans JP",system-ui,sans-serif; font-size:13px; }}
  header {{ background:#1F4D3A; color:#fff; padding:14px 16px; }} header h1 {{ margin:0; font-size:1.2rem; }}
  header p {{ margin:4px 0 0; font-size:.8rem; opacity:.9; }} header a {{ color:#F0D896; }}
  main {{ max-width:1000px; margin:0 auto; padding:12px; }}
  .pp-controls {{ background:#fff; border:1px solid #E4DAC0; border-radius:8px; padding:10px 12px; margin-bottom:12px; }}
  .pp-controls h3 {{ margin:0 0 6px; font-size:1rem; }} .pp-row {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:6px 0; }}
  .pp-vchips label {{ margin-right:10px; white-space:nowrap; }}
  .pp-print {{ background:#1F4D3A; color:#fff; border:0; border-radius:6px; padding:8px 16px; font-size:14px; font-weight:700; }}
  .pp-hint {{ font-size:11px; color:#6B5F4D; margin:4px 0 0; }}
  .pp-venue h2 {{ font-size:1rem; margin:14px 0 6px; }}
  .pp-card {{ background:#fff; border:1px solid #bbb; padding:8px 10px; margin:0 0 10px; break-inside:avoid; }}
  .pp-head {{ display:flex; gap:10px; align-items:baseline; border-bottom:2px solid #222; padding-bottom:3px; }}
  .pp-head b {{ font-size:1.15rem; }} .pp-post {{ margin-left:auto; font-weight:700; }}
  .pp-sub {{ margin:4px 0; }} .pp-bet {{ margin:4px 0; padding:4px 6px; border:1px solid #B8862B; background:#FBF6E8; }}
  .cb {{ display:inline-block; border:1px solid #999; border-radius:3px; padding:0 4px; margin:1px 2px; font-variant-numeric:tabular-nums; }}
  .pp-t {{ width:100%; border-collapse:collapse; margin-top:4px; font-variant-numeric:tabular-nums; }}
  .pp-t th, .pp-t td {{ border:1px solid #aaa; padding:2px 4px; }} .pp-t th {{ background:#eee; font-size:11px; }}
  .pp-t tr.top td {{ background:#FFF4DE; }} .c {{ text-align:center; }} .r {{ text-align:right; }} .b {{ font-weight:700; }}
  .sm {{ font-size:11px; }} .nm {{ font-weight:700; white-space:nowrap; }} .mk {{ text-align:center; font-weight:700; }}
  .wk {{ display:inline-block; width:18px; text-align:center; border:1px solid #888; border-radius:3px; font-weight:700; }}
  .memo {{ width:18%; }}
  .pp-grid.two {{ display:grid; grid-template-columns:1fr; }}
  @media print {{
    @page {{ size:A4; margin:10mm; }}
    body {{ background:#fff; font-size:11px; }} header, .pp-controls {{ display:none; }} main {{ padding:0; max-width:none; }}
    .pp-venue {{ break-before:page; }} .pp-venue:first-of-type {{ break-before:auto; }}
    .pp-grid:not(.two) .pp-card {{ break-after:page; }}
    .pp-card {{ border:1px solid #888; }}
  }}
</style></head><body>
<header><h1>印刷用 予想一覧</h1>
<p>{_html.escape(date_label)} ・ 更新 {_html.escape(generated_at)} ・ <a href="index.html">全レースの予想</a> ・ <a href="picks.html">買うべきレース</a></p></header>
<main>
  <div class="pp-controls">
    <h3>本日の予想一覧({n}レース)</h3>
    <div class="pp-row pp-vchips"><span>競馬場:</span>{chips}</div>
    <div class="pp-row">
      <label>絞り込み <select id="ppFlag"><option value="all">全レース</option><option value="bet">AI買い目のレースだけ</option>
        <option value="are">荒れそう(3連複の万馬券率50%以上)</option></select></label>
      <label>レイアウト <select id="ppLay"><option value="1">1ページ1レース</option><option value="2">詰めて印刷(1ページに複数)</option></select></label>
      <label><input type="checkbox" id="ppFuture"> 発走前のレースだけ</label>
    </div>
    <div class="pp-row"><button type="button" class="pp-print" id="ppPrint">印刷 / PDFに保存</button></div>
    <p class="pp-hint">競馬場ごとに改ページします。印刷画面で「PDFとして保存」を選ぶとPDFになります(iPhoneは 共有 → プリント → プレビューをピンチアウト → 共有でPDF保存)。
    印=AI評価順の◎○▲△、AI1着%・AI3着内%はAIの予測、妙味=AIと人気のズレの判定、メモ欄は手書き用です。</p>
  </div>
  {sections or '<p>本日の予想はありません。</p>'}
</main>
<script>
(function(){{
  function hm(){{ var d=new Date(); return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }}
  function apply(){{
    var on={{}}; document.querySelectorAll('.pp-v').forEach(function(c){{ on[c.value]=c.checked; }});
    var flag=document.getElementById('ppFlag').value, fut=document.getElementById('ppFuture').checked, two=document.getElementById('ppLay').value==='2', now=hm();
    document.querySelectorAll('.pp-card').forEach(function(c){{
      var ok=on[c.dataset.venue]!==false;
      if(ok && flag!=='all') ok=(' '+c.dataset.flags+' ').indexOf(' '+flag+' ')>=0;
      if(ok && fut && c.dataset.post && c.dataset.post<now) ok=false;
      c.style.display=ok?'':'none';
    }});
    document.querySelectorAll('.pp-venue').forEach(function(v){{
      var any=Array.prototype.some.call(v.querySelectorAll('.pp-card'),function(c){{ return c.style.display!=='none'; }});
      v.style.display=any?'':'none'; v.querySelector('.pp-grid').classList.toggle('two',two);
    }});
  }}
  document.querySelectorAll('.pp-controls input, .pp-controls select').forEach(function(el){{ el.addEventListener('change',apply); }});
  document.getElementById('ppPrint').addEventListener('click',function(){{ apply(); window.print(); }});
  apply();
}})();
</script>
</body></html>"""
