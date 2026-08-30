"""
JRA競馬AI - HTMLダッシュボード生成
data/predictions.csv (1レースまたは複数レース分) を読み込み、
docs/index.html に静的ページとして出力する(GitHub Pages想定)。
"""
import pandas as pd
import sys
from datetime import datetime

IN_CSV = sys.argv[1] if len(sys.argv) > 1 else "data/predictions.csv"
OUT_HTML = sys.argv[2] if len(sys.argv) > 2 else "docs/index.html"

df = pd.read_csv(IN_CSV)
generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

def race_block(race_id, g):
    g = g.sort_values("pred_win_rank")
    rows = ""
    for _, r in g.iterrows():
        ev = r.get("expected_value", None)
        ev_badge = ""
        if pd.notnull(ev):
            cls = "ev-good" if ev >= 1.1 else ("ev-mid" if ev >= 0.9 else "ev-low")
            ev_badge = f'<span class="badge {cls}">期待値 {ev:.2f}</span>'
        odds = f'{r["odds_win"]:.1f}倍' if pd.notnull(r.get("odds_win")) else "-"
        rows += f"""
        <tr>
          <td class="rank">{int(r['pred_win_rank'])}</td>
          <td class="waku waku-{int(r['waku']) if pd.notnull(r['waku']) else 0}">{int(r['waku']) if pd.notnull(r['waku']) else '-'}</td>
          <td>{int(r['umaban'])}</td>
          <td class="horse">{r['horse']}</td>
          <td>{r['jockey']}</td>
          <td class="pct">{r['pred_win_norm']*100:.1f}%</td>
          <td class="pct">{r['pred_top3']*100:.1f}%</td>
          <td>{odds}</td>
          <td>{ev_badge}</td>
        </tr>"""
    return f"""
    <section class="race-card">
      <h2>レースID: {race_id}</h2>
      <table>
        <thead>
          <tr><th>予測順位</th><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th>
              <th>1着率</th><th>3連対率</th><th>単勝オッズ</th><th>期待値</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""

blocks = "".join(race_block(rid, g) for rid, g in df.groupby("race_id"))

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JRA競馬AI予測</title>
<style>
  :root {{
    --bg: #0f1419; --card: #1a2129; --line: #2a333d; --text: #e8edf2; --sub: #8b98a5;
    --accent: #4f9cf9; --good: #3ecf8e; --mid: #f5c451; --low: #f2545b;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif;
          background: var(--bg); color: var(--text); padding: 24px 16px 60px; }}
  header {{ max-width: 1000px; margin: 0 auto 24px; }}
  header h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  header p {{ color: var(--sub); font-size: .85rem; margin: 0; }}
  .race-card {{ max-width: 1000px; margin: 0 auto 20px; background: var(--card);
                border: 1px solid var(--line); border-radius: 12px; padding: 16px; overflow-x: auto; }}
  .race-card h2 {{ font-size: 1rem; margin: 0 0 12px; color: var(--accent); }}
  table {{ border-collapse: collapse; width: 100%; font-size: .82rem; white-space: nowrap; }}
  th {{ text-align: left; color: var(--sub); font-weight: 500; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); }}
  .rank {{ font-weight: 700; }}
  .horse {{ font-weight: 600; }}
  .pct {{ color: var(--accent); font-variant-numeric: tabular-nums; }}
  .waku {{ text-align:center; border-radius:4px; color:#111; font-weight:700; }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: .75rem; font-weight: 600; }}
  .ev-good {{ background: rgba(62,207,142,.15); color: var(--good); }}
  .ev-mid  {{ background: rgba(245,196,81,.15); color: var(--mid); }}
  .ev-low  {{ background: rgba(242,84,91,.15); color: var(--low); }}
  footer {{ max-width:1000px; margin: 24px auto 0; color: var(--sub); font-size: .75rem; line-height:1.6; }}
</style>
</head>
<body>
<header>
  <h1>🏇 JRA競馬AI予測ランキング</h1>
  <p>最終更新: {generated_at} / 1着率・3連対率はLightGBMモデルによる推定値です</p>
</header>
{blocks}
<footer>
  期待値 = 予測1着確率 × 単勝オッズ。1.0を上回るほど市場価格に対して割安と推定されることを示しますが、
  的中を保証するものではありません。馬券の最終判断はご自身でお願いします。
</footer>
</body>
</html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"{OUT_HTML} を生成しました({df['race_id'].nunique()}レース分)")
