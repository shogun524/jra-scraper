"""
予測の成績ページ(performance.html)を生成する。

入力: history/predictions_YYYY-MM-DD.csv(予測ジョブが毎回保存している予測履歴)
      history/results_YYYY-MM-DD.csv    (fetch_results.py が保存する確定結果)
出力: performance.html

見るもの:
  1. ◎(AI1着率1位)の勝率・3着内率・単勝回収率と、1番人気との比較
  2. キャリブレーション: 「AIが10%と言った馬は本当に10%勝っているか」を確率帯ごとに
  3. AI vs 市場: 勝ち馬に付けていた確率の対数損失・ブライアスコアを比較し、
     AIと市場の確率を混ぜたときの最適な重み(AIが市場に情報を足せているか)を推定
  4. 人気帯別: ◎が何番人気だったか(=市場とどれだけ食い違っていたか)ごとの成績
  5. 期待値フィルタ: EV≧閾値の馬に単勝を買い続けた場合の回収率
  6. 開催日別・競馬場別の推移

使い方:
    python src/generate_performance.py site/history site/performance.html
"""
import glob
import html as _html
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HISTORY_DIR = sys.argv[1] if len(sys.argv) > 1 else "docs/history"
OUT_HTML = sys.argv[2] if len(sys.argv) > 2 else "docs/performance.html"

EV_THRESHOLDS = [1.0, 1.2, 1.5, 2.0]
PROB_BINS = [0, .03, .06, .10, .15, .20, .30, 1.01]
PROB_LABELS = ["〜3%", "3〜6%", "6〜10%", "10〜15%", "15〜20%", "20〜30%", "30%〜"]
POP_BINS = [0, 1, 3, 6, 9, 99]
POP_LABELS = ["1番人気", "2〜3番人気", "4〜6番人気", "7〜9番人気", "10番人気〜"]


# ----------------------------------------------------------------------------
# データ読み込み
# ----------------------------------------------------------------------------
def load(history_dir: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(os.path.join(history_dir, "predictions_*.csv"))):
        d = os.path.basename(f)[len("predictions_"):-len(".csv")]
        rf = os.path.join(history_dir, f"results_{d}.csv")
        if not os.path.exists(rf):
            continue
        pred = pd.read_csv(f, dtype={"race_id": str})
        res = pd.read_csv(rf, dtype={"race_id": str})
        res = res.drop(columns=[c for c in ("horse",) if c in res.columns])
        m = pred.merge(res, on=["race_id", "umaban"], how="inner")
        frames.append(m)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    # 確定済み(1着がいる)レースだけ
    done = df.groupby("race_id")["finish"].transform(lambda s: (s == 1).any())
    df = df[done].copy()
    # 取消・除外(着順もオッズも無い)は返還扱いで評価から除く。競走中止は着外として残す。
    df = df[~(df["finish"].isna() & df["odds_final"].isna())].copy()

    df["win"] = (df["finish"] == 1).astype(int)
    df["top3"] = (df["finish"] <= 3).astype(int)
    # 単勝払戻の計算は確定オッズ。取れなければ予測時点のオッズで代用。
    df["odds_pay"] = df["odds_final"].fillna(df["odds_win"])

    # AI確率: 除外馬を除いたので、レース内で合計1に正規化し直す
    df["p_ai"] = df["pred_win_norm"] / df.groupby("race_id")["pred_win_norm"].transform("sum")
    # 市場確率: 確定単勝オッズの逆数をレース内で正規化(控除率分を取り除く)
    inv = 1.0 / df["odds_pay"]
    df["p_mkt"] = inv / inv.groupby(df["race_id"]).transform("sum")
    # 人気: 確定人気。無ければ確定オッズ順
    pop_from_odds = df.groupby("race_id")["odds_pay"].rank(method="min")
    df["pop"] = df["popularity"].fillna(pop_from_odds)
    # ◎ = AI1着率1位(除外で繰り上がりはしない、掲載時の◎をそのまま評価)
    df["is_honmei"] = df["pred_win_rank"] == 1
    df["ev_post"] = df["p_ai"] * df["odds_pay"]  # 確定オッズで計算した期待値
    return df


# ----------------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------------
def bet_stats(sub: pd.DataFrame) -> dict:
    sub = sub[sub["odds_pay"].notna()]  # オッズ不明の馬は買えなかったものとして除外
    n = len(sub)
    if n == 0:
        return {"n": 0, "hit": 0, "hit_rate": np.nan, "top3_rate": np.nan, "roi": np.nan, "avg_odds": np.nan}
    pay = (sub["win"] * sub["odds_pay"]).sum()
    return {
        "n": n, "hit": int(sub["win"].sum()),
        "hit_rate": sub["win"].mean(), "top3_rate": sub["top3"].mean(),
        "roi": pay / n, "avg_odds": sub["odds_pay"].mean(),
    }


def calibration(df: pd.DataFrame, col: str) -> pd.DataFrame:
    b = pd.cut(df[col], PROB_BINS, labels=PROB_LABELS, right=False)
    g = df.groupby(b, observed=False).agg(n=("win", "size"), pred=(col, "mean"), actual=("win", "mean"))
    return g


def race_logloss(df: pd.DataFrame, col: str) -> float:
    p = df.loc[df["win"] == 1, col].clip(1e-4, 1)
    return float(-np.log(p).mean())


def brier(df: pd.DataFrame, col: str) -> float:
    return float(((df[col] - df["win"]) ** 2).groupby(df["race_id"]).sum().mean())


def fit_blend(df: pd.DataFrame):
    """p ∝ p_ai^a × p_mkt^b の a, b を最尤推定(グリッドサーチ)。
    a≈0 なら「AIは市場に何も情報を足せていない」、a>0 ならAIに独自の情報がある。"""
    la = np.log(df["p_ai"].clip(1e-4).to_numpy())
    lm = np.log(df["p_mkt"].clip(1e-4).to_numpy())
    rid = pd.factorize(df["race_id"])[0]
    win = df["win"].to_numpy() == 1
    n_r = rid.max() + 1
    best = (np.inf, 0.0, 1.0)
    for a in np.arange(0, 2.001, 0.05):
        for b in np.arange(0, 2.001, 0.05):
            s = a * la + b * lm
            # レースごとのlog-sum-exp
            mx = np.full(n_r, -np.inf)
            np.maximum.at(mx, rid, s)
            z = np.zeros(n_r)
            np.add.at(z, rid, np.exp(s - mx[rid]))
            ll = -(s[win] - mx[rid[win]] - np.log(z[rid[win]])).mean()
            if ll < best[0]:
                best = (ll, a, b)
    ll, a, b = best
    s = a * la + b * lm
    p = np.exp(s - pd.Series(s).groupby(rid).transform("max").to_numpy())
    p = p / pd.Series(p).groupby(rid).transform("sum").to_numpy()
    return float(a), float(b), float(ll), p


# ----------------------------------------------------------------------------
# HTML部品
# ----------------------------------------------------------------------------
def pct(x, d=1):
    return "-" if pd.isna(x) else f"{x * 100:.{d}f}%"


def roi_cell(x):
    if pd.isna(x):
        return '<td class="num">-</td>'
    cls = "good" if x >= 1.0 else ("mid" if x >= 0.8 else "low")
    return f'<td class="num roi-{cls}">{x * 100:.1f}%</td>'


def bar(pred, actual, scale):
    """予測(枠線)と実際(塗り)を同じスケールで重ねる横棒"""
    if pd.isna(pred) or pd.isna(actual):
        return ""
    w_p = min(pred / scale, 1) * 100
    w_a = min(actual / scale, 1) * 100
    return (f'<div class="cal"><span class="cal-act" style="width:{w_a:.1f}%"></span>'
            f'<span class="cal-pred" style="left:{w_p:.1f}%"></span></div>')


def table(head, rows, cls=""):
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<div class="tbl-wrap"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def build_html(df: pd.DataFrame) -> str:
    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    if df.empty:
        body = '<section class="card"><p>まだ結果と照合できたレースがありません。開催日の夕方に結果を取得すると、ここに成績が表示されます。</p></section>'
        return page(body, now, "")

    n_races = df["race_id"].nunique()
    dates = sorted(df["race_date"].astype(str).unique())
    period = f"{dates[0]} 〜 {dates[-1]}(開催{len(dates)}日・{n_races}レース)"

    hon = df[df["is_honmei"]]
    fav = df[df["pop"] == 1]
    s_hon, s_fav = bet_stats(hon), bet_stats(fav)
    s_all = bet_stats(df)

    # ---- KPI ----
    kpis = f"""
    <div class="kpis">
      <div class="kpi"><div class="kpi-label">◎ 勝率</div><div class="kpi-val">{pct(s_hon['hit_rate'])}</div>
        <div class="kpi-sub">1番人気 {pct(s_fav['hit_rate'])}</div></div>
      <div class="kpi"><div class="kpi-label">◎ 3着内率</div><div class="kpi-val">{pct(s_hon['top3_rate'])}</div>
        <div class="kpi-sub">1番人気 {pct(s_fav['top3_rate'])}</div></div>
      <div class="kpi"><div class="kpi-label">◎ 単勝回収率</div><div class="kpi-val">{pct(s_hon['roi'])}</div>
        <div class="kpi-sub">1番人気 {pct(s_fav['roi'])} ・ 全馬 {pct(s_all['roi'])}</div></div>
      <div class="kpi"><div class="kpi-label">◎が1番人気だった割合</div><div class="kpi-val">{pct((hon['pop'] == 1).mean())}</div>
        <div class="kpi-sub">◎の平均人気 {hon['pop'].mean():.1f}番</div></div>
    </div>"""

    # ---- AI vs 市場 ----
    a, b, ll_blend, p_blend = fit_blend(df)
    df = df.assign(p_blend=p_blend)
    n_runners = df.groupby("race_id").size().mean()
    ll_unif = float(np.log(df.groupby("race_id").size()).mean())
    ll_ai, ll_mkt = race_logloss(df, "p_ai"), race_logloss(df, "p_mkt")
    rows = []
    for name, ll, br, note in [
        ("AI(1着率)", ll_ai, brier(df, "p_ai"), ""),
        ("市場(確定単勝オッズ)", ll_mkt, brier(df, "p_mkt"), ""),
        (f"最適ブレンド(AI^{a:.2f}×市場^{b:.2f})", ll_blend, brier(df, "p_blend"), "同じデータで重みを決めているので、やや甘めの値"),
        ("でたらめ(全馬同確率)", ll_unif, np.nan, f"平均{n_runners:.1f}頭立て"),
    ]:
        rows.append(f"<tr><td>{name}</td><td class='num'>{ll:.3f}</td>"
                    f"<td class='num'>{'-' if pd.isna(br) else f'{br:.4f}'}</td><td class='note'>{note}</td></tr>")
    if a < 0.15:
        verdict = "AIの重みがほぼ0。現状のAIは<b>市場オッズに上乗せできる情報をほとんど持っていない</b>状態です。"
    elif ll_ai > ll_mkt:
        verdict = (f"AI単体は市場より精度が低い一方、ブレンド時のAIの重みは{a:.2f}。"
                   "<b>市場の確率を土台に、AIは補正として少し混ぜる</b>使い方が合っています。")
    else:
        verdict = "AI単体が市場と同等以上の精度。AIの独自情報が効いている可能性があります(件数が少ないうちは要注意)。"
    vs_market = f"""
    <section class="card">
      <h2>AI と 市場、どちらの確率が当たっているか</h2>
      <p class="lead">勝ち馬にどれだけ高い確率を付けていたか(対数損失)と、全馬の確率の誤差(ブライアスコア)。どちらも<b>小さいほど良い</b>。</p>
      {table(["確率の出し方", "対数損失", "ブライア", ""], rows)}
      <p class="verdict">{verdict}</p>
    </section>"""

    # ---- キャリブレーション ----
    cal_ai, cal_mkt = calibration(df, "p_ai"), calibration(df, "p_mkt")
    scale = max(0.4, float(np.nanmax([cal_ai["actual"].max(), cal_ai["pred"].max(), cal_mkt["actual"].max()])))
    rows = []
    for lab in PROB_LABELS:
        r_ai, r_mk = cal_ai.loc[lab], cal_mkt.loc[lab]
        rows.append(
            f"<tr><td>{lab}</td>"
            f"<td class='num'>{int(r_ai.n)}</td><td class='num'>{pct(r_ai.pred)}</td><td class='num'><b>{pct(r_ai.actual)}</b></td>"
            f"<td class='barcell'>{bar(r_ai.pred, r_ai.actual, scale)}</td>"
            f"<td class='num sep'>{int(r_mk.n)}</td><td class='num'>{pct(r_mk.pred)}</td><td class='num'><b>{pct(r_mk.actual)}</b></td>"
            f"<td class='barcell'>{bar(r_mk.pred, r_mk.actual, scale)}</td></tr>")
    calib = f"""
    <section class="card">
      <h2>キャリブレーション(確率の正直さ)</h2>
      <p class="lead">「1着率10%と出た馬は、本当に10回に1回勝っているか」。棒の<span class="lg-act">塗り</span>が実際の勝率、<span class="lg-pred">縦線</span>が予測。
      塗りが縦線より短い帯は<b>過大評価</b>、長い帯は<b>過小評価</b>。</p>
      {table(["確率帯", "AI 頭数", "予測", "実際", "", "市場 頭数", "予測", "実際", ""], rows, "cal-table")}
    </section>"""

    # ---- 人気帯別(AIと市場の食い違い) ----
    hon_band = pd.cut(hon["pop"], POP_BINS, labels=POP_LABELS)
    df_band = pd.cut(df["pop"], POP_BINS, labels=POP_LABELS)
    rows = []
    for lab in POP_LABELS:
        s = bet_stats(hon[hon_band == lab])
        allb = df[df_band == lab]
        rows.append(
            f"<tr><td>{lab}</td><td class='num'>{s['n']}</td><td class='num'>{s['hit']}</td>"
            f"<td class='num'>{pct(s['hit_rate'])}</td><td class='num'>{pct(s['top3_rate'])}</td>{roi_cell(s['roi'])}"
            f"<td class='num sep'>{pct(allb['p_ai'].mean())}</td><td class='num'>{pct(allb['p_mkt'].mean())}</td>"
            f"<td class='num'><b>{pct(allb['win'].mean())}</b></td></tr>")
    pop_tbl = f"""
    <section class="card">
      <h2>人気帯別 — AIと市場が食い違ったとき</h2>
      <p class="lead">左: ◎が実際には何番人気だったか別の成績。◎が人気薄のときは「AIだけが推している」状態。<br>
      右: その人気帯の全馬について、AIと市場が付けた平均1着率と実際の勝率。実際に近い方が正確。</p>
      {table(["◎の人気", "◎数", "1着", "勝率", "3着内", "単勝回収", "AI平均", "市場平均", "実際"], rows)}
    </section>"""

    # ---- 期待値フィルタ ----
    rows = []
    for label, sub in [("全馬に単勝(ベースライン)", df), ("◎に単勝", hon), ("1番人気に単勝", fav)]:
        s = bet_stats(sub)
        rows.append(f"<tr><td>{label}</td><td class='num'>{s['n']}</td><td class='num'>{s['hit']}</td>"
                    f"<td class='num'>{pct(s['hit_rate'])}</td><td class='num'>{s['avg_odds']:.1f}</td>{roi_cell(s['roi'])}</tr>")
    for col, name in [("expected_value", "掲載時オッズ"), ("ev_post", "確定オッズ")]:
        for t in EV_THRESHOLDS:
            s = bet_stats(df[df[col] >= t])
            avg = "-" if pd.isna(s["avg_odds"]) else f"{s['avg_odds']:.1f}"
            rows.append(f"<tr><td>EV≧{t:.1f}({name}で判定)</td><td class='num'>{s['n']}</td><td class='num'>{s['hit']}</td>"
                        f"<td class='num'>{pct(s['hit_rate'])}</td><td class='num'>{avg}</td>{roi_cell(s['roi'])}</tr>")
    ev_tbl = f"""
    <section class="card">
      <h2>回収率シミュレーション(単勝100円ずつ)</h2>
      <p class="lead">払戻は確定オッズで計算。「掲載時オッズ」はページに表示されていた期待値、「確定オッズ」は締切後のオッズで計算し直した期待値で判定。</p>
      {table(["買い方", "点数", "的中", "的中率", "平均オッズ", "回収率"], rows)}
    </section>"""

    # ---- 開催日別・競馬場別 ----
    def grouped(key, label):
        rows = []
        for k, g in df.groupby(key, sort=True):
            h = g[g["is_honmei"]]
            s, f = bet_stats(h), bet_stats(g[g["pop"] == 1])
            rows.append(f"<tr><td>{_html.escape(str(k))}</td><td class='num'>{g['race_id'].nunique()}</td>"
                        f"<td class='num'>{s['hit']}</td><td class='num'>{pct(s['hit_rate'])}</td><td class='num'>{pct(s['top3_rate'])}</td>"
                        f"{roi_cell(s['roi'])}<td class='num sep'>{pct(f['hit_rate'])}</td>"
                        f"<td class='num'>{race_logloss(g, 'p_ai'):.2f}</td><td class='num'>{race_logloss(g, 'p_mkt'):.2f}</td></tr>")
        return table([label, "R数", "◎1着", "◎勝率", "◎3着内", "◎単回収", "1人気勝率", "AI損失", "市場損失"], rows)

    by_group = f"""
    <section class="card">
      <h2>開催日別</h2>
      {grouped("race_date", "日付")}
      <h2 style="margin-top:22px">競馬場別</h2>
      {grouped("track_name", "競馬場")}
    </section>"""

    body = kpis + vs_market + calib + pop_tbl + ev_tbl + by_group
    return page(body, now, period)


def page(body: str, now: str, period: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>予測の成績</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600;800&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --turf: #1F4D3A; --paper: #F6F1E4; --paper-line: #E4DAC0;
    --ink: #2A2118; --ink-soft: #6B5F4D; --gold: #B8862B; --gold-soft: #F0D896;
    --good: #2E7D46; --mid: #B8862B; --low: #B0433A;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink);
    font-family: "Noto Sans JP", system-ui, sans-serif; font-size: 15px; line-height: 1.6; }}
  header.top {{ background: var(--turf); color: #fff; padding: 20px; }}
  header .inner {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-family: "Shippori Mincho", serif; font-weight: 800; margin: 0 0 4px; font-size: 1.6rem; }}
  header p {{ margin: 0; font-size: .82rem; opacity: .9; }}
  header a {{ color: var(--gold-soft); }}
  main {{ max-width: 900px; margin: 0 auto; padding: 16px; }}
  .card {{ background: #fff; border: 1px solid var(--paper-line); border-radius: 6px; padding: 16px; margin-bottom: 16px; }}
  h2 {{ font-family: "Shippori Mincho", serif; color: var(--turf); font-size: 1.15rem; margin: 0 0 6px; }}
  .lead {{ margin: 0 0 10px; font-size: .82rem; color: var(--ink-soft); }}
  .verdict {{ margin: 12px 0 0; padding: 10px 12px; background: #FBF6E8; border-left: 3px solid var(--gold); font-size: .88rem; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .kpi {{ background: #fff; border: 1px solid var(--paper-line); border-top: 3px solid var(--turf); border-radius: 6px; padding: 12px 14px; }}
  .kpi-label {{ font-size: .76rem; color: var(--ink-soft); }}
  .kpi-val {{ font-size: 1.7rem; font-weight: 700; color: var(--turf); font-variant-numeric: tabular-nums; line-height: 1.3; }}
  .kpi-sub {{ font-size: .74rem; color: var(--ink-soft); }}
  .tbl-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .84rem; }}
  th {{ text-align: left; font-weight: 500; font-size: .72rem; color: var(--ink-soft);
    padding: 6px 8px; border-bottom: 1px solid var(--paper-line); white-space: nowrap; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid var(--paper-line); white-space: nowrap; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.note {{ color: var(--ink-soft); font-size: .74rem; white-space: normal; }}
  .sep {{ border-left: 1px solid var(--paper-line); }}
  .roi-good {{ color: var(--good); font-weight: 700; }}
  .roi-mid {{ color: var(--mid); font-weight: 700; }}
  .roi-low {{ color: var(--low); font-weight: 700; }}
  .barcell {{ width: 120px; min-width: 90px; }}
  .cal {{ position: relative; height: 12px; background: #F3EEDF; border-radius: 2px; }}
  .cal-act {{ position: absolute; left: 0; top: 0; bottom: 0; background: var(--turf); opacity: .75; border-radius: 2px; }}
  .cal-pred {{ position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--gold); }}
  .lg-act {{ display: inline-block; padding: 0 4px; background: var(--turf); color: #fff; border-radius: 2px; opacity: .8; }}
  .lg-pred {{ display: inline-block; padding: 0 4px; border-left: 2px solid var(--gold); }}
  footer {{ max-width: 900px; margin: 0 auto 24px; padding: 0 20px; color: var(--ink-soft); font-size: .74rem; line-height: 1.7; }}
</style>
</head>
<body>
<header class="top">
  <div class="inner">
    <h1>予測の成績</h1>
    <p>{period} ・ 最終更新 {now} (JST) ・ <a href="index.html">今週の予想へ戻る</a></p>
  </div>
</header>
<main>{body}</main>
<footer>
  <p>◎はAIの1着率1位の馬(ページ掲載時点)。人気・払戻は確定オッズ。取消・除外馬は集計から除いています。
  件数が少ないうちは数字のブレが大きいので、回収率は数百レース貯まるまで参考程度に見てください。</p>
</footer>
</body>
</html>"""


if __name__ == "__main__":
    data = load(HISTORY_DIR)
    out = build_html(data)
    os.makedirs(os.path.dirname(OUT_HTML) or ".", exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(out)
    n = data["race_id"].nunique() if not data.empty else 0
    print(f"{OUT_HTML} を生成しました({n}レース照合)")
