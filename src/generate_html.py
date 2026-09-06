"""
JRA競馬AI - HTMLダッシュボード生成(v2)
data/predictions.csv を読み込み、競馬場ごとのタブ形式で docs/index.html を生成する。

デザイン方針:
- 汎用的な「黒×青のAIダッシュボード」ではなく、競馬新聞・出馬表・トートボードの質感を土台にする
- 枠番の色は実際のJRA公式配色(白/黒/赤/青/黄/緑/橙/桃)をそのまま使う
- レースIDではなく、競馬場名・R番号・レース名・発走時刻を主役にする
"""
import pandas as pd
import sys
from datetime import datetime, timezone, timedelta

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
generated_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
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


def race_card(race_id, g):
    g = g.sort_values("pred_win_rank")
    first = g.iloc[0]
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

        # 信頼度: 予測の根拠となる過去データの量(0-100)
        conf = r.get("confidence")
        if pd.notnull(conf):
            conf_cls = "conf-high" if conf >= 70 else ("conf-mid" if conf >= 35 else "conf-low")
            conf_html = f'<span class="conf {conf_cls}">{int(conf)}</span>'
        else:
            conf_html = "-"

        style = r.get("running_style") if pd.notnull(r.get("running_style")) else "-"
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
          <td class="gap">{gap_html}</td>
          <td>{stab_html}</td>
          <td class="style">{style}</td>
          <td>{conf_html}</td>
          <td>{odds}</td>
          <td>{ev_badge}</td>
        </tr>"""

    return f"""
    <article class="race-card">
      <header class="race-card-head">
        <span class="race-num">{int(first['race_number'])}<small>R</small></span>
        <div class="race-title">
          <h2>{race_name}</h2>
          <p class="race-sub">{first.get('track_name','')} {post_time_html}</p>
        </div>
      </header>
      <div class="table-scroll">
      <table>
        <thead>
          <tr><th>予想</th><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th>
              <th>1着率</th><th>3連対率</th><th>1位との差</th><th>安定度</th>
              <th>脚質</th><th>信頼度</th><th>単勝</th><th>期待値</th></tr>
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
        race_tab_buttons += f'<button class="race-tab-btn {r_active}" data-race="race-{i}-{j}">{race_num}R</button>'
        race_panels += f'<div class="race-panel {r_active}" id="race-{i}-{j}">{race_card(rid, g)}</div>'

    tab_panels += f"""<section class="tab-panel {active}" id="tab-{i}">
      <nav class="race-tabs">{race_tab_buttons}</nav>
      {race_panels}
    </section>"""

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
    <p>最終更新 {generated_at} (JST) ・ {odds_status} ・ <a href="courses.html" style="color:#F0D896;">競馬場ガイドを見る →</a></p>
  </div>
  <nav class="tabs">{tab_buttons}</nav>
</header>
<main>{tab_panels}</main>
<footer>
  <p><strong>各項目の見方</strong></p>
  <p>
    <b>1着率 / 3連対率</b>: モデルの推定確率。レース内で合計が100% / 300%になるよう調整。<br>
    <b>1位との差</b>: 1着率1位の馬との差(pt)。差が小さいレースほど混戦。<br>
    <b>安定度</b>: 3連対率÷1着率。数値が大きいほど「勝ち切れないが崩れにくい」(複勝・ワイド向き)、
    小さいほど「勝つか凡走かの一発型」(単勝向き)。<br>
    <b>脚質</b>: 過去の4コーナー通過位置から推定した逃げ/先行/差し/追込。<br>
    <b>信頼度</b>: その馬の過去データがどれだけ揃っているか(0-100)。低い馬は予測の根拠が薄いので注意。<br>
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
