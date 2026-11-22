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

import numpy as np
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
    return (f'<p class="manbaken" title="{"3連複オッズから" if v.get("src") == "3連複" else "単勝オッズから推定"}">万馬券率(3連複) <b class="{lvl}">{v["man"]:.0%}</b>'
            f'<span class="sep">・</span>目安 <b>{v["q50"]:,.0f}円</b> <small>({v["q25"]:,.0f}〜{v["q75"]:,.0f})</small></p>')

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
        <div class="aibet-combos">{" / ".join(e["tickets"])}</div>{_old_note(e)}
      </div>"""
    return html


def _old_note(e):
    """旧AIで検証したルールは、軸が画面の◎(新AI)と違うことがあるので一言添える"""
    if e["rule"].key in ("main", "sub", "op") and ("旧AI" in e["desc"] or e["rule"].key == "op"):
        return ('<div class="aibet-note">このルールは旧AI(前の版のAI)の評価で検証したので、軸・妙味馬は旧AIで選んでいます。'
                '画面の◎・1着率・込%(新AI)とは違う馬になることがあります。表の「旧◎」が旧AIの1位。</div>')
    return ""


# ---- EVボード(単勝・馬連・ワイド) ----
_site_dir = os.path.dirname(os.path.abspath(IN_CSV))
_race_date = str(df["race_date"].iloc[0]) if "race_date" in df.columns and len(df) else ""
_latest, _hist = load_odds_files(_site_dir, _race_date) if _race_date else (None, None)
_cal = load_calibration()
# ---- 的中率重視の買い目(人気順・回収率は見ない) ----
from hitrate import plan as _hit_plan  # noqa: E402


def hit_html(g):
    h = _hit_plan(g)
    if not h:
        return ""
    parts = " ／ ".join(f'{b} <b>{c}</b> <span class="hp">{p:.0%}</span>' for b, c, p in h["items"])
    return f'<p class="hitline" title="過去12,710レースの的中率(1番人気のオッズ×頭数ごと)">的中率重視: {parts}</p>'


def trio_html(g):
    """3連複(込%軸)。E=的中重視(約50%・回収79%)、E'=込%が市場の1.1倍以上の組だけ(的中15%・回収92%)"""
    from trio import plan, fmt
    t = plan(g)
    if not t:
        return ""
    sec = "・".join(map(str, t["second"]))
    thr = "・".join(map(str, [u for u in t["third"] if u != t["axis"]]))
    e2 = (f'<br><span class="tr-e2">回収重視(込%が市場の1.1倍以上の組だけ・{len(t["edge"])}点・的中約15%・回収約92%): '
          f'<b>{" / ".join(fmt(x) for x in t["edge"])}</b></span>') if t["edge"] else '<br><span class="tr-e2">回収重視: 該当なし</span>'
    return (f'<p class="trioline">3連複 的中重視(込%軸・{len(t["tickets"])}点・的中約50%・回収約79%): '
            f'<b>{t["axis"]}軸 → {sec} → {thr}</b>{e2}</p>')



try:
    with open(os.path.join(_site_dir, "runner_ped.json"), encoding="utf-8") as _rpf:
        RUNNER_PED = json.load(_rpf)
except (OSError, ValueError):
    RUNNER_PED = {}
# ---- 当日のトラックバイアス(その日のそれまでのレース結果から) ----
from track_bias import compute as _bias_compute, favored as _bias_favored  # noqa: E402
try:
    _res_today = pd.read_csv(os.path.join(_site_dir, "history", f"results_{_race_date[:10]}.csv"), dtype={"race_id": str}) if _race_date else None
except (OSError, ValueError):
    _res_today = None
BIAS = _bias_compute(df, _res_today)
# 前日発売のオッズ(レースが明日)は売れ始めで薄く、999.9倍など極端な値が混じる → 期待値は当日のオッズで見るよう注意を出す
try:
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _is_zenjitsu = bool(_race_date) and _race_date[:10] > _dt.now(_tz(_td(hours=9))).strftime("%Y-%m-%d")
except Exception:  # noqa: BLE001
    _is_zenjitsu = False
hit_summary = ""
zenjitsu_note = ('<p class="zenjitsu">⚠ 前日発売のオッズです。まだ売れ始めで、999.9倍など極端な値が混じり、期待値・妙味・万馬券率は大きくぶれます。'
                 'EVボードは単勝だけ出しています(馬連・ワイドは票が薄く、EVが意味をなさないため当日から)。'
                 '買う判断は当日朝からのオッズ(5分おきに更新)で。</p>') if _is_zenjitsu else ""


def bias_html(race_id):
    b = BIAS.get(str(race_id))
    if not b or not b.get("label"):
        return ""
    cls = "bias-in" if b["side"] == "内" else ("bias-out" if b["side"] == "外" else "bias-flat")
    adv = {"内": "内枠(馬番が頭数の内側1/3)が有利", "外": "外枠(外側1/3)が有利"}.get(b["side"], "内外の差なし")
    return (f'<p class="bias {cls}" title="{adv}({b["n"]}レース分)">{b.get("surface", "")}バイアス <b>{b["label"]}</b>'
            f'{" <small>緑の馬番が有利</small>" if b["side"] else ""}</p>')


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


# 11/15: 馬連・ワイドのEVは組み合わせ確率の推定が粗く、高配当の組で1.3超えが並んで当てにならないので出さない。
# EVは単勝だけ、込%(オッズ込みの1着率)×単勝オッズで出す(各馬の「期待値」と同じ)。
_old_ev = EV
if "mm_win" in df.columns:
    _e = df.assign(race_id=df["race_id"].astype(str))
    _e = _e[pd.to_numeric(_e["mm_win"], errors="coerce").notna() & pd.to_numeric(_e["odds_win"], errors="coerce").gt(0)]
    EV = pd.DataFrame({"race_id": _e["race_id"], "bet": "単勝", "combo": _e["umaban"].astype(int).astype(str),
                       "odds": _e["odds_win"].astype(float), "p": _e["mm_win"].astype(float),
                       "ev": (_e["mm_win"].astype(float) * _e["odds_win"].astype(float)).round(2)}).reset_index(drop=True)
    _keep = [c for c in ("move", "odds_first", "weight_diff") if c in _old_ev.columns] if not _old_ev.empty else []
    if _keep:
        EV = EV.merge(_old_ev[_old_ev["bet"] == "単勝"][["race_id", "combo"] + _keep].drop_duplicates(["race_id", "combo"]),
                      on=["race_id", "combo"], how="left")
    for _c in ("move", "odds_first", "weight_diff"):
        if _c not in EV.columns:
            EV[_c] = np.nan
elif not EV.empty:
    EV = EV[EV["bet"] == "単勝"].reset_index(drop=True)


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
    n_ev = int((e["ev"] >= 1.0).sum())
    return f'<details class="evbox"><summary class="evhead">このレースのEV1.0以上 {n_ev}件</summary><div class="evcols">{"".join(parts)}</div></details>'


def race_card(race_id, g):
    g = g.sort_values("pred_win_rank")
    first = g.iloc[0]
    aibet_html = ai_bet_box(ai_bet_status(g))   # レースごとのEV一覧は出さない(各馬の「妙味・期待値」で見る)
    race_name = first.get("race_name")
    race_name = race_name if isinstance(race_name, str) and race_name.strip() else ""
    post_time = first.get("post_time")
    post_time_html = f'<span class="post-time">発走 {post_time}</span>' if isinstance(post_time, str) and post_time else ""

    from honmei_tags import race_tags
    _tags = race_tags(g)
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
        if pd.notnull(r.get("odds_place_lo")):
            _hi = r.get("odds_place_hi")
            odds += f'<br><small class="plc">複{r["odds_place_lo"]:.1f}{"-" + format(_hi, ".1f") if pd.notnull(_hi) and _hi != r["odds_place_lo"] else ""}</small>'
        _bl = (RUNNER_PED.get(str(r["horse"])) or {})
        blood = f'<br><small class="bl">父{_bl["sire"]} 母父{_bl.get("damsire") or "-"}</small>' if _bl.get("sire") else ""
        if not EV.empty and pd.notnull(r.get("umaban")):
            _m = EV[(EV["race_id"] == str(race_id)) & (EV["bet"] == "単勝") & (EV["combo"] == str(int(r["umaban"])))]
            if not _m.empty and pd.notna(_m["move"].iloc[0]) and abs(_m["move"].iloc[0]) >= 0.1:
                odds += ' <span class="mv mv-in">↓</span>' if _m["move"].iloc[0] > 0 else ' <span class="mv mv-out">↑</span>'
        hw, wdiff = r.get("horse_weight"), r.get("weight_diff")
        weight_html = (f'{int(hw)}<small>({int(wdiff):+d})</small>' if pd.notnull(hw) and pd.notnull(wdiff)
                       else (f"{int(hw)}" if pd.notnull(hw) else "-"))
        umaban = int(r["umaban"]) if pd.notnull(r.get("umaban")) else "-"
        rank_cls = "top-pick" if r["pred_win_rank"] == 1 else ""
        _b = BIAS.get(str(race_id)) or {}
        _fav = _bias_favored(r.get("umaban"), len(g), _b.get("side"))
        if _fav is True:
            umaban = f'<span class="ub-fav" title="当日バイアスで有利な側">{umaban}</span>'
        elif _fav is False:
            umaban = f'<span class="ub-unfav" title="当日バイアスで不利な側">{umaban}</span>'

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

        def mm_html(r):
            # オッズ込みの1着率(market_model.py: 単勝オッズ+市場が織り込み切れていない要素)。◎はAIのまま、実際に勝つ確率の目安
            v = r.get("mm_win")
            if not pd.notnull(v):
                return ""
            hi = pd.notnull(r.get("value_ratio")) and r["value_ratio"] >= 1.15
            return f'<br><small class="mm{" mm-hi" if hi else ""}">込{v*100:.0f}%</small>'

        mk_cell = mk_html or f'<span class="rk">{int(r["pred_win_rank"])}</span>'
        if pd.notnull(r.get("rule_rank")) and int(r["rule_rank"]) == 1:
            mk_cell += '<br><small class="oldmk">旧◎</small>'
        _t = _tags.get(int(r["umaban"]))
        if _t:
            mk_cell += f'<br><span class="htag {_t[1]}">{_t[0]}</span>'
        sub = " ".join(x for x in [str(r.get("jockey") or ""), weight_html if weight_html != "-" else ""] if x)
        rows += f"""
        <tr class="{rank_cls}">
          <td class="c">{mk_cell}</td>
          <td class="c ubc">{waku_badge(r.get('waku'))}<b class="ubn">{umaban}</b></td>
          <td class="horse">{r['horse']}<br><small class="sub">{sub}</small>{blood}</td>
          <td class="r od">{odds}</td>
          <td class="r pct"><b>{r['pred_win_norm']*100:.1f}</b><small>%</small><br><small class="sub">3着内{r['pred_top3_norm']*100:.0f}%</small>{mm_html(r)}</td>
          <td class="c">{vt_html if vt_html != "-" else ""}<br><small class="evs">{ev_badge}</small></td>
        </tr>"""

    return f"""
    <article class="race-card">
      <header class="race-card-head">
        <span class="race-num">{int(first['race_number'])}<small>R</small></span>
        <div class="race-title">
          <h2>{race_name}</h2>
          <p class="race-sub">{first.get('track_name','')} {post_time_html}</p>
          <div class="chips">{manbaken_html(g)}{bias_html(race_id)}</div>
          {hit_html(g)}{trio_html(g)}
        </div>
      </header>
      {aibet_html}
      <div class="table-scroll">
      <table class="ht"><colgroup><col style="width:26px"><col style="width:50px"><col><col style="width:58px"><col style="width:50px"><col style="width:54px"></colgroup>
        <thead>
          <tr><th>印</th><th>馬番</th><th>馬名</th><th>単勝</th><th>AI<br><small>込</small></th><th>妙味</th></tr>
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
# ルールごとの「どのAIで選ぶか」と検証成績(一目で分かるように見出しに出す)
RULE_META = {"main": ("旧AI", "回収率160%"), "sub": ("旧AI", "回収率120%"), "c1": ("新AI", "回収率133%"),
             "gr": ("新AI", "回収率139%・的中60%"), "op": ("旧AI", "回収率141%")}
if "race_class" in df.columns:
    blocks, empty = "", []
    for rule in RULES:
        items = sorted(_targets[rule.key], key=lambda x: x[0])
        if not items:
            empty.append(rule.label)
            continue
        ai, roi = RULE_META.get(rule.key, ("", ""))
        lis = "".join(
            f'<li><span class="aibet-tag{"" if e["status"] == "対象" else " aibet-wait"}">{e["status"]}</span> '
            f'<b>{tn}{rn}R</b> {pt} <span class="rnm">{nm if isinstance(nm, str) else ""}</span><br><span class="aibet-buy">{e["desc"]}</span></li>'
            for pt, tn, rn, nm, e in items)
        blocks += (f'<div class="rule"><h3 class="aibet-h3">{rule.label}<span class="ai-b ai-{"old" if ai == "旧AI" else "new"}">{ai}で選ぶ</span>'
                   f'<span class="roi-b">検証{roi}</span></h3>'
                   f'<details class="why"><summary>条件</summary><p class="aibet-note">{rule.name}。{rule.backtest}。</p></details><ul>{lis}</ul></div>')
    if empty:
        blocks += f'<p class="aibet-none">今日は対象なし: {"・".join(empty)}</p>'
    aibet_summary = f"""<section class="aibet-summary"><h2>今日のAI買い目</h2>{blocks}
      </section>"""

# サイトの見方(よく迷うところだけ)
guide = """<details class="guide" open><summary>このサイトの見方</summary><ul>
  <li><b>◎・AI評価%</b> … 新AIが馬の力だけで見た1着率(今のオッズは使わない)。◎が人気薄のときは強気すぎる傾向。</li>
  <li><b>込%</b> … 実際に勝ちそうな確率(オッズ込み)。<b>一番当たる数字</b>。妙味・期待値・EVボードもこれで計算。</li>
  <li><b>妙味</b> … 込%がオッズの見込みより高い馬=妙味あり(1.15倍以上)、低い馬=過剰人気。</li>
  <li><b>狙い目！・普通・注意・危険！</b> … ◎と旧◎に付く一言。<span class="htag tg-go">狙い目！</span>=旧◎が◎と違う人気薄(過去はオッズの見込みより2〜3割多く勝つ)、<span class="htag tg-ok">普通</span>=1〜3番人気(オッズどおり。普通に買ってよい)、<span class="htag tg-warn">注意</span>=◎が4〜5番人気、<span class="htag tg-ng">危険！</span>=◎と旧◎が同じ人気薄(見込みの6割しか勝たない)。そのレースでは<span class="htag tg-push">推し</span>(込%1位。過去は複勝圏61%・複勝回収率90%)を代わりに。</li>
  <li><b>3連複</b> … 的中重視=込%1位を軸に込%2〜4位→AIの3連対率10%以上へ(過去 的中約50%・回収79%)。回収重視=その中で込%が市場より1.1倍以上高く見ている組だけ(的中約15%・回収92%)。</li>
  <li><b>AI買い目</b> … 過去の検証で回収率が出たルール。<span class="ai-b ai-old">旧AIで選ぶ</span>ものは画面の◎と違う馬が軸になる(表の「旧◎」の馬)。</li>
  <li>オッズは5分おきに更新。AI評価は朝〜午後に数回(馬体重などで)作り直し。</li>
</ul></details>"""

# EV上位(全レース)
def build_hit_summary():
    rows = []
    for rid, g in df.groupby("race_id"):
        h = _hit_plan(g)
        if not h:
            continue
        f0 = g.iloc[0]
        rows.append((h["items"][0][2], h["items"][1][2], f0.get("track_name", ""), int(f0.get("race_number", 0)),
                     str(f0.get("post_time", "") or ""), h))
    if not rows:
        return ""
    from datetime import datetime as _d2, timedelta as _t2, timezone as _z2
    _now = _d2.now(_z2(_t2(hours=9))).strftime("%H:%M")
    if not _is_zenjitsu:  # 終わったレースは外す
        rows = [r for r in rows if not r[4] or r[4] >= _now] or rows
    rows.sort(key=lambda x: (-x[0], x[4]))
    lis = "".join(f'<li><b>{tn}{rn}R</b> {pt} 複勝 <b>{h["fav"]}番</b>({h["fav_odds"]:.1f}倍) <span class="hp">{p1:.0%}</span>'
                  f' ・ ワイド4点 <span class="hp">{p2:.0%}</span></li>' for p1, p2, tn, rn, pt, h in rows[:6])
    return (f'<section class="aibet-summary hit-summary"><h2>的中率が高いレース</h2><ul>{lis}</ul>'
            f'<details class="why"><summary>見方</summary><p class="aibet-note">回収率は見ず、当たりやすさだけで選んだ買い目。'
            f'1番人気の単勝オッズと頭数ごとの過去の的中率(2023年〜、12,710レース)。各レースの「的中率重視」に買い目があります。'
            f'AIを混ぜるより人気順の方が当たる(1番人気の3着内率65.5%、AIの1位は58.5%)。</p></details></section>')


hit_summary = build_hit_summary()
ev_summary = ""
if not EV.empty:
    _meta = df.assign(race_id=df["race_id"].astype(str)).groupby("race_id").agg(
        tn=("track_name", "first"), rn=("race_number", "first"), pt=("post_time", "first"))
    top = EV[EV["ev"] >= 1.0].sort_values("ev", ascending=False).head(15)
    title_note = "単勝で期待値1.0以上"
    if top.empty:
        top = EV.sort_values("ev", ascending=False).head(10)
        title_note = "EV 1.0以上はなし。EVが高い順"
    lis = ""
    for i_, (_, r) in enumerate(top.iterrows()):
        m = _meta.loc[r["race_id"]]
        mv = ""
        if r["bet"] == "単勝" and pd.notna(r.get("move")) and abs(r["move"]) >= 0.1:
            mv = f' <span class="mv {"mv-in" if r["move"] > 0 else "mv-out"}">{"↓" if r["move"] > 0 else "↑"}</span>'
        if i_ == 5:
            lis += '</ul><details class="ev-more"><summary>もっと見る</summary><ul>'
        lis += (f'<li><span class="evr">{m.tn}{int(m.rn)}R</span> <b>{r["bet"]} {r["combo"]}</b> {fmt_odds(r)}倍{mv}'
                f' <span class="badge {"ev-good" if r["ev"] >= 1 else "ev-mid"}">{r["ev"]:.2f}</span></li>')
    if len(top) > 5:
        lis += "</ul></details><ul>"
    _how = ('<details class="ev-how"><summary>EVの出し方</summary><p class="aibet-note">単勝だけ。EV = 込%(オッズ込みの1着率)× 単勝オッズ。'
            '1.0を超えると、その単勝の回収率が100%を超える見込み。馬連・ワイドは組み合わせの確率が粗く当てにならないので出していません。</p></details>')
    if title_note.startswith("単勝で"):
        ev_summary = f"""<section class="aibet-summary ev-summary"><h2>EVボード <small>({title_note})</small></h2>
      <ul>{lis}</ul>{_how}</section>"""
    else:
        ev_summary = f"""<details class="aibet-summary ev-summary"><summary><b>EVボード</b> <small>今日は単勝で期待値1.0以上の馬なし(上位を見る)</small></summary>
      <ul>{lis}</ul>{_how}</details>"""


FOLD_CSS = """
  details.fold > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 6px; }
  details.fold > summary::-webkit-details-marker { display: none; }
  details.fold > summary::before { content: "▶"; font-size: .7em; color: #8A7F6E; transition: transform .15s; }
  details.fold[open] > summary::before { transform: rotate(90deg); }
  details.fold > summary h2 { display: inline; margin: 0; }
  details.fold:not([open]) { padding-bottom: 10px; }
"""


def fold(html):
    """<section class="..."><h2>見出し</h2>…</section> を、見出しを押すと開く <details>(最初は閉じた状態)にする。
    「このサイトの見方」も閉じた状態にする(11/22)"""
    import re
    html = re.sub(r'<section class="([^"]*)">\s*<h2>(.*?)</h2>',
                  lambda m: f'<details class="{m.group(1)} fold"><summary><h2>{m.group(2)}</h2></summary>', html, flags=re.S)
    html = re.sub(r'</section>', '</details>', html)
    html = html.replace('<details class="guide" open>', '<details class="guide">')
    return html


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
  .zenjitsu {{ background:#FFF3D6; border:1px solid #E2B94F; border-radius:8px; padding:8px 12px; margin:10px 0; font-size:.85rem; }}
  .bias {{ margin: 2px 0 0; font-size: .8rem; }} .bias small {{ color:#6B5F4D; }}
  .bias-in b {{ color:#1F4D3A; }} .bias-out b {{ color:#9A4A12; }} .bias-flat b {{ color:#6B5F4D; }}
  .ub-fav {{ display:inline-block; min-width:1.6em; text-align:center; border-radius:4px; background:#D9EEDF; color:#1F4D3A; font-weight:700; box-shadow:inset 0 0 0 1px #6FAE85; }}
  .ub-unfav {{ display:inline-block; min-width:1.6em; text-align:center; color:#9A8F7E; text-decoration:line-through; text-decoration-color:#C9BFAE; }}
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
  .horse {{ font-weight: 700; }} .horse .bl {{ font-weight:400; color:#6B5F4D; font-size:.72rem; }} small.plc {{ color:#6B5F4D; }}
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

  /* 出走表(スマホで横スクロールなしで読める6列) */
  table.ht {{ min-width: 0; width: 100%; font-size: .84rem; table-layout: fixed; }}
  table.ht td.horse {{ white-space: normal; overflow: hidden; word-break: break-all; }}
  table.ht .bl {{ display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  table.ht th {{ font-size: .7rem; padding: 6px 3px; }}
  table.ht th, table.ht td {{ padding: 6px 5px; vertical-align: middle; }}
  table.ht td.c {{ text-align: center; }} table.ht td.r {{ text-align: right; white-space: nowrap; }}
  table.ht .ubc {{ white-space: nowrap; }} table.ht .ubn {{ margin-left: 3px; font-size: .95rem; }}
  table.ht .sub {{ color: var(--ink-soft); font-weight: 400; font-size: .72rem; }}
  table.ht .rk {{ color: var(--ink-soft); font-size: .8rem; }}
  table.ht .pct b {{ font-size: .95rem; }}
  .guide {{ background:#fff; border:1px solid var(--paper-line); border-radius:12px; padding:10px 14px; margin:12px 0; font-size:.82rem; }}
  .trioline {{ font-size:.8rem; margin:4px 0 0; line-height:1.5; }} .trioline b {{ font-variant-numeric:tabular-nums; }}
  .tr-e2 {{ color:#C8102E; }}
  .guide summary {{ font-weight:700; color:var(--turf); cursor:pointer; }}
  .guide ul {{ margin:8px 0 2px; padding-left:1.1em; line-height:1.6; }} .guide li {{ margin:2px 0; }}
  .rule {{ border-top:1px dashed var(--paper-line); padding-top:6px; margin-top:6px; }} .rule:first-of-type {{ border-top:0; }}
  .ai-b {{ display:inline-block; font-size:.66rem; font-weight:700; border-radius:10px; padding:1px 7px; margin-left:6px; vertical-align:middle; }}
  .ai-old {{ background:#F3E6CC; color:#7A5A1E; }} .ai-new {{ background:#DDEFE4; color:#1F4D3A; }}
  .roi-b {{ font-size:.68rem; color:#8A7E6A; margin-left:6px; font-weight:500; }}
  .aibet-none {{ font-size:.78rem; color:#8A7E6A; margin:8px 0 0; }}
  .rnm {{ color:#6B6152; font-size:.8rem; }}
  table.ht .vt {{ white-space:nowrap; font-size:.68rem; padding:1px 4px; }}
  .evs {{ display:block; margin-top:2px; }} .evs .badge {{ font-size:.66rem; padding:1px 5px; }}
  .htag {{ display:inline-block; font-size:.62rem; font-weight:800; border-radius:4px; padding:1px 4px; margin-top:2px; white-space:nowrap; }}
  .tg-go {{ background:#C8102E; color:#fff; }} .tg-ok {{ background:#E6EFE9; color:#1F4D3A; }}
  .tg-warn {{ background:#FFF0C2; color:#7A5A1E; }} .tg-ng {{ background:#3A3A3A; color:#fff; }}
  .tg-push {{ background:#2E5BA8; color:#fff; }}
  table.ht .oldmk {{ display:inline-block; font-size:.62rem; color:#7A5A1E; border:1px solid #B88A2E; border-radius:4px; padding:0 3px; margin-top:2px; }}
  .aibet-note {{ font-size:.72rem; color:#8A7E6A; margin-top:4px; line-height:1.4; }}
  table.ht .mm {{ color: #2E5BA8; font-size: .72rem; font-weight: 600; }}
  table.ht .mm-hi {{ color: #C8102E; }}
  .hit-summary .hp, .hitline .hp {{ color:#1F4D3A; font-weight:800; }}
  .hitline {{ margin: 3px 0 0; font-size: .76rem; }} .hitline .hp {{ color: #1F4D3A; font-weight: 800; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 2px; }}
  .chips p {{ margin: 0; }}
  .top .meta {{ margin: 4px 0 8px; font-size: .78rem; opacity: .9; }}
  .top .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
  .top .btn {{ appearance: none; border: 0; border-radius: 999px; padding: 8px 14px; font-size: .85rem; font-weight: 700;
               background: #F0D896; color: #1F3B2C; text-decoration: none; cursor: pointer; }}
  .top .btn-sub {{ background: transparent; color: #F0D896; box-shadow: inset 0 0 0 1.5px #F0D896; }}
  .top .links {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .top .links a {{ color: #fff; font-size: .78rem; text-decoration: none; padding: 3px 9px; border-radius: 999px; background: rgba(255,255,255,.12); }}
  details.why {{ display: inline; font-size: .7rem; font-weight: 400; margin-left: 6px; }}
  details.why summary {{ display: inline; cursor: pointer; color: var(--ink-soft); }}
  .aibet-buy {{ font-weight: 700; }}
  .evr {{ color: var(--ink-soft); font-size: .76rem; }}
  details.evbox summary {{ cursor: pointer; }}
  .ev-more summary {{ cursor: pointer; font-size: .78rem; color: var(--ink-soft); margin: 4px 0; }}
  footer details summary {{ cursor: pointer; }}
{FOLD_CSS}
</style>
</head>
<body>
<header class="top">
  <div class="inner">
    <h1>JRA週末AI予想</h1>
    <p class="meta"><b>{date_label}</b> ・ 更新 {generated_at} ・ {odds_status}</p>
    <div class="actions">
      <button type="button" class="btn" onclick="location.href=location.pathname+'?t='+Date.now()">↻ 最新を表示</button>
      <a class="btn btn-sub" href="https://github.com/shogun524/jra-scraper/actions/workflows/odds_update.yml" target="_blank" rel="noopener">オッズを今すぐ取り直す</a>
    </div>
    <nav class="links"><a href="picks.html">買うべきレース</a><a href="drops.html">オッズ急落</a><a href="print.html">印刷用</a><a href="performance.html">成績</a><a href="courses.html">競馬場</a></nav>
  </div>
  <nav class="tabs">{tab_buttons}</nav>
</header>
<main>{zenjitsu_note}{fold(guide + aibet_summary + hit_summary + ev_summary)}{tab_panels}</main>
<footer><details><summary><strong>各項目の見方</strong></summary>
  <p>
    <b>1着率 / 3連対率</b>: AI(オッズを使わない予想)の推定確率。レース内で合計が100% / 300%になるよう調整。<br>
    <b>込◯%(オッズ込みの1着率)</b>: 単勝オッズから見た勝つ確率に、市場が織り込み切れていない要素(近走・前走の人気と着順・距離適性・騎手など)を足した確率。
    ◎はAIの評価のまま選び、実際に勝ちそうな確率はこちらで確かめる(2023年〜の検証で、市場・AIより確率の当たりが良い)。赤字はオッズの見込みより1.15倍以上高い馬。<br>
    <b>1位との差</b>: 1着率1位の馬との差(pt)。差が小さいレースほど混戦。<br>
    <b>安定度</b>: 3連対率÷1着率。数値が大きいほど「勝ち切れないが崩れにくい」(複勝・ワイド向き)、
    小さいほど「勝つか凡走かの一発型」(単勝向き)。<br>
    <b>印</b>: AI評価順の◎本命 ○対抗 ▲単穴 △連下。<br>
    <b>AI指数</b>: 1着率と3連対率をまとめた総合スコア。レース内トップを100として相対化。<br>
    <b>枠順評価</b>: そのコース(競馬場×芝ダ×距離)の過去実データで、その枠がどれだけ有利か。1.00が平均。<br>
    <b>人気との乖離</b>: 「人気順位 − AI評価順位」。プラスならAIが市場より高く買っている(妙味)、
    マイナスなら過剰人気の疑い。<br>
    <b>妙味</b>: オッズ+補正モデル(単勝オッズに、市場が織り込み切れていない要素を足したもの)の1着率が、オッズの見込みの何倍か。1.15倍以上=妙味あり・1.3倍以上=妙味大・0.8倍以下=過剰人気
    (2023年〜の検証で、1.15倍以上の馬の単勝回収率84%・全馬72%。100%は超えないが、穴を選ぶ目安に)。<br>
    <b>期待値</b>: 込%(オッズ込みの1着率)×単勝オッズ。1.0を超えると単勝の回収率が100%を超える見込み(11/15〜。以前はAIの1着率×オッズ)。<br>
    <b>旧◎</b>: 旧AI(前の版のAI)の1位。AI買い目(本命・準)とOP単勝は旧AIで検証したルールなので、この馬が軸になる。 オッズ未取得時は「オッズ待ち」。
  </p>
  <p>期待値が1.0を上回るほど市場価格に対して割安と推定されますが、的中や回収率のプラスを保証するものでは
  ありません(バックテストでは市場を上回るエッジは確認できていません)。馬券の最終判断はご自身でお願いします。
  枠番の色は実際の帽色に合わせています。</p>
  <p>「オッズを今すぐ取り直す」はGitHubのページが開きます。「Run workflow」を押すと1〜2分でオッズが入り、「↻ 最新を表示」で反映されます。
  開催日は5分おき・前日の夜は10分おきに自動でも取り直しています。</p>
</details></footer>
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
// 見ていた会場・レースを覚えておき、「最新を表示」や再読み込みのあとも同じレースを開く(11/22)
(function(){{
  var K = 'sel:' + location.pathname;
  function vname(b){{ return b ? (b.childNodes[0] ? b.childNodes[0].textContent : b.textContent).trim() : null; }}
  function rname(b){{ var m = b ? b.textContent.match(/(\\d+)R/) : null; return m ? m[1] : null; }}
  function save(){{
    try {{
      var t = document.querySelector('.tab-btn.active');
      var p = t ? document.getElementById(t.dataset.tab) : document;
      var r = p ? p.querySelector('.race-tab-btn.active') : null;
      sessionStorage.setItem(K, JSON.stringify({{ v: vname(t), r: rname(r) }}));
    }} catch (e) {{}}
  }}
  document.querySelectorAll('.tab-btn, .race-tab-btn').forEach(function(b){{
    b.addEventListener('click', function(e){{ if (e.isTrusted) save(); }});
  }});
  window.restoreSel = function(){{
    try {{
      var s = JSON.parse(sessionStorage.getItem(K) || 'null');
      if (!s) return false;
      var t = null;
      document.querySelectorAll('.tab-btn').forEach(function(b){{ if (vname(b) === s.v) t = b; }});
      if (t) t.click();
      var p = t ? document.getElementById(t.dataset.tab) : document;
      var r = null;
      if (p && s.r) p.querySelectorAll('.race-tab-btn').forEach(function(b){{ if (rname(b) === s.r) r = b; }});
      if (r) r.click();
      return !!(t || r);
    }} catch (e) {{ return false; }}
  }};
}})();
restoreSel();
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
# 印刷ページで選んだレースのPDFをつなげるライブラリ(CDNに頼らずサイトに置く)
try:
    import shutil
    shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "pdf-lib.min.js"),
                    os.path.join(os.path.dirname(os.path.abspath(OUT_HTML)), "pdf-lib.min.js"))
except OSError as _e:
    print(f"[WARN] pdf-lib.min.js をコピーできません: {_e}")
with open(_print, "w", encoding="utf-8") as f:
    _past = {}
    try:  # 近4走(weekly_predict.yml が past_runs.py で予想と同じ場所に作る)
        with open(os.path.join(_site_dir, "past_runs.json"), encoding="utf-8") as _pf:
            _past = json.load(_pf)
    except (OSError, ValueError):
        pass
    _ped = {}
    try:  # 出走馬の血統(weekly_predict.yml が pedigree.py runners で作る)
        with open(os.path.join(_site_dir, "runner_ped.json"), encoding="utf-8") as _pf:
            _ped = json.load(_pf)
    except (OSError, ValueError):
        pass
    f.write(build_print(df, date_label, generated_at, TRIFECTA, _past, BIAS, _ped, _latest))
print(f"{_print} を生成しました")
