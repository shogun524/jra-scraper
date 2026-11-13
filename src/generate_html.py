"""
JRA競馬AI - HTMLダッシュボード生成(v2)
data/predictions.csv を読み込み、競馬場ごとのタブ形式で docs/index.html を生成する。

デザイン方針:
- 汎用的な「黒×青のAIダッシュボード」ではなく、競馬新聞・出馬表・トートボードの質感を土台にする
- 枠番の色は実際のJRA公式配色(白/黒/赤/青/黄/緑/橙/桃)をそのまま使う
- レースIDではなく、競馬場名・R番号・レース名・発走時刻を主役にする
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_bets import RULES, evaluate_all  # noqa: E402
from ev_board import board, load_calibration, load_odds_files  # noqa: E402
from manbaken import load_params as load_manbaken, market_view, market_view_trifecta  # noqa: E402

MANBAKEN = load_manbaken()


def _load_trifecta(out_path, race_date):
    """odds_snapshot.py が残した3連複オッズ(レースごとの最新値)"""
    p = os.path.join(os.path.dirname(os.path.abspath(out_path)), "odds_history", f"trio_latest_{race_date}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def manbaken_html(g):
    """3連複の万馬券率(1万円以上の確率)と配当の目安。3連複オッズが無く単勝オッズも全頭そろっていなければ出さない"""
    if "odds_win" not in g.columns:
        return ""
    o = pd.to_numeric(g["odds_win"], errors="coerce")
    o = o[o > 0]
    rid = str(g["race_id"].iloc[0])
    v = market_view_trifecta(TRIFECTA[rid]["odds"]) if rid in TRIFECTA else None   # 3連複オッズがあれば直接(範囲が狭い)
    # 無ければ単勝オッズから推定。取消馬はオッズが無いので除く。発売前(ほとんど無い)はオッズ待ち
    if v is None:
        v = market_view(o.tolist(), MANBAKEN) if len(o) >= 5 and len(o) >= 0.8 * len(g) else None
    if not v:
        return '<p class="manbaken muted">3連複の万馬券率: オッズ待ち</p>'
    lvl = "hi" if v["man"] >= 0.5 else ("lo" if v["man"] < 0.2 else "")
    return (f'<p class="manbaken">3連複の万馬券率 <b class="{lvl}">{v["man"]:.0%}</b>'
            f'<span class="sep">・</span>3連複の配当の目安 <b>{v["q50"]:,.0f}円</b>'
            f'<small>(半分くらいのレースは {v["q25"]:,.0f}〜{v["q75"]:,.0f}円'
            f'{"・3連複オッズから" if v.get("src") == "3連複" else "・単勝オッズから推定"})</small></p>')

IN_CSV = sys.argv[1] if len(sys.argv) > 1 else "data/predictions.csv"
OUT_HTML = sys.argv[2] if len(sys.argv) > 2 else "docs/index.html"

TRACK_NAMES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
# JRA公式の枠番配色(1〜8枠)
WAKU_COLORS = {
    1: ("#FFFFFF", "#2A2118"), 2: ("#1A1A1A", "#FFFFFF"), 3: ("#D0342C", "#FFFFFF"),
    4: ("#1E5FA8", "#FFFFFF"), 5: ("#EFC94C", "#2A2118"), 6: ("#2E8B57", "#FFFFFF"),
    7: ("#E8792B", "#FFFFFF"), 8: ("#E8A0BC", "#2A2118"),
}

df = pd.read_csv(IN_CSV)
if "track_code" in df.columns:
    df["track_code"] = df["track_code"].astype(str).str.zfill(2)
    df["track_name"] = df.get("track_name", df["track_code"].map(TRACK_NAMES))
else:
    df["track_name"] = "不明"
df["race_number"] = df.get("race_number", df["race_id"].astype(str).str[-2:].astype(int))
_now_jst = datetime.now(timezone(timedelta(hours=9)))
generated_at = _now_jst.strftime("%Y-%m-%d %H:%M")

# このページがどの日のレースを対象にしているかを見出しに出す。
# 金曜夜に翌日(土曜)の予測を先に作れるようにしたため、
# 「今見ているのが今日の予測か、翌日以降の予測か」が一目で分かるようにする。
_dates = sorted(df["race_date"].astype(str).unique()) if "race_date" in df.columns else []
if _dates:
    _d = _dates[0]
    _wd = "月火水木金土日"[datetime.strptime(_d, "%Y-%m-%d").weekday()]
    date_label = f"{_d}({_wd})"
    if _d > _now_jst.strftime("%Y-%m-%d"):
        date_label += " ＝事前予測(オッズは前日のため参考値)"
else:
    date_label = "日付不明"
TRIFECTA = _load_trifecta(OUT_HTML, _dates[0]) if _dates else {}
# オッズが取れているレースの割合(直前実行で更新されるほど高くなる)
if "odds_win" in df.columns:
    n_with_odds = df.groupby("race_id")["odds_win"].apply(lambda s: s.notna().any()).sum()
    n_races_total = df["race_id"].nunique()
    odds_status = f"オッズ取得 {n_with_odds}/{n_races_total}レース"
else:
    odds_status = "オッズ未取得"


def waku_badge(waku):
    if pd.isna(waku):
        return '<span class="waku waku-none">-</span>'
    w = int(waku)
    bg, fg = WAKU_COLORS.get(w, ("#999", "#fff"))
    border = "border:1px solid #C8B98A;" if w == 1 else ""
    return f'<span class="waku" style="background:{bg};color:{fg};{border}">{w}</span>'


def ai_bet_status(g):
    """AI買い目(本命・準)の判定リスト。race_class列がない古い予測では判定しない"""
    if "race_class" not in g.columns:
        return []
    return evaluate_all(g)


def ai_bet_box(results):
    html = ""
    for e in results:
        if e["status"] not in ("対象", "オッズ待ち"):
            continue
        sub = " aibet-sub" if e["rule"].key == "sub" else ""
        wait = " aibet-wait" if e["status"] != "対象" else ""
        html += f"""
      <div class="aibet{sub}">
        <span class="aibet-tag{wait}">{e["rule"].label} {e["status"]}</span> <span class="aibet-reason">{e["reason"]}</span>
        <div class="aibet-body">{e["desc"]}</div>
        <div class="aibet-combos">{" / ".join(e["tickets"])}</div>
      </div>"""
    return html


# ---- EVボード(単勝・馬連・ワイド) ----
_site_dir = os.path.dirname(os.path.abspath(IN_CSV))
_race_date = str(df["race_date"].iloc[0]) if "race_date" in df.columns and len(df) else ""
_latest, _hist = load_odds_files(_site_dir, _race_date) if _race_date else (None, None)
_cal = load_calibration()
try:
    EV = board(df.assign(race_id=df["race_id"].astype(str)),
               _latest[_latest["bet"] != "単勝"] if _latest is not None else None, _hist, _cal)
except Exception as _e:  # EVが計算できなくてもページは出す
    print(f"EVの計算に失敗: {_e}")
    EV = pd.DataFrame()


def fmt_odds(r):
    if r["bet"] == "ワイド" and pd.notna(r.get("odds_high")):
        return f'{r["odds"]:.1f}〜{r["odds_high"]:.1f}'
    return f'{r["odds"]:.1f}'


def ev_box(race_id):
    if EV.empty:
        return ""
    e = EV[EV["race_id"] == str(race_id)]
    if e.empty:
        return ""
    parts = []
    for bet, k in (("単勝", 3), ("馬連", 3), ("ワイド", 3)):
        t = e[(e["bet"] == bet) & (e["ev"] >= 1.0)].sort_values("ev", ascending=False).head(k)
        if t.empty:
            continue
        cells = ""
        for _, r in t.iterrows():
            cls = "ev-good" if r["ev"] >= 1.0 else ("ev-mid" if r["ev"] >= 0.9 else "ev-low")
            extra = ""
            if bet == "単勝":
                if pd.notna(r.get("move")) and abs(r["move"]) >= 0.1:
                    arrow = "↓買われ" if r["move"] > 0 else "↑売られ"
                    extra += f' <span class="mv {"mv-in" if r["move"] > 0 else "mv-out"}">{r["odds_first"]:.1f}→{r["odds"]:.1f} {arrow}</span>'
                if pd.notna(r.get("weight_diff")):
                    extra += f' <span class="wd">体重{int(r["weight_diff"]):+d}</span>'
            cells += (f'<li><b>{r["combo"]}</b> {fmt_odds(r)}倍 ・ 確率{r["p"] * 100:.1f}% ・ '
                      f'<span class="badge {cls}">EV {r["ev"]:.2f}</span>{extra}</li>')
        parts.append(f'<div class="evcol"><h4>{bet}</h4><ul>{cells}</ul></div>')
    if not parts:
        return ""
    return f'<div class="evbox"><div class="evhead">EVボード(AI×オッズの歪み×オッズの動き)</div><div class="evcols">{"".join(parts)}</div></div>'


def race_card(race_id, g):
    g = g.sort_values("pred_win_rank")
    first = g.iloc[0]
    aibet_html = ai_bet_box(ai_bet_status(g)) + ev_box(race_id)
    race_name = first.get("race_name")
    race_name = race_name if isinstance(race_name, str) and race_name.strip() else ""
    post_time = first.get("post_time")
    post_time_html = f'<span class="post-time">発走 {post_time}</span>' if isinstance(post_time, str) and post_time else ""

    rows = ""
    for _, r in g.iterrows():
        ev = r.get("expected_value", None)
        ev_badge = ""
        if pd.notnull(ev):
            cls = "ev-good" if ev >= 1.1 else ("ev-mid" if ev >= 0.9 else "ev-low")
            ev_badge = f'<span class="badge {cls}">{ev:.2f}</span>'
        else:
            ev_badge = '<span class="na">オッズ待ち</span>'
        odds = f'{r["odds_win"]:.1f}倍' if pd.notnull(r.get("odds_win")) else "-"
        if not EV.empty and pd.notnull(r.get("umaban")):
            _m = EV[(EV["race_id"] == str(race_id)) & (EV["bet"] == "単勝") & (EV["combo"] == str(int(r["umaban"])))]
            if not _m.empty and pd.notna(_m["move"].iloc[0]) and abs(_m["move"].iloc[0]) >= 0.1:
                odds += ' <span class="mv mv-in">↓</span>' if _m["move"].iloc[0] > 0 else ' <span class="mv mv-out">↑</span>'
        hw, wdiff = r.get("horse_weight"), r.get("weight_diff")
        weight_html = (f'{int(hw)}<small>({int(wdiff):+d})</small>' if pd.notnull(hw) and pd.notnull(wdiff)
                       else (f"{int(hw)}" if pd.notnull(hw) else "-"))
        umaban = int(r["umaban"]) if pd.notnull(r.get("umaban")) else "-"
        rank_cls = "top-pick" if r["pred_win_rank"] == 1 else ""

        # 安定度: 3連対率÷1着率。大きいほど「勝ち切れないが崩れにくい」
        stab = r.get("stability")
        if pd.notnull(stab):
            if stab >= 4.0:
                stab_html = f'<span class="tag tag-place">複勝向き {stab:.1f}</span>'
            elif stab <= 2.5:
                stab_html = f'<span class="tag tag-win">一発型 {stab:.1f}</span>'
            else:
                stab_html = f'<span class="tag">{stab:.1f}</span>'
        else:
            stab_html = "-"

        # 市場との乖離: 人気順位 - AI評価順位。プラスなら市場より高評価=妙味
        rd = r.get("rank_diff")
        if pd.notnull(rd):
            rd = int(rd)
            if rd >= 3:
                rd_html = f'<span class="rd rd-good">+{rd}</span>'
            elif rd <= -3:
                rd_html = f'<span class="rd rd-bad">{rd}</span>'
            else:
                rd_html = f'<span class="rd">{rd:+d}</span>'
        else:
            rd_html = "-"

        # AI指数(レース内トップを100とした相対スコア)
        ai = r.get("ai_score")
        ai_html = f'<span class="ai">{int(ai)}</span>' if pd.notnull(ai) else "-"

        # 印(◎○▲△)
        mk = r.get("mark")
        mk = mk if isinstance(mk, str) else ""
        mk_html = f'<span class="mark">{mk}</span>' if mk else ""

        # 妙味判定
        vt = r.get("value_tag")
        vt = vt if isinstance(vt, str) else ""
        VT_CLS = {"妙味大": "vt-big", "妙味あり": "vt-ok", "妥当": "vt-mid", "過剰人気": "vt-bad"}
        vt_html = f'<span class="vt {VT_CLS.get(vt, "")}">{vt}</span>' if vt else "-"

        # 枠順評価(コース実データ、平均=1.00)
        wf = r.get("waku_fit")
        if pd.notnull(wf):
            wf_cls = "cf-good" if wf >= 1.08 else ("cf-bad" if wf <= 0.93 else "")
            wf_html = f'<span class="cf {wf_cls}">{wf:.2f}</span>'
        else:
            wf_html = "-"

        gap = r.get("gap_from_top")
        gap_html = "—" if (pd.notnull(gap) and gap == 0) else (f"-{gap:.1f}pt" if pd.notnull(gap) else "-")

        rows += f"""
        <tr class="{rank_cls}">
          <td class="rank">{int(r['pred_win_rank'])}</td>
          <td>{waku_badge(r.get('waku'))}</td>
          <td>{umaban}</td>
          <td class="horse">{r['horse']}</td>
          <td>{r['jockey']}</td>
          <td class="pct">{r['pred_win_norm']*100:.1f}%</td>
          <td class="pct">{r['pred_top3_norm']*100:.1f}%</td>
          <td>{mk_html}</td>
          <td>{ai_html}</td>
          <td class="gap">{gap_html}</td>
          <td>{stab_html}</td>
          <td>{wf_html}</td>
          <td>{weight_html}</td>
          <td>{odds}</td>
          <td>{rd_html}</td>
          <td>{ev_badge}</td>
          <td>{vt_html}</td>
        </tr>"""

    return f"""
    <article class="race-card">
      <header class="race-card-head">
        <span class="race-num">{int(first['race_number'])}<small>R</small></span>
        <div class="race-title">
          <h2>{race_name}</h2>
          <p class="race-sub">{first.get('track_name','')} {post_time_html}</p>
          {manbaken_html(g)}
        </div>
      </header>
      {aibet_html}
      <div class="table-scroll">
      <table>
        <thead>
          <tr><th>予想</th><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th>
              <th>1着率</th><th>3連対率</th><th>印</th><th>AI<br>指数</th>
              <th>1位との差</th><th>安定度</th><th>枠順<br>評価</th>
              <th>馬体重</th><th>単勝</th><th>人気との<br>乖離</th><th>期待値</th><th>妙味</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      </div>
    </article>"""


# 競馬場ごとにグループ化(タブ) -> さらにレース番号ごとに二段目のタブ
tracks = sorted(df["track_name"].dropna().unique(), key=lambda t: df[df.track_name == t]["track_code"].iloc[0])
tab_buttons = ""
tab_panels = ""
for i, track in enumerate(tracks):
    tdf = df[df.track_name == track]
    active = "active" if i == 0 else ""
    n_races = tdf["race_id"].nunique()
    tab_buttons += f'<button class="tab-btn {active}" data-tab="tab-{i}">{track}<span class="tab-count">{n_races}R</span></button>'

    race_groups = sorted(tdf.groupby("race_id"), key=lambda kv: kv[1]["race_number"].iloc[0])
    race_tab_buttons = ""
    race_panels = ""
    for j, (rid, g) in enumerate(race_groups):
        r_active = "active" if j == 0 else ""
        race_num = int(g["race_number"].iloc[0])
        st = ai_bet_status(g.sort_values("pred_win_rank"))
        star = "★" if any(e["status"] == "対象" and e["rule"].key == "main" for e in st) else (
            "☆" if any(e["status"] == "対象" for e in st) else "")
        race_tab_buttons += f'<button class="race-tab-btn {r_active}" data-race="race-{i}-{j}">{star}{race_num}R</button>'
        race_panels += f'<div class="race-panel {r_active}" id="race-{i}-{j}">{race_card(rid, g)}</div>'

    tab_panels += f"""<section class="tab-panel {active}" id="tab-{i}">
      <nav class="race-tabs">{race_tab_buttons}</nav>
      {race_panels}
    </section>"""

# 今日のAI買い目の一覧(ページ上部)
_targets = {r.key: [] for r in RULES}
for rid, g in df.groupby("race_id"):
    for e in ai_bet_status(g.sort_values("pred_win_rank")):
        if e["status"] in ("対象", "オッズ待ち"):
            f0 = g.iloc[0]
            _targets[e["rule"].key].append((str(f0.get("post_time", "")), f0.get("track_name", ""),
                                            int(f0["race_number"]), f0.get("race_name", ""), e))
aibet_summary = ""
if "race_class" in df.columns:
    blocks = ""
    for rule in RULES:
        items = sorted(_targets[rule.key], key=lambda x: x[0])
        lis = "".join(
            f'<li><span class="aibet-tag{"" if e["status"] == "対象" else " aibet-wait"}">{e["status"]}</span> '
            f'{tn}{rn}R {nm if isinstance(nm, str) else ""} {pt} ― {e["desc"]}<small>({e["reason"]})</small></li>'
            for pt, tn, rn, nm, e in items) or "<li>今日は対象レースなし</li>"
        blocks += f"""<h3 class="aibet-h3{' aibet-h3-sub' if rule.key == 'sub' else ''}">{rule.label}</h3>
      <p class="aibet-note">{rule.name}。{rule.backtest}。</p><ul>{lis}</ul>"""
    aibet_summary = f"""<section class="aibet-summary"><h2>今日のAI買い目</h2>{blocks}
      <p class="aibet-note">オッズ・人気は発走直前で変わります。条件の境目にいる馬は、買う前に確認してください。</p></section>"""

# EV上位(全レース)
ev_summary = ""
if not EV.empty:
    _meta = df.assign(race_id=df["race_id"].astype(str)).groupby("race_id").agg(
        tn=("track_name", "first"), rn=("race_number", "first"), pt=("post_time", "first"))
    top = EV[EV["ev"] >= 1.0].sort_values("ev", ascending=False).head(15)
    title_note = "EV 1.0以上の買い目"
    if top.empty:
        top = EV.sort_values("ev", ascending=False).head(10)
        title_note = "EV 1.0以上はなし。EVが高い順"
    lis = ""
    for _, r in top.iterrows():
        m = _meta.loc[r["race_id"]]
        mv = ""
        if r["bet"] == "単勝" and pd.notna(r.get("move")) and abs(r["move"]) >= 0.1:
            mv = f' <span class="mv {"mv-in" if r["move"] > 0 else "mv-out"}">{r["odds_first"]:.1f}→{r["odds"]:.1f}</span>'
        lis += (f'<li>{m.tn}{int(m.rn)}R {m.pt} ・ <b>{r["bet"]} {r["combo"]}</b> {fmt_odds(r)}倍 ・ '
                f'確率{r["p"] * 100:.1f}% ・ <span class="badge {"ev-good" if r["ev"] >= 1 else "ev-mid"}">EV {r["ev"]:.2f}</span>{mv}</li>')
    _how = (f'<details class="ev-how"><summary>EVの出し方</summary><p class="aibet-note">確率 = AIの1着率^{_cal["a"]:.2f} × '
            f'市場の確率^{_cal["b"]:.2f} × オッズの動き(係数{_cal["c_move"]:.2f})を正規化。係数は過去の結果から毎週推定'
            f'(今のところAIの重みはほぼ0)。EVの実績は成績ページで採点しています。</p></details>')
    if title_note.startswith("EV 1.0以上の"):
        ev_summary = f"""<section class="aibet-summary ev-summary"><h2>EVボード <small>({title_note})</small></h2>
      <ul>{lis}</ul>{_how}</section>"""
    else:
        ev_summary = f"""<details class="aibet-summary ev-summary"><summary><b>EVボード</b> <small>今日はEV1.0以上の買い目なし(上位を見る)</small></summary>
      <ul>{lis}</ul>{_how}</details>"""

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JRA週末AI予想</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600;800&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --turf: #1F4D3A; --turf-dark: #163A2B; --paper: #F6F1E4; --paper-line: #E4DAC0;
    --ink: #2A2118; --ink-soft: #6B5F4D; --gold: #B8862B; --gold-soft: #F0D896;
    --good: #2E7D46; --mid: #B8862B; --low: #B0433A;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--paper); color: var(--ink);
    font-family: "Noto Sans JP", sans-serif; padding-bottom: 48px;
  }}
  h1, h2, .race-num {{ font-family: "Shippori Mincho", serif; }}

  header.top {{
    background: var(--turf); color: #fff; padding: 20px 20px 0;
  }}
  header.top .inner {{ max-width: 900px; margin: 0 auto; }}
  header.top h1 {{ margin: 0 0 4px; font-size: 1.5rem; font-weight: 800; letter-spacing: .02em; }}
  header.top p {{ margin: 0 0 16px; font-size: .8rem; color: #CFE3D8; }}

  nav.tabs {{
    max-width: 900px; margin: 0 auto; display: flex; gap: 4px; overflow-x: auto;
    padding: 0 20px; scrollbar-width: none;
  }}
  nav.tabs::-webkit-scrollbar {{ display: none; }}
  .tab-btn {{
    flex: 0 0 auto; background: transparent; border: none; cursor: pointer;
    color: #CFE3D8; font-family: "Noto Sans JP", sans-serif; font-weight: 700;
    font-size: .9rem; padding: 10px 16px; border-radius: 8px 8px 0 0;
    display: flex; align-items: baseline; gap: 6px;
  }}
  .tab-btn .tab-count {{ font-size: .7rem; font-weight: 500; color: #9BB8A9; }}
  .tab-btn.active {{ background: var(--paper); color: var(--ink); }}
  .tab-btn.active .tab-count {{ color: var(--ink-soft); }}

  main {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}

  nav.race-tabs {{
    display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px;
  }}
  .race-tab-btn {{
    background: #fff; border: 1px solid var(--paper-line); color: var(--ink-soft);
    font-family: "Noto Sans JP", sans-serif; font-weight: 700; font-size: .82rem;
    padding: 6px 12px; border-radius: 999px; cursor: pointer;
  }}
  .race-tab-btn.active {{ background: var(--turf); border-color: var(--turf); color: #fff; }}
  .race-panel {{ display: none; }}
  .race-panel.active {{ display: block; }}

  .race-card {{
    background: #fff; border: 1px solid var(--paper-line); border-radius: 6px;
    margin-bottom: 16px; overflow: hidden;
  }}
  .race-card-head {{
    display: flex; align-items: center; gap: 14px;
    padding: 14px 16px; border-bottom: 2px solid var(--turf);
    background: linear-gradient(180deg, #fff, #FBF8F0);
  }}
  .race-num {{
    font-size: 1.8rem; font-weight: 800; color: var(--turf); line-height: 1;
    min-width: 2ch; text-align: center;
  }}
  .race-num small {{ font-size: .9rem; font-weight: 600; margin-left: 1px; }}
  .race-title h2 {{ margin: 0; font-size: 1.05rem; font-weight: 800; }}
  .race-sub {{ margin: 3px 0 0; font-size: .78rem; color: var(--ink-soft); }}
  .post-time {{ margin-left: 8px; color: var(--gold); font-weight: 700; }}
  .manbaken {{ margin: 2px 0 0; font-size: .82rem; }}
  details.ev-summary > summary, details.ev-how > summary {{ cursor: pointer; }}
  details.ev-how {{ margin-top: 6px; font-size: .8rem; }}
  .manbaken b {{ font-variant-numeric: tabular-nums; }}
  .manbaken b.hi {{ color: #B3261E; }} .manbaken b.lo {{ color: #2F6B4F; }}
  .manbaken small {{ margin-left: 4px; opacity: .75; }} .manbaken .sep {{ margin: 0 4px; opacity: .5; }}
  .manbaken.muted {{ opacity: .6; }}

  table {{ border-collapse: collapse; width: 100%; font-size: .82rem; }}
  .table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; position: relative; }}
  .race-card {{ position: relative; }}
  .race-card::after {{
    content: ""; position: absolute; top: 54px; bottom: 0; right: 0; width: 18px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.9));
    pointer-events: none; display: none;
  }}
  @media (max-width: 620px) {{
    .race-card::after {{ display: block; }}
  }}
  th {{
    text-align: left; font-weight: 500; font-size: .72rem; color: var(--ink-soft);
    padding: 8px 10px; border-bottom: 1px solid var(--paper-line); white-space: nowrap;
  }}
  td {{ padding: 8px 10px; border-bottom: 1px solid var(--paper-line); white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.top-pick {{ background: #FBF3DC; }}
  .rank {{ font-weight: 800; color: var(--turf); text-align: center; }}
  .horse {{ font-weight: 700; }}
  .pct {{ font-variant-numeric: tabular-nums; }}
  .waku {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 4px; font-weight: 700; font-size: .78rem;
  }}
  .waku-none {{ background: #eee; color: #999; }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700; }}
  .ev-good {{ background: #E4F1E7; color: var(--good); }}
  .ev-mid  {{ background: #F5EAD0; color: var(--mid); }}
  .ev-low  {{ background: #F5DEDC; color: var(--low); }}
  .na {{ color: #A9A093; font-size: .72rem; }}
  .gap {{ color: var(--ink-soft); font-variant-numeric: tabular-nums; }}
  .style {{ font-weight: 600; }}
  .tag {{ padding: 2px 7px; border-radius: 4px; font-size: .72rem; font-weight: 600; background: #F0EBDD; color: var(--ink-soft); }}
  .tag-place {{ background: #E4F1E7; color: var(--good); }}
  .tag-win {{ background: #F5EAD0; color: var(--mid); }}
  .conf {{ display: inline-block; min-width: 26px; text-align: center; padding: 2px 5px; border-radius: 4px; font-size: .72rem; font-weight: 700; }}
  .conf-high {{ background: #E4F1E7; color: var(--good); }}
  .conf-mid {{ background: #F5EAD0; color: var(--mid); }}
  .conf-low {{ background: #EFEAE0; color: #948A7B; }}
  .mark {{ font-size: 1.05rem; font-weight: 700; color: var(--turf); }}
  .ai {{ font-variant-numeric: tabular-nums; font-weight: 700; color: var(--turf); }}
  .vt {{ padding: 2px 7px; border-radius: 4px; font-size: .72rem; font-weight: 700; white-space: nowrap; }}
  .vt-big {{ background: #DCEFE2; color: #1F7A46; }}
  .vt-ok {{ background: #E9F2E4; color: #4A7A2E; }}
  .vt-mid {{ background: #F0EBDD; color: #6B5F4D; }}
  .vt-bad {{ background: #F5DEDC; color: #B0433A; }}
  .rd {{ font-variant-numeric: tabular-nums; font-weight: 700; }}
  .rd-good {{ color: var(--good); }}
  .rd-bad {{ color: #B0433A; }}
  .muted {{ color: #A9A093; }}
  .wd-warn {{ color: #B0433A; font-weight: 700; }}
  .cf {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  .cf-good {{ color: var(--good); }}
  .cf-bad {{ color: #B0433A; }}

  .aibet {{ margin: 0 0 12px; padding: 10px 12px; background: #FBF6E8; border: 1px solid var(--gold-soft);
    border-left: 4px solid var(--gold); border-radius: 6px; font-size: .86rem; }}
  .aibet-tag {{ display: inline-block; background: var(--gold); color: #fff; font-weight: 700; font-size: .72rem;
    padding: 2px 8px; border-radius: 999px; }}
  .aibet-wait {{ background: #A9A093; }}
  .aibet-reason {{ color: var(--ink-soft); font-size: .76rem; margin-left: 4px; }}
  .aibet-body {{ margin-top: 6px; font-size: .95rem; }}
  .aibet-combos {{ margin-top: 2px; color: var(--ink-soft); font-size: .78rem; font-variant-numeric: tabular-nums; }}
  .aibet-summary {{ background: #fff; border: 1px solid var(--paper-line); border-top: 3px solid var(--gold);
    border-radius: 6px; padding: 12px 16px; margin-bottom: 16px; }}
  .aibet-summary h2 {{ margin: 0 0 4px; font-size: 1.05rem; color: var(--turf); }}
  .aibet-summary ul {{ margin: 8px 0 0; padding-left: 0; list-style: none; font-size: .86rem; line-height: 1.9; }}
  .aibet-summary small {{ color: var(--ink-soft); margin-left: 4px; }}
  .aibet-sub {{ border-left-color: #7A8B9A; background: #F3F5F7; }}
  .aibet-h3 {{ margin: 10px 0 2px; font-size: .9rem; color: var(--gold); }}
  .aibet-h3-sub {{ color: #5E6E7D; }}
  .evbox {{ margin: 0 0 12px; padding: 10px 12px; background: #fff; border: 1px solid var(--paper-line); border-radius: 6px; }}
  .evhead {{ font-size: .78rem; font-weight: 700; color: var(--turf); margin-bottom: 4px; }}
  .evcols {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 8px; }}
  .evcol h4 {{ margin: 0 0 2px; font-size: .78rem; color: var(--ink-soft); }}
  .evcol ul, .ev-summary ul {{ margin: 0; padding-left: 0; list-style: none; font-size: .8rem; line-height: 1.8; }}
  .ev-summary h2 small {{ font-size: .72rem; color: var(--ink-soft); font-weight: 500; }}
  .mv {{ font-size: .72rem; font-weight: 700; }}
  .mv-in {{ color: #B0433A; }}
  .mv-out {{ color: #1E5FA8; }}
  .wd {{ font-size: .72rem; color: var(--ink-soft); }}
  .aibet-note {{ margin: 0; font-size: .76rem; color: var(--ink-soft); line-height: 1.6; }}

  footer {{
    max-width: 900px; margin: 16px auto 0; padding: 0 20px;
    color: var(--ink-soft); font-size: .74rem; line-height: 1.7;
  }}

  @media (max-width: 480px) {{
    table {{ font-size: .74rem; min-width: 840px; }}
    th, td {{ padding: 7px 6px; }}
  }}
</style>
</head>
<body>
<header class="top">
  <div class="inner">
    <h1>JRA週末AI予想</h1>
    <p><b>{date_label}</b> のレース ・ 最終更新 {generated_at} (JST) ・ {odds_status} ・ <a href="courses.html" style="color:#F0D896;">競馬場ガイド</a> ・ <a href="performance.html" style="color:#F0D896;">予測の成績</a> ・ <a href="picks.html" style="color:#F0D896;font-weight:700;">買うべきレース</a> ・ <a href="drops.html" style="color:#F0D896;font-weight:700;">オッズ急落</a> ・ <a href="print.html" style="color:#F0D896;">印刷用</a></p>
  </div>
  <nav class="tabs">{tab_buttons}</nav>
</header>
<main>{aibet_summary}{ev_summary}{tab_panels}</main>
<footer>
  <p><strong>各項目の見方</strong></p>
  <p>
    <b>1着率 / 3連対率</b>: モデルの推定確率。レース内で合計が100% / 300%になるよう調整。<br>
    <b>1位との差</b>: 1着率1位の馬との差(pt)。差が小さいレースほど混戦。<br>
    <b>安定度</b>: 3連対率÷1着率。数値が大きいほど「勝ち切れないが崩れにくい」(複勝・ワイド向き)、
    小さいほど「勝つか凡走かの一発型」(単勝向き)。<br>
    <b>印</b>: AI評価順の◎本命 ○対抗 ▲単穴 △連下。<br>
    <b>AI指数</b>: 1着率と3連対率をまとめた総合スコア。レース内トップを100として相対化。<br>
    <b>枠順評価</b>: そのコース(競馬場×芝ダ×距離)の過去実データで、その枠がどれだけ有利か。1.00が平均。<br>
    <b>人気との乖離</b>: 「人気順位 − AI評価順位」。プラスならAIが市場より高く買っている(妙味)、
    マイナスなら過剰人気の疑い。<br>
    <b>妙味</b>: 期待値をもとにした一言判定(妙味大 / 妙味あり / 妥当 / 過剰人気)。<br>
    <b>期待値</b>: 予測1着確率 × 単勝オッズ。オッズ未取得時は「オッズ待ち」と表示。
  </p>
  <p>期待値が1.0を上回るほど市場価格に対して割安と推定されますが、的中や回収率のプラスを保証するものでは
  ありません(バックテストでは市場を上回るエッジは確認できていません)。馬券の最終判断はご自身でお願いします。
  枠番の色は実際の帽色に合わせています。</p>
</footer>
<script>
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  }});
}});
document.querySelectorAll('.race-tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const panel = btn.closest('.tab-panel');
    panel.querySelectorAll('.race-tab-btn').forEach(b => b.classList.remove('active'));
    panel.querySelectorAll('.race-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.race).classList.add('active');
  }});
}});
</script>
</body>
</html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"{OUT_HTML} を生成しました({len(tracks)}競馬場 / {df['race_id'].nunique()}レース分)")

# 「買うべきレース」ページ(AI買い目の対象だけ)も同じ場所に作る
import picks_page  # noqa: E402
from picks_page import build_picks  # noqa: E402
picks_page.TRIFECTA = TRIFECTA

_picks = os.path.join(os.path.dirname(os.path.abspath(OUT_HTML)), "picks.html")
with open(_picks, "w", encoding="utf-8") as f:
    f.write(build_picks(df, date_label, generated_at))
print(f"{_picks} を生成しました")

# 印刷用ページ(A4で印刷・PDF保存)も同じ場所に作る
from print_page import build_print  # noqa: E402

_print = os.path.join(os.path.dirname(os.path.abspath(OUT_HTML)), "print.html")
with open(_print, "w", encoding="utf-8") as f:
    _past = {}
    try:  # 近4走(weekly_predict.yml が past_runs.py で予想と同じ場所に作る)
        with open(os.path.join(_site_dir, "past_runs.json"), encoding="utf-8") as _pf:
            _past = json.load(_pf)
    except (OSError, ValueError):
        pass
    f.write(build_print(df, date_label, generated_at, TRIFECTA, _past))
print(f"{_print} を生成しました")
