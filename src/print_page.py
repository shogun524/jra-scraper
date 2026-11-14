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


def _card(rid, g, trio, prm, past=None, bias=None, ped=None, pairs=None):
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

    def run_cell(x):
        """近走1つ分: 左に大きく着順、右に3行(日付・場・クラス・距離馬場 / 頭数・馬番・人気・騎手斤量 / タイム差・上り・通過・体重)"""
        if not x:
            return '<td class="run none"></td>'
        fin = x.get("fin")
        fcls = "f1" if fin == 1 else ("f23" if fin in (2, 3) else ("f45" if fin in (4, 5) else ""))
        fin_txt = str(fin) if isinstance(fin, int) else _html.escape(str(fin or "-"))[:2]
        n, ub, pop = x.get("n"), x.get("ub"), x.get("pop")
        l1 = f'{_html.escape(x.get("d", "")[3:])} {_html.escape(x.get("v", ""))} {_html.escape(x.get("sd", ""))}{_html.escape(x.get("baba", ""))} <b>{_html.escape(x.get("cls", "")[:6])}</b>'
        l2 = (f'{n or "-"}頭{ub or "-"}番 <span class="{"pp1" if pop == 1 else ""}">{pop or "-"}人</span> '
              f'{_html.escape(x.get("jky", ""))}{_f(x.get("kin"), "{:.0f}")}')
        diff = x.get("diff")
        dtxt = "" if diff is None else (f'<span class="dw">{diff:+.1f}</span>' if diff < 0 else f'({diff:.1f})')
        l3r = x.get("l3r")
        l3 = (f'上{_f(x.get("l3f"))}<span class="{"l3top" if l3r and l3r <= 3 else ""}">{"①②③"[l3r - 1] if l3r and l3r <= 3 else (f"[{l3r}]" if l3r else "")}</span>'
              if x.get("l3f") else "")
        hw = f'{x["hw"]}' + (f'({x["hwd"]:+d})' if x.get("hwd") is not None else "") if x.get("hw") else ""
        l3line = f'{_html.escape(x.get("t", ""))}{dtxt} {l3} {_html.escape(x.get("pas", ""))} {hw}'
        return (f'<td class="run"><div class="rw"><span class="fin {fcls}">{fin_txt}</span>'
                f'<span class="rl"><span>{l1}</span><span>{l2}</span><span>{l3line}</span></span></div></td>')

    from track_bias import favored as _fav

    def blood_html(horse):
        b = (ped or {}).get(str(horse)) or {}
        if not b.get("sire"):
            return ""
        return f'<div class="bl">父{_html.escape(b["sire"])} 母父{_html.escape(b.get("damsire") or "-")}</div>'

    def place_html(r):
        lo, hi = r.get("odds_place_lo"), r.get("odds_place_hi")
        if lo is None or pd.isna(lo):
            return ""
        return f'<div class="plc">複{float(lo):.1f}{"-" + format(float(hi), ".1f") if hi is not None and pd.notna(hi) and float(hi) != float(lo) else ""}</div>'
    b = (bias or {}).get(str(rid)) or {}
    rows = ""
    for i, (_, r) in enumerate(g.iterrows()):
        w = int(r["waku"]) if pd.notna(r.get("waku")) else 0
        bg, fg = WAKU_COLORS.get(w, ("#eee", "#222"))
        hw, wd = gv(r, "horse_weight"), gv(r, "weight_diff")
        hw_txt = (f'{_f(hw, "{:.0f}")}' + (f'({_f(wd, "{:+.0f}")})' if wd is not None else "")) if hw is not None else ""
        top = int(r.get("pred_win_rank", 99)) == 1
        mk = gv(r, "mark") or ""
        vt = str(gv(r, "value_tag") or "")
        vcls = "v-hot" if "大" in vt else ("v-ok" if "あり" in vt else ("v-bad" if "過剰" in vt else ""))
        sexage = f'{gv(r, "sex") or ""}{_f(gv(r, "age"), "{:.0f}")}'
        kc = gv(r, "weight_carry_change_from_last")
        kin = _f(gv(r, "weight_carry"), "{:.0f}") + (f'({_f(kc, "{:+.0f}")})' if kc not in (None, 0) else "")
        pr = (past or {}).get(str(r["horse"])) or {}
        trainer = pr.get("trainer", "")
        runs = (pr.get("runs") or [])[:4]
        # 成績の欄
        st = []
        cs = gv(r, "career_starts")
        if cs:
            cw = gv(r, "career_winrate") or 0
            c3 = gv(r, "career_top3rate")
            st.append(f'通算 <b>{_f(cs, "{:.0f}")}戦{round(cw * float(cs))}勝</b>' + (f' 複{pct(c3)}' if c3 is not None else ""))
        elif not runs:
            st.append("<b>初出走</b>")
        ds = gv(r, "dist_aptitude_starts")
        if ds:
            dt = gv(r, "dist_aptitude_top3rate") or 0
            st.append(f'同距離 {_f(ds, "{:.0f}")}戦 <b class="{"hl" if dt >= 0.5 else ""}">複{pct(dt)}</b>')
        dd, dc = gv(r, "days_since_last"), gv(r, "distance_change_from_last")
        if dd is not None:
            st.append(f'間隔{_f(dd, "{:.0f}")}日' + (f' 距離{_f(dc, "{:+.0f}")}' if dc not in (None, 0) else ""))
        jw, tw = gv(r, "jockey_winrate"), gv(r, "trainer_winrate")
        if jw is not None or tw is not None:
            st.append(f'騎{pct(jw)} 厩{pct(tw)}')
        ev = gv(r, "expected_value")
        pw = float(r.get("pred_win_norm", 0) or 0)
        mm = gv(r, "mm_win")
        mm_txt = f' 込{_f(float(mm) * 100, "{:.0f}")}%' if mm is not None else ""
        p3 = float(r.get("pred_top3_norm", 0) or 0)
        bar = f'<span class="bar"><span style="width:{min(pw * 250, 100):.0f}%"></span></span>'
        ks = style(gv(r, "recent5_style_ratio"))
        cls = ("top " if top else "") + ("odd" if i % 2 else "even")
        pop = _f(r.get("market_rank"), "{:.0f}")
        run_cells = "".join(run_cell(runs[k] if k < len(runs) else None) for k in range(4))
        rows += (f'<tr class="{cls}"><td class="mk mk-{_html.escape(str(mk))}">{_html.escape(str(mk))}</td>'
                 f'<td class="c"><span class="wk" style="background:{bg};color:{fg};">{w or ""}</span></td>'
                 f'<td class="c ub {({True: "bfav", False: "bunf"}).get(_fav(r["umaban"], len(g), b.get("side")), "")}">{int(r["umaban"])}</td>'
                 f'<td class="horse"><div class="nm">{_html.escape(str(r["horse"]))}</div>'
                 f'<div class="hs">{_html.escape(sexage)} {kin} <b>{_html.escape(str(r.get("jockey") or ""))}</b>'
                 f'{" ・" + _html.escape(trainer) if trainer else ""} {hw_txt}{" ・" + ks if ks else ""}</div>'
                 f'{blood_html(r["horse"])}</td>'
                 f'<td class="c sep od"><div class="popn {"pp1" if pop == "1" else ""}">{pop}<small>人気</small></div><div class="b">{_f(r.get("odds_win"))}</div>{place_html(r)}</td>'
                 f'<td class="sep ai"><div><b>{_f(pw * 100)}</b><small>%</small> {bar} <span class="vt {vcls}">{_html.escape(vt)}</span></div>'
                 f'<div class="sm">複{_f(p3 * 100, "{:.0f}")}%{mm_txt} 期待<span class="{"evh" if ev is not None and float(ev) >= 1 else ""}">{_f(ev, "{:.2f}")}</span></div></td>'
                 f'<td class="sep stat">{"<br>".join([" ".join(st[i:i + 2]) for i in range(0, len(st), 2)])}</td>'
                 f'{run_cells}<td class="memo"></td></tr>')
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
    if b.get("label"):
        cond_html += (f'<span class="tag bias-{ {"内": "in", "外": "out"}.get(b["side"], "flat") }">当日{_html.escape(b.get("surface", ""))}バイアス '
                      f'<b>{_html.escape(b["label"])}</b>({b["n"]}R分{"・緑の馬番が有利な側" if b["side"] else ""})</span>')
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
    from hitrate import plan as _hp
    _h = _hp(g)
    hit_line = ("" if not _h else '<div class="pp-pop pp-hit"><i>的中率重視</i>' +
                "　／　".join(f'<b>{b}</b> {_html.escape(c)} <span>{p:.0%}</span>' for b, c, p in _h["items"]) + "</div>")
    pop_html = ""
    if pairs is not None and not pairs.empty:
        pr = pairs[pairs["race_id"].astype(str) == str(rid)]
        parts = []
        for bet, n in (("馬連", 6), ("ワイド", 4)):
            t = pr[pr["bet"] == bet].dropna(subset=["odds"]).sort_values("odds").head(n)
            if len(t):
                parts.append(f'<b>{bet}</b> ' + "　".join(f'{_html.escape(str(x.combo))} <span>{float(x.odds):.1f}</span>' for x in t.itertuples()))
        if parts:
            pop_html = f'<div class="pp-pop"><i>人気の組</i>{"　／　".join(parts)}</div>'
    return f"""
    <div class="pp-card{" big" if len(g) >= 13 else ""}{" huge" if len(g) >= 16 else ""}" id="c{_html.escape(rid)}" data-rid="{_html.escape(rid)}" data-venue="{_html.escape(venue)}" data-flags="{' '.join(flags)}" data-post="{_html.escape(post)}">
      <div class="pp-head"><span class="rno">{_html.escape(venue)} <b>{rno}R</b></span>
        <span class="rname">{_html.escape(str(f0.get('race_name', '') or ''))}</span><span class="pp-post">発走 <b>{_html.escape(post)}</b></span></div>
      <div class="pp-cond">{cond_html}</div>
      <div class="pp-sum">{man_box}<div class="box ai3"><i>AIの上位(1着率)</i>{top3}</div></div>
      {pop_html}{hit_line}
      {bet_html}
      <table class="pp-t"><colgroup><col class="c-mk"><col class="c-wk"><col class="c-ub"><col class="c-h"><col class="c-od"><col class="c-ai"><col class="c-st">
      <col><col><col><col><col class="c-memo"></colgroup><thead><tr><th>印</th><th>枠</th><th>馬番</th><th>馬名 / 性齢 斤量 騎手・厩舎 馬体重 脚質 / 父・母父</th>
      <th class="sep">人気<br>単勝</th><th class="sep">AI 1着%<br>複勝% 期待値</th><th class="sep">成績</th>
      <th class="sep">前走</th><th>2走前</th><th>3走前</th><th>4走前</th><th class="memo">メモ</th></tr></thead><tbody>{rows}</tbody></table>
    </div>"""


def short_name(nm):
    """チップ用の短いレース名: 「３歳以上１勝クラス」→「1勝C」、特別・重賞は名前"""
    import unicodedata
    nm = unicodedata.normalize("NFKC", str(nm or ""))
    for k, v in (("新馬", "新馬"), ("未勝利", "未勝利"), ("1勝クラス", "1勝C"), ("2勝クラス", "2勝C"), ("3勝クラス", "3勝C")):
        if nm.endswith(k) or nm == k:
            return v
    nm = nm.replace("ステークス", "S")
    return nm[:8]


def build_print(df: pd.DataFrame, date_label: str, generated_at: str, trio: dict | None = None, past: dict | None = None, bias: dict | None = None,
                ped: dict | None = None, pairs: pd.DataFrame | None = None) -> str:
    prm = load_manbaken()
    sections, picker = "", ""
    df = df.copy()
    df["race_number"] = pd.to_numeric(df["race_number"], errors="coerce")
    for venue, gvn in df.groupby("track_name", sort=False):
        races = sorted(gvn.groupby("race_id"), key=lambda x: x[1]["race_number"].iloc[0])
        cards, chips = "", ""
        for rid, g in races:
            rid = str(rid)
            cards += _card(rid, g, trio, prm, past, bias, ped, pairs)
            mv = _manbaken(g.sort_values("umaban"), trio, prm)
            man = f'{mv["man"]:.0%}' if mv else "-"
            mcls = "hi" if mv and mv["man"] >= 0.5 else ("lo" if mv and mv["man"] < 0.2 else "")
            post = str(g["post_time"].iloc[0] or "") if "post_time" in g.columns else ""
            chips += (f'<label class="rc-chip {mcls}"><input type="checkbox" class="pp-r" value="{_html.escape(rid)}" checked>'
                      f'<span><b>{int(g["race_number"].iloc[0])}R</b><small class="cn">{_html.escape(short_name(g["race_name"].iloc[0] if "race_name" in g.columns else ""))}</small><small>{_html.escape(post)}</small><i class="hc">{len(g)}頭</i><em>{man}</em></span></label>')
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
  .pp-chips {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(78px,1fr)); gap:6px; }}
  .rc-chip input {{ position:absolute; opacity:0; pointer-events:none; }}
  .rc-chip span {{ display:flex; flex-direction:column; align-items:center; border:2px solid #D8CDB2; border-radius:8px; padding:4px 2px; background:#F3EEE2; color:#8A7E6A; cursor:pointer; line-height:1.25; }}
  .rc-chip .cn {{ font-size:10.5px; font-weight:700; color:#1F4D3A; white-space:nowrap; overflow:hidden; max-width:100%; }}
  .rc-chip b {{ font-size:14px; }} .rc-chip small {{ font-size:10px; }} .rc-chip em {{ font-style:normal; font-size:11px; font-weight:700; }} .rc-chip .hc {{ font-style:normal; font-size:11px; font-weight:700; color:#2A2118; }}
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
  .rno {{ font-size:1rem; font-weight:700; }} .rno b {{ font-size:1.6rem; }} .rname {{ font-size:1.25rem; font-weight:800; }}
  .pp-post {{ margin-left:auto; }} .pp-post b {{ font-size:1.25rem; }}
  .pp-cond {{ margin:6px 0; display:flex; flex-wrap:wrap; gap:4px; }}
  .ub.bfav {{ background:#D9EEDF !important; box-shadow:inset 0 0 0 2px #6FAE85; }} .ub.bunf {{ color:#B0A796; }}
  .tag.bias-in {{ background:#D9EEDF; border-color:#6FAE85; }} .tag.bias-out {{ background:#F6E3D3; border-color:#D9A27A; }}
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
  /* 出走表(競馬新聞の形: 1頭1行、右に近4走) */
  main {{ max-width:1400px; }}
  .pp-card {{ overflow-x:auto; }}
  .pp-t {{ width:100%; min-width:1180px; border-collapse:collapse; margin-top:6px; font-variant-numeric:tabular-nums; table-layout:fixed; }}
  .pp-t th {{ background:#1F4D3A; color:#fff; font-size:10.5px; font-weight:700; padding:3px; border:1px solid #1F4D3A; line-height:1.25; }}
  .pp-t td {{ border:1px solid #cfc8b8; padding:2px 4px; vertical-align:middle; line-height:1.3; }}
  .pp-t tr.odd td {{ background:#F7F5EF; }} .pp-t tr.top td {{ background:#FFF1D6; }}
  .pp-t .sep {{ border-left:2px solid #777; }}
  col.c-mk {{ width:26px; }} col.c-wk {{ width:28px; }} col.c-ub {{ width:30px; }} col.c-h {{ width:190px; }} col.c-od {{ width:52px; }}
  col.c-ai {{ width:118px; }} col.c-st {{ width:150px; }} col.c-memo {{ width:70px; }}
  .c {{ text-align:center; }} .r {{ text-align:right; }} .b {{ font-weight:700; }} .sm {{ font-size:10.5px; }}
  .nm {{ font-weight:700; font-size:14px; white-space:nowrap; overflow:hidden; }}
  .hs {{ font-size:10.5px; white-space:nowrap; overflow:hidden; color:#333; }}
  .ub {{ font-weight:700; font-size:16px; }}
  .mk {{ text-align:center; font-weight:900; font-size:17px; }} .mk-◎ {{ color:#C0392B; }} .mk-○ {{ color:#1F4D3A; }} .mk-▲ {{ color:#2a6ad1; }}
  .wk {{ display:inline-block; width:20px; text-align:center; border:1px solid #888; border-radius:3px; font-weight:700; }}
  .od .popn {{ font-size:15px; font-weight:700; }} .od .popn small {{ font-size:9px; font-weight:400; }} .pp1 {{ color:#C0392B; font-weight:700; }}
  .ai div {{ white-space:nowrap; overflow:hidden; }} .ai b {{ font-size:14px; }} .ai small {{ font-size:9px; }}
  .bar {{ display:inline-block; vertical-align:middle; width:28px; height:5px; background:#eee; border-radius:2px; }}
  .bar span {{ display:block; height:100%; background:#C0392B; border-radius:2px; }}
  .evh {{ color:#C0392B; font-weight:700; }}
  .vt {{ font-size:10px; white-space:nowrap; padding:0 4px; border-radius:3px; }}
  .v-hot {{ background:#C0392B; color:#fff; font-weight:700; }} .v-ok {{ background:#FCE3C4; color:#7A4A00; }} .v-bad {{ color:#999; }}
  .bl {{ font-size:9.5px; color:#5a4a2a; white-space:nowrap; overflow:hidden; }} .plc {{ font-size:9.5px; color:#555; }}
  .pp-pop {{ font-size:11px; margin:2px 0 4px; padding:2px 8px; background:#F3F7F4; border:1px solid #C9D9CF; border-radius:6px; }} .pp-pop i {{ font-style:normal; color:#6B5F4D; margin-right:6px; }} .pp-pop span {{ color:#C0392B; font-weight:700; }}
  .stat {{ font-size:10px; white-space:nowrap; overflow:hidden; }} .stat b {{ font-weight:700; }} .hl {{ color:#C0392B; }}
  .run {{ font-size:9.5px; padding:2px 3px; }} .run.none {{ background:#f2f2f2 !important; }}  /* 斜線(repeating-linear-gradient)はPDFが巨大になる(1枠ごとにページ大の模様)ので無地 */
  .rw {{ display:flex; gap:4px; align-items:center; }}
  .fin {{ flex:0 0 auto; min-width:22px; text-align:center; font-size:15px; font-weight:900; border-radius:4px; padding:1px 2px; color:#555; }}
  .fin.f1 {{ background:#C0392B; color:#fff; }} .fin.f23 {{ background:#F6D6D2; color:#B03024; }} .fin.f45 {{ color:#222; }}
  .rl {{ display:flex; flex-direction:column; min-width:0; }} .rl > span {{ white-space:nowrap; overflow:hidden; }}
  .rl b {{ font-weight:700; }} .dw {{ color:#C0392B; font-weight:700; }} .l3top {{ color:#C0392B; font-weight:700; }}
  .memo {{ border-left:2px solid #777 !important; }}
  .pp-grid.two {{ display:grid; grid-template-columns:1fr; }}
  .pp-dl {{ margin:8px 0; font-size:13px; }} .pp-dl b {{ margin-right:6px; }}
  .pp-dl a {{ display:inline-block; margin:3px 4px 3px 0; padding:5px 10px; border-radius:999px; background:#1F4D3A; color:#fff; text-decoration:none; font-weight:700; font-size:12px; }}
  .pp-dl a.sub {{ background:#fff; color:#1F4D3A; box-shadow:inset 0 0 0 1.5px #1F4D3A; }}
  /* 2レースで1枚: 3走前・4走前・メモ欄・血統・人気の組を省いてA4縦に */
  body.compact .pp-t td:nth-child(10), body.compact .pp-t td:nth-child(11), body.compact .pp-t td:nth-child(12),
  body.compact .pp-t th:nth-child(10), body.compact .pp-t th:nth-child(11), body.compact .pp-t th:nth-child(12),
  body.compact .pp-t col:nth-child(n+10), body.compact .bl, body.compact .pp-pop:not(.pp-hit), body.compact .pp-bet {{ display:none; }}
  body.compact .pp-t {{ min-width:0; }} body.compact col.c-h {{ width:190px; }} body.compact col.c-ai {{ width:84px; }} body.compact col.c-od {{ width:66px; }}
  body.compact .od {{ white-space:nowrap; }}
  /* 1頭1行: 騎手は馬名の横、近走は1行目(日付 場 距離馬場 クラス)と着順だけ、成績欄は省く */
  body.compact .pp-t td:nth-child(7), body.compact .pp-t th:nth-child(7), body.compact .pp-t col:nth-child(7) {{ display:none; }}
  body.compact .hs:nth-of-type(n+2), body.compact .plc, body.compact .ai .sm, body.compact .rl > span:not(:first-child) {{ display:none; }}
  body.compact .nm {{ display:inline; }} body.compact .horse .hs {{ display:inline; margin-left:4px; }}
  body.compact .od .popn, body.compact .od .b {{ display:inline; }} body.compact .od .popn small {{ display:none; }} body.compact .od .b {{ margin-left:3px; }}
  body.compact .ai .vt {{ display:none; }} body.compact .fin {{ font-size:11px !important; min-width:15px; padding:0 1px; }}
  @media (max-width:640px) {{ .pp-sum {{ grid-template-columns:1fr 1fr; }} .ai3 {{ grid-column:1/-1; }} }}
  @media print {{
    @page {{ size:A4 landscape; margin:6mm; }}
    body {{ background:#fff; font-size:10px; }} header, .pp-controls {{ display:none; }} main {{ padding:0; max-width:none; }}
    .pp-card {{ overflow:visible; }} .pp-t {{ min-width:0; }}
    .pp-card.off {{ display:none !important; }}
    .pp-venue {{ break-before:page; }} .pp-venue:first-of-type {{ break-before:auto; }}
    .pp-venue:has(.pp-grid:not(.two)) > h2 {{ display:none; }}
    .pp-grid:not(.two) .pp-card {{ break-after:page; break-inside:auto; padding:0; margin:0; border:0; }}
    .pp-t td {{ padding:1px 3px; line-height:1.22; }} .pp-t th {{ font-size:9px; padding:1px 2px; }}
    .run {{ font-size:8.6px; }} .fin {{ font-size:13px; min-width:19px; }} .nm {{ font-size:12.5px; }} .hs, .stat {{ font-size:9px; }}
    .ub {{ font-size:14px; }} .mk {{ font-size:15px; }} .od .popn {{ font-size:13px; }} .ai b {{ font-size:12.5px; }}
    .pp-t tr {{ break-inside:avoid; }} .pp-sum {{ margin:3px 0; }} .box {{ padding:1px 6px; }} .box b {{ font-size:1.05rem; }}
    .pp-head {{ padding-bottom:2px; }} .rno b {{ font-size:1.35rem; }} .pp-cond {{ margin:2px 0; }} .tag {{ font-size:9.5px; padding:0 5px; }}
    .pp-sum {{ grid-template-columns:0.8fr 1.6fr 3fr; }} .box i {{ display:inline; margin-right:6px; }} .box small {{ display:inline; margin-left:6px; }} .ai3 i {{ flex-basis:auto; }}
    .pp-t td {{ line-height:1.15; }} .bl, .plc {{ font-size:8px; }} .pp-pop {{ font-size:9px; padding:1px 6px; margin:1px 0 2px; }}
    .pp-card.big .pp-t td {{ padding:0 2px; line-height:1.07; }} .pp-card.big .run {{ font-size:8px; }} .pp-card.big .nm {{ font-size:11px; }}
    .pp-card.big .fin {{ font-size:12px; }}
    .pp-card.huge .pp-t td {{ line-height:1.0; }} .pp-card.huge .run {{ font-size:7.6px; }} .pp-card.huge .hs, .pp-card.huge .bl, .pp-card.huge .stat, .pp-card.huge .plc {{ font-size:7.4px; }}
    body.compact .pp-grid .pp-card {{ break-after:auto !important; break-inside:avoid; margin:0 0 3mm; padding:0; border:0; border-bottom:1px dashed #999 !important; }}
    body.compact .pp-venue {{ break-before:auto; }} body.compact .pp-venue > h2 {{ display:none; }}
    body.compact .pp-t td {{ padding:0 2px; line-height:1.05; }} body.compact .run {{ font-size:7.4px; }} body.compact .nm {{ font-size:10px; }}
    body.compact .hs, body.compact .stat, body.compact .plc {{ font-size:7.2px; }} body.compact .pp-sum {{ margin:1px 0; }}
    body.compact .pp-cond, body.compact .pp-sum {{ font-size:8.5px; }} body.compact .rname {{ font-size:1rem; }}
    .pp-card.huge .nm {{ font-size:10.5px; }} .pp-card.huge .pp-sum {{ margin:1px 0; }} .pp-card.huge .box b {{ font-size:.95rem; }} .pp-card.big .pp-sum {{ margin:2px 0; }} .pp-card.big .pp-cond {{ margin:1px 0; }} .nm {{ font-size:11.5px; }} .hs, .stat {{ font-size:8.5px; }} .run {{ font-size:8.3px; }}
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
    <div class="pp-dl" id="ppPdf"></div>
    <div class="pp-row">
      <label>レイアウト <select id="ppLay"><option value="1">1枚1レース(全部入り・A4横)</option><option value="2">2レースで1枚(近2走まで・A4縦)</option></select></label>
      <span class="pp-count" id="ppCount"></span>
      <button type="button" class="pp-print" id="ppPrint">選んだレースを印刷 / PDFに保存</button>
    </div>
    <p class="pp-hint">レース番号をタップで 選ぶ/外す(下の数字は3連複の万馬券率。赤=荒れそう・緑=堅そう)。選んだレースだけ下に表示・印刷されます。
    印刷画面で「PDFとして保存」を選ぶとPDFになります(iPhoneは 共有 → プリント → プレビューをピンチアウト → 共有でPDF保存)。
    LINE・X・Gmail などのアプリ内で開くと印刷ボタンが動かないので、上の「できあがりPDF」を使うか、Safari/Chromeで開いてください。<br>
    紙はA4横。表の見方: 印=AIの評価順、AI 1着%(赤い棒)・複勝%=AIの3着内予測、込%=オッズ込みの1着率(実際に勝ちそうな確率の目安)、期待値=AI1着率×単勝(1以上は赤)、妙味=AIと人気のズレ、
    成績=通算・同距離帯の3着内率(50%以上は赤)・間隔・騎手/厩舎の勝率。<br>
    近4走: 左の大きい数字=着順(1着は赤地・2〜3着はピンク)、1行目=日付 場 クラス 距離馬場、2行目=頭数・馬番・人気(1人気は赤)・騎手斤量、
    3行目=タイム(勝ち馬との差、勝った時は2着との差を赤で)・上り3F(①〜③=メンバー中の順位)・通過順・馬体重。メモ欄は手書き用です。</p>
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
  var PS=document.createElement('style'); document.head.appendChild(PS);
  function apply(){{
    var two=document.getElementById('ppLay').value==='2', n=0;
    document.body.classList.toggle('compact',two);
    PS.textContent = two ? '@page {{ size:A4 portrait; margin:6mm; }}' : '@page {{ size:A4 landscape; margin:6mm; }}';
    boxes.forEach(function(b){{ var c=card(b.value); if(!c) return; c.style.display=b.checked?'':'none'; c.classList.toggle('off',!b.checked); if(b.checked) n++; }});
    document.querySelectorAll('.pp-venue').forEach(function(v){{
      var any=Array.prototype.some.call(v.querySelectorAll('.pp-card'),function(c){{ return c.style.display!=='none'; }});
      v.style.display=any?'':'none';
    }});
    document.getElementById('ppCount').textContent=n+'レース選択中';
    document.getElementById('ppPrint').disabled=(n===0);
    save();
  }}
  try{{ var off=JSON.parse(localStorage.getItem(KEY)||'[]'); boxes.forEach(function(b){{ if(off.indexOf(b.value)>=0) b.checked=false; }}); }}catch(e){{}}
  // URLで指定(できあがりPDFを作るとき用): ?sel=all|bet|are|v:競馬場 &lay=2
  var qs=new URLSearchParams(location.search), sel=qs.get('sel');
  if(qs.get('lay')) document.getElementById('ppLay').value=qs.get('lay');
  if(sel){{ boxes.forEach(function(b){{ var c=card(b.value); if(!c) return; var f=' '+(c.dataset.flags||'')+' ';
    b.checked = sel==='all' ? true : sel.indexOf('v:')===0 ? c.dataset.venue===sel.slice(2) : f.indexOf(' '+sel+' ')>=0; }}); KEY=KEY+'-url'; }}
  // できあがりPDF(GitHubで作った物)のリンク
  fetch('pdf/index.json?t='+Date.now()).then(function(r){{ return r.ok?r.json():null; }}).then(function(j){{
    if(!j||!j.files||!j.files.length) return;
    document.getElementById('ppPdf').innerHTML='<b>できあがりPDF</b><small>('+j.made+' 作成)</small><br>'+j.files.map(function(f,i){{
      return '<a class="'+(i>1?'sub':'')+'" href="pdf/'+encodeURIComponent(f.file)+'?t='+Date.now()+'" download>'+f.label+'('+f.pages+'枚)</a>'; }}).join('');
  }}).catch(function(){{}});
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
