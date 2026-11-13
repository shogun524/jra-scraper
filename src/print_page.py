"""
JRA「印刷用」(print.html): その日の全レースの予想を、A4の紙に印刷・PDF保存できる形にまとめる。
generate_html.py が予想ページを作るたびに一緒に作る(オッズ更新のたびに最新になる)。

画面でレース番号をタップして印刷するレースを選ぶ(まとめて: 全部/AI買い目/荒れそう/発走前、競馬場ごとに全部/外す)。
選択はブラウザに覚えておく。「選んだレースを印刷」でブラウザの印刷画面を開く。操作パネルは印刷に出ない。
各レース: 発走・レース名・3連複の万馬券率と配当の目安・AI買い目(対象のとき)・出走表(印・枠・馬番・馬名・騎手・
人気・単勝・馬体重・AI1着率・AI3着内率・妙味)と、手書き用の「メモ」欄。
"""
import html as _html
import json
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
    def gv(r, c):
        v = r.get(c) if c in r.index else None
        try:
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else v
        except TypeError:
            return v

    def style(x):
        if x is None:
            return ""
        return "逃先" if x < 0.30 else ("好位" if x < 0.50 else ("中団" if x < 0.70 else "差追"))

    def pct(x, d=0):
        return "" if x is None else f"{float(x) * 100:.{d}f}%"

    def kv(k, v, strong=False):
        return f'<span class="kv"><i>{k}</i><b class="{"hl" if strong else ""}">{v}</b></span>'

    rows = ""
    for i, (_, r) in enumerate(g.iterrows()):
        w = int(r["waku"]) if pd.notna(r.get("waku")) else 0
        bg, fg = WAKU_COLORS.get(w, ("#eee", "#222"))
        hw, wd = gv(r, "horse_weight"), gv(r, "weight_diff")
        hw_txt = (f'{_f(hw, "{:.0f}")}' + (f'<small>({_f(wd, "{:+.0f}")})</small>' if wd is not None else "")) if hw is not None else ""
        top = int(r.get("pred_win_rank", 99)) == 1
        mk = gv(r, "mark") or ""
        vt = str(gv(r, "value_tag") or "")
        vcls = "v-hot" if "大" in vt else ("v-ok" if "あり" in vt else ("v-bad" if "過剰" in vt else ""))
        sexage = f'{gv(r, "sex") or ""}{_f(gv(r, "age"), "{:.0f}")}'
        kc = gv(r, "weight_carry_change_from_last")
        kin = _f(gv(r, "weight_carry"), "{:.0f}") + (f'<small>({_f(kc, "{:+.0f}")})</small>' if kc not in (None, 0) else "")
        # 2行目: 過去の成績を「項目名 + 数字」で区切って並べる(良い数字は赤太字)
        last, st5, cs = gv(r, "last_race_finish"), gv(r, "recent5_starts"), gv(r, "career_starts")
        parts = []
        if not cs and not st5 and last is None:
            parts.append(kv("経歴", "初出走"))
        else:
            parts.append(kv("前走", f'{_f(last, "{:.0f}")}着' if last is not None else "-", last is not None and float(last) <= 3))
            if st5:
                t3 = gv(r, "recent5_top3rate") or 0
                parts.append(kv(f'近{_f(st5, "{:.0f}")}走', f'平均{_f(gv(r, "recent5_avg_finish"))}着 3着内{pct(t3)}', t3 >= 0.5))
            if cs:
                cw = gv(r, "career_winrate") or 0
                parts.append(kv("通算", f'{_f(cs, "{:.0f}")}戦{round(cw * float(cs))}勝', cw >= 0.25))
            ds = gv(r, "dist_aptitude_starts")
            if ds:
                dt = gv(r, "dist_aptitude_top3rate") or 0
                parts.append(kv("同距離帯", f'{_f(ds, "{:.0f}")}戦 3着内{pct(dt)}', dt >= 0.5))
        dd = gv(r, "days_since_last")
        if dd is not None:
            parts.append(kv("間隔", f'{_f(dd, "{:.0f}")}日', False))
        dc = gv(r, "distance_change_from_last")
        if dc not in (None, 0):
            parts.append(kv("距離", f'{_f(dc, "{:+.0f}")}m'))
        jw, tw = gv(r, "jockey_winrate"), gv(r, "trainer_winrate")
        if jw is not None or tw is not None:
            parts.append(kv("騎/厩", f'{pct(jw)} / {pct(tw)}', (jw or 0) >= 0.15))
        rec = "".join(parts)
        ev = gv(r, "expected_value")
        pw = float(r.get("pred_win_norm", 0) or 0)
        p3 = float(r.get("pred_top3_norm", 0) or 0)
        bar = f'<span class="bar"><span style="width:{min(pw * 250, 100):.0f}%"></span></span>'
        ks = style(gv(r, "recent5_style_ratio"))
        cls = ("top " if top else "") + ("odd" if i % 2 else "even")
        rows += (f'<tr class="{cls} r1"><td class="mk mk-{_html.escape(str(mk))}" rowspan="2">{_html.escape(str(mk))}</td>'
                 f'<td class="c" rowspan="2"><span class="wk" style="background:{bg};color:{fg};">{w or ""}</span></td>'
                 f'<td class="c ub" rowspan="2">{int(r["umaban"])}</td>'
                 f'<td class="nm">{_html.escape(str(r["horse"]))}</td>'
                 f'<td class="c sm">{_html.escape(sexage)}</td><td class="c sm">{kin}</td>'
                 f'<td class="nw sm">{_html.escape(str(r.get("jockey") or ""))}</td>'
                 f'<td class="c sm nw">{ks}</td>'
                 f'<td class="c sep">{_f(r.get("market_rank"), "{:.0f}")}</td><td class="r b">{_f(r.get("odds_win"))}</td>'
                 f'<td class="r sm">{hw_txt}</td>'
                 f'<td class="r sep ai"><b>{_f(pw * 100)}</b>{bar}</td>'
                 f'<td class="r">{_f(p3 * 100, "{:.0f}")}</td>'
                 f'<td class="r sm {"evh" if ev is not None and float(ev) >= 1 else ""}">{_f(ev, "{:.2f}")}</td>'
                 f'<td class="c"><span class="vt {vcls}">{_html.escape(vt)}</span></td><td class="memo" rowspan="2"></td></tr>'
                 f'<tr class="{cls} rc"><td colspan="12">{rec}</td></tr>')
    # レースの条件
    surf, dist, baba = gv(f0, "surface"), gv(f0, "distance"), gv(f0, "baba")
    conds = [x for x in [f'{surf or ""}{_f(dist, "{:.0f}")}m' if dist is not None else "",
                         f'馬場 {baba}' if baba else "", f'{len(g)}頭'] if x]
    comp = gv(f0, "race_competitiveness")
    if comp is not None:
        conds.append(f'混戦度 {_f(comp, "{:.0f}")}')
    favp = gv(f0, "course_fav_win_pct")
    if favp is not None:
        conds.append(f'このコースの1番人気勝率 {_f(favp, "{:.0f}")}%')
    cond_html = "".join(f'<span class="tag">{_html.escape(c)}</span>' for c in conds)
    order = g.sort_values("pred_win_rank")
    top3 = "".join(f'<div class="t3"><b class="mk-{m}">{m}</b><span class="t3n">{int(x.umaban)} {_html.escape(str(x.horse))}</span>'
                   f'<span class="t3p">{float(x.pred_win_norm) * 100:.0f}%</span></div>'
                   for m, (_, x) in zip(["◎", "○", "▲"], order.head(3).iterrows()))
    if mv:
        mcls = "hi" if mv["man"] >= 0.5 else ("lo" if mv["man"] < 0.2 else "")
        man_box = (f'<div class="box man {mcls}"><i>3連複の万馬券率</i><b>{mv["man"]:.0%}</b></div>'
                   f'<div class="box"><i>3連複の配当の目安</i><b>{mv["q50"]:,.0f}円</b>'
                   f'<small>半分は {mv["q25"]:,.0f}〜{mv["q75"]:,.0f}円</small></div>')
    else:
        man_box = '<div class="box"><i>3連複の万馬券率</i><b>-</b><small>オッズ待ち</small></div>'
    venue = str(f0.get("track_name", ""))
    rno = int(f0['race_number'])
    return f"""
    <div class="pp-card" id="c{_html.escape(rid)}" data-rid="{_html.escape(rid)}" data-venue="{_html.escape(venue)}" data-flags="{' '.join(flags)}" data-post="{_html.escape(post)}">
      <div class="pp-head"><span class="rno">{_html.escape(venue)} <b>{rno}R</b></span>
        <span class="rname">{_html.escape(str(f0.get('race_name', '') or ''))}</span><span class="pp-post">発走 <b>{_html.escape(post)}</b></span></div>
      <div class="pp-cond">{cond_html}</div>
      <div class="pp-sum">{man_box}<div class="box ai3"><i>AIの上位(1着率)</i>{top3}</div></div>
      {bet_html}
      <table class="pp-t"><thead><tr><th>印</th><th>枠</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th><th>脚質</th>
      <th class="sep">人気</th><th>単勝</th><th>馬体重</th><th class="sep">AI1着%</th><th>AI3着内%</th><th>期待値</th><th>妙味</th><th class="memo">メモ</th></tr></thead><tbody>{rows}</tbody></table>
    </div>"""


def build_print(df: pd.DataFrame, date_label: str, generated_at: str, trio: dict | None = None) -> str:
    prm = load_manbaken()
    sections, picker = "", ""
    df = df.copy()
    df["race_number"] = pd.to_numeric(df["race_number"], errors="coerce")
    for venue, gvn in df.groupby("track_name", sort=False):
        races = sorted(gvn.groupby("race_id"), key=lambda x: x[1]["race_number"].iloc[0])
        cards, chips = "", ""
        for rid, g in races:
            rid = str(rid)
            cards += _card(rid, g, trio, prm)
            mv = _manbaken(g.sort_values("umaban"), trio, prm)
            man = f'{mv["man"]:.0%}' if mv else "-"
            mcls = "hi" if mv and mv["man"] >= 0.5 else ("lo" if mv and mv["man"] < 0.2 else "")
            post = str(g["post_time"].iloc[0] or "") if "post_time" in g.columns else ""
            chips += (f'<label class="rc-chip {mcls}"><input type="checkbox" class="pp-r" value="{_html.escape(rid)}" checked>'
                      f'<span><b>{int(g["race_number"].iloc[0])}R</b><small>{_html.escape(post)}</small><em>{man}</em></span></label>')
        v = _html.escape(str(venue))
        picker += (f'<div class="pp-vrow" data-venue="{v}"><div class="pp-vname">{v}'
                   f'<button type="button" class="mini" data-act="von">全部</button><button type="button" class="mini" data-act="voff">外す</button></div>'
                   f'<div class="pp-chips">{chips}</div></div>')
        sections += (f'<section class="pp-venue" data-venue="{v}"><h2>{v}　{_html.escape(date_label)}</h2>'
                     f'<div class="pp-grid">{cards}</div></section>')
    n = df["race_id"].nunique()
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>印刷用 予想一覧</title>
<style>
  * {{ box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  .nw {{ white-space:nowrap; }}
  body {{ margin:0; background:#F6F1E4; color:#2A2118; font-family:"Noto Sans JP","Hiragino Sans",system-ui,sans-serif; font-size:13px; }}
  header {{ background:#1F4D3A; color:#fff; padding:14px 16px; }} header h1 {{ margin:0; font-size:1.2rem; }}
  header p {{ margin:4px 0 0; font-size:.8rem; opacity:.9; }} header a {{ color:#F0D896; }}
  main {{ max-width:1000px; margin:0 auto; padding:12px; }}
  /* 操作パネル */
  .pp-controls {{ background:#fff; border:1px solid #E4DAC0; border-radius:10px; padding:12px; margin-bottom:12px; }}
  .pp-controls h3 {{ margin:0 0 8px; font-size:1rem; }}
  .pp-quick {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }}
  .pp-quick button, .mini {{ border:1px solid #1F4D3A; background:#fff; color:#1F4D3A; border-radius:999px; padding:5px 12px; font-size:12px; font-weight:700; cursor:pointer; }}
  .pp-quick button:active, .mini:active {{ background:#1F4D3A; color:#fff; }}
  .mini {{ padding:2px 9px; font-size:11px; margin-left:6px; }}
  .pp-vrow {{ border-top:1px solid #EEE6D2; padding:8px 0; }}
  .pp-vname {{ font-weight:700; margin-bottom:6px; }}
  .pp-chips {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(64px,1fr)); gap:6px; }}
  .rc-chip input {{ position:absolute; opacity:0; pointer-events:none; }}
  .rc-chip span {{ display:flex; flex-direction:column; align-items:center; border:2px solid #D8CDB2; border-radius:8px; padding:4px 2px; background:#F3EEE2; color:#8A7E6A; cursor:pointer; line-height:1.25; }}
  .rc-chip b {{ font-size:14px; }} .rc-chip small {{ font-size:10px; }} .rc-chip em {{ font-style:normal; font-size:11px; font-weight:700; }}
  .rc-chip input:checked + span {{ background:#fff; border-color:#1F4D3A; color:#2A2118; box-shadow:inset 0 -3px 0 #1F4D3A; }}
  .rc-chip.hi em {{ color:#C0392B; }} .rc-chip.lo em {{ color:#2E7D4F; }}
  .pp-row {{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:10px 0 0; }}
  .pp-print {{ background:#1F4D3A; color:#fff; border:0; border-radius:8px; padding:10px 18px; font-size:15px; font-weight:700; }}
  .pp-count {{ font-weight:700; }}
  .pp-hint {{ font-size:11px; color:#6B5F4D; margin:8px 0 0; line-height:1.6; }}
  /* レースカード */
  .pp-venue h2 {{ font-size:1rem; margin:14px 0 6px; }}
  .pp-card {{ background:#fff; border:1px solid #bbb; border-radius:6px; padding:10px 12px; margin:0 0 12px; break-inside:avoid; }}
  .pp-head {{ display:flex; gap:12px; align-items:baseline; border-bottom:3px solid #1F4D3A; padding-bottom:4px; }}
  .rno {{ font-size:1rem; font-weight:700; }} .rno b {{ font-size:1.6rem; }} .rname {{ font-size:1.05rem; font-weight:700; }}
  .pp-post {{ margin-left:auto; }} .pp-post b {{ font-size:1.25rem; }}
  .pp-cond {{ margin:6px 0; display:flex; flex-wrap:wrap; gap:4px; }}
  .tag {{ background:#EEF3EF; border:1px solid #C9D9CF; border-radius:4px; padding:1px 7px; font-size:11.5px; }}
  .pp-sum {{ display:grid; grid-template-columns:1fr 1.3fr 2.2fr; gap:6px; margin:6px 0; }}
  .box {{ border:1px solid #DDD3BC; border-radius:6px; padding:4px 8px; background:#FCFAF4; }}
  .box i {{ display:block; font-style:normal; font-size:10.5px; color:#6B5F4D; }}
  .box b {{ font-size:1.3rem; }} .box small {{ display:block; font-size:10.5px; color:#555; }}
  .box.man.hi {{ background:#FDECEA; border-color:#E6A39B; }} .box.man.hi b {{ color:#C0392B; }}
  .box.man.lo b {{ color:#2E7D4F; }}
  .ai3 {{ display:flex; flex-wrap:wrap; align-items:baseline; column-gap:14px; }} .ai3 i {{ flex-basis:100%; }}
  .ai3 .t3 {{ display:flex; gap:4px; align-items:baseline; line-height:1.5; }} .t3 > b {{ font-size:1.05rem; }} .t3n {{ font-weight:700; }} .t3p {{ color:#555; font-variant-numeric:tabular-nums; }}
  .pp-bet {{ margin:6px 0; padding:5px 8px; border:2px solid #B8862B; border-radius:6px; background:#FBF6E8; }}
  .cb {{ display:inline-block; border:1px solid #999; border-radius:3px; padding:0 4px; margin:1px 2px; background:#fff; font-variant-numeric:tabular-nums; }}
  /* 出走表 */
  .pp-t {{ width:100%; border-collapse:collapse; margin-top:6px; font-variant-numeric:tabular-nums; }}
  .pp-t th {{ background:#1F4D3A; color:#fff; font-size:10.5px; font-weight:700; padding:3px 3px; border:1px solid #1F4D3A; white-space:nowrap; }}
  .pp-t td {{ border-left:1px solid #ddd; border-right:1px solid #ddd; padding:3px 4px; }}
  .pp-t tr.r1 td {{ border-top:1px solid #999; }}
  .pp-t tr.odd td {{ background:#F7F5EF; }} .pp-t tr.top td {{ background:#FFF1D6; }}
  .pp-t .sep {{ border-left:2px solid #888; }}
  .c {{ text-align:center; }} .r {{ text-align:right; }} .b {{ font-weight:700; }}
  .sm {{ font-size:11px; }} .sm small, .r small {{ font-size:9.5px; color:#666; }}
  .nm {{ font-weight:700; font-size:13.5px; white-space:nowrap; }}
  .ub {{ font-weight:700; font-size:15px; }}
  .mk {{ text-align:center; font-weight:900; font-size:16px; }} .mk-◎ {{ color:#C0392B; }} .mk-○ {{ color:#1F4D3A; }} .mk-▲ {{ color:#2a6ad1; }}
  .wk {{ display:inline-block; width:20px; text-align:center; border:1px solid #888; border-radius:3px; font-weight:700; }}
  .ai b {{ font-size:13px; }} .bar {{ display:block; height:4px; background:#eee; border-radius:2px; margin-top:1px; }}
  .bar span {{ display:block; height:100%; background:#C0392B; border-radius:2px; }}
  .evh {{ color:#C0392B; font-weight:700; }}
  .vt {{ font-size:10.5px; white-space:nowrap; padding:0 4px; border-radius:3px; }}
  .v-hot {{ background:#C0392B; color:#fff; font-weight:700; }} .v-ok {{ background:#FCE3C4; color:#7A4A00; }} .v-bad {{ color:#999; }}
  .memo {{ width:12%; border-left:2px solid #888 !important; }}
  tr.rc td {{ font-size:10.5px; color:#333; padding:1px 4px 3px; }}
  .kv {{ display:inline-block; margin-right:10px; white-space:nowrap; }}
  .kv i {{ font-style:normal; color:#fff; background:#8A7E6A; border-radius:3px; padding:0 4px; margin-right:3px; font-size:9.5px; }}
  .kv b {{ font-weight:500; }} .kv b.hl {{ color:#C0392B; font-weight:700; }}
  .pp-grid.two {{ display:grid; grid-template-columns:1fr; }}
  @media (max-width:640px) {{ .pp-sum {{ grid-template-columns:1fr 1fr; }} .ai3 {{ grid-column:1/-1; }} .pp-card {{ overflow-x:auto; }} }}
  @media print {{
    @page {{ size:A4; margin:7mm; }}
    body {{ background:#fff; font-size:10px; }} header, .pp-controls {{ display:none; }} main {{ padding:0; max-width:none; }}
    .pp-card.off {{ display:none !important; }}
    .pp-venue {{ break-before:page; }} .pp-venue:first-of-type {{ break-before:auto; }}
    .pp-venue:has(.pp-grid:not(.two)) > h2 {{ display:none; }}
    .pp-grid:not(.two) .pp-card {{ break-after:page; break-inside:auto; padding:2px 4px; margin:0; border:0; }}
    .pp-t td {{ padding:1px 3px; }} .pp-t th {{ font-size:9px; padding:2px; }} tr.rc td {{ font-size:8.8px; padding:0 3px 2px; }}
    .nm {{ font-size:11.5px; }} .ub {{ font-size:12.5px; }} .mk {{ font-size:13px; }}
    .pp-t tr {{ break-inside:avoid; }} .pp-sum {{ margin:4px 0; }} .box {{ padding:2px 6px; }} .box b {{ font-size:1.05rem; }}
    .kv {{ margin-right:7px; }} .kv i {{ font-size:8px; padding:0 3px; }} .bar {{ height:3px; }} .pp-cond {{ margin:3px 0; }} .tag {{ font-size:10px; padding:0 5px; }}
  }}
</style></head><body>
<header><h1>印刷用 予想一覧</h1>
<p>{_html.escape(date_label)} ・ 更新 {_html.escape(generated_at)} ・ <a href="index.html">全レースの予想</a> ・ <a href="picks.html">買うべきレース</a></p></header>
<main>
  <div class="pp-controls">
    <h3>印刷するレースを選ぶ(全{n}レース)</h3>
    <div class="pp-quick">
      <button type="button" data-q="all">全部</button><button type="button" data-q="none">全部外す</button>
      <button type="button" data-q="bet">AI買い目のレース</button><button type="button" data-q="are">荒れそう(万馬券率50%以上)</button>
      <button type="button" data-q="future">発走前のレース</button>
    </div>
    {picker}
    <div class="pp-row">
      <label>レイアウト <select id="ppLay"><option value="1">1ページ1レース</option><option value="2">詰めて印刷(1ページに複数)</option></select></label>
      <span class="pp-count" id="ppCount"></span>
      <button type="button" class="pp-print" id="ppPrint">選んだレースを印刷 / PDFに保存</button>
    </div>
    <p class="pp-hint">レース番号をタップで 選ぶ/外す(下の数字は3連複の万馬券率。赤=荒れそう・緑=堅そう)。選んだレースだけ下に表示・印刷されます。
    印刷画面で「PDFとして保存」を選ぶとPDFになります(iPhoneは 共有 → プリント → プレビューをピンチアウト → 共有でPDF保存)。<br>
    表の見方: 印=AIの評価順、AI1着%(赤い棒)・AI3着内%=AIの予測、期待値=AI1着率×単勝(1以上は赤)、妙味=AIと人気のズレ、
    脚質=近5走の4コーナーの位置。2行目は過去の成績(赤字=前走3着以内・3着内率50%以上・通算勝率25%以上など良い数字)。メモ欄は手書き用です。</p>
  </div>
  {sections or '<p>本日の予想はありません。</p>'}
</main>
<script>
(function(){{
  var KEY='jra-print-'+{json.dumps(date_label)};
  function hm(){{ var d=new Date(); return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }}
  var boxes=Array.prototype.slice.call(document.querySelectorAll('.pp-r'));
  function card(id){{ return document.getElementById('c'+id); }}
  function save(){{ try{{ localStorage.setItem(KEY, JSON.stringify(boxes.filter(function(b){{return !b.checked;}}).map(function(b){{return b.value;}}))); }}catch(e){{}} }}
  function apply(){{
    var two=document.getElementById('ppLay').value==='2', n=0;
    boxes.forEach(function(b){{ var c=card(b.value); if(!c) return; c.style.display=b.checked?'':'none'; c.classList.toggle('off',!b.checked); if(b.checked) n++; }});
    document.querySelectorAll('.pp-venue').forEach(function(v){{
      var any=Array.prototype.some.call(v.querySelectorAll('.pp-card'),function(c){{ return c.style.display!=='none'; }});
      v.style.display=any?'':'none'; v.querySelector('.pp-grid').classList.toggle('two',two);
    }});
    document.getElementById('ppCount').textContent=n+'レース選択中';
    document.getElementById('ppPrint').disabled=(n===0);
    save();
  }}
  try{{ var off=JSON.parse(localStorage.getItem(KEY)||'[]'); boxes.forEach(function(b){{ if(off.indexOf(b.value)>=0) b.checked=false; }}); }}catch(e){{}}
  document.querySelectorAll('.pp-quick button').forEach(function(btn){{ btn.addEventListener('click',function(){{
    var q=btn.dataset.q, now=hm();
    boxes.forEach(function(b){{ var c=card(b.value); if(!c) return; var f=' '+(c.dataset.flags||'')+' ';
      b.checked = q==='all' ? true : q==='none' ? false : q==='future' ? !(c.dataset.post && c.dataset.post<now) : f.indexOf(' '+q+' ')>=0; }});
    apply();
  }}); }});
  document.querySelectorAll('.pp-vrow .mini').forEach(function(btn){{ btn.addEventListener('click',function(){{
    var row=btn.closest('.pp-vrow'), on=btn.dataset.act==='von';
    row.querySelectorAll('.pp-r').forEach(function(b){{ b.checked=on; }}); apply();
  }}); }});
  boxes.forEach(function(b){{ b.addEventListener('change',apply); }});
  document.getElementById('ppLay').addEventListener('change',apply);
  document.getElementById('ppPrint').addEventListener('click',function(){{ apply(); window.print(); }});
  apply();
}})();
</script>
</body></html>"""
