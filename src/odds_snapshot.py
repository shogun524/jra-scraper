"""
オッズの時系列記録(10分ごとの定期実行用)+ 馬体重の更新 + EVの記録。

update_odds.py が predictions.csv の単勝オッズを最新化したあとに実行する。
発走前のレースについて、
  1. 単勝(predictions.csv の最新値)・馬連・ワイド(netkeiba オッズAPI type=4/5)のオッズを
     site/odds_history/odds_<日付>.csv に追記し、最新値を latest_<日付>.csv に保存する
     → オッズの動き(直前に買われた馬)を分析・EVに反映するためのデータ
  2. 発走80分前を過ぎて馬体重が未取得のレースは、出馬表を取り直して馬体重・増減を predictions.csv に入れる
  3. その時点の EV(ev_board.py)を site/ev_log/ev_<日付>.csv に保存する(発走前の最後の値が残る)
     → 成績ページで「EV≧1.0として表示した買い目」を実際の払戻で採点する

APIの応答の形は公開されていないため、解析に失敗した場合は生の応答を raw_<日付>_type<n>.txt に残す。

使い方:
    python src/odds_snapshot.py site/predictions.csv site
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ev_board import board, load_calibration  # noqa: E402

JST = timezone(timedelta(hours=9))
ODDS_API = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type={type}"
SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
PAIR_TYPES = {4: "馬連", 5: "ワイド"}
HIST_COLS = ["ts", "race_id", "bet", "combo", "odds", "odds_high", "pop"]


def http_get(url, referer, timeout=15, encoding=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer, "Accept": "*/*", "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    if encoding:
        return raw.decode(encoding, errors="replace")
    for enc in ("utf-8", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if re.fullmatch(r"\d+(?:\.\d+)?", s):
            return float(s)
    return None


def _pair_key(k):
    """'0102' / '01-02' / '1_2' -> (1, 2)。組でなければ None"""
    if not isinstance(k, str):
        return None
    m = re.fullmatch(r"(\d{2})(\d{2})", k) or re.fullmatch(r"(\d{1,2})\D(\d{1,2})", k)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if not (1 <= a <= 18 and 1 <= b <= 18) or a == b:
        return None
    return (min(a, b), max(a, b))


def _values(v):
    """値から [オッズ, (上限), 人気] を取り出す。配列・文字列・辞書のどれでも対応"""
    if isinstance(v, (list, tuple)):
        nums = [_num(x) for x in v]
    elif isinstance(v, dict):
        nums = [_num(x) for x in v.values()]
    else:
        nums = [_num(v)]
    return [x for x in nums if x is not None]


def parse_pair_odds(raw: str, bet: str) -> dict:
    """オッズAPIの応答 -> {(a, b): (オッズ, 上限, 人気)}。構造は決め打ちせず、組のキーを持つ辞書を探す"""
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    data = obj.get("data") if isinstance(obj, dict) else obj
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}
    best = {}

    def walk(node):
        nonlocal best
        if isinstance(node, dict):
            cand = {}
            for k, v in node.items():
                pk = _pair_key(k)
                if pk is None:
                    continue
                vals = _values(v)
                if not vals or not (1.0 <= vals[0] <= 100000):
                    continue
                if bet == "ワイド" and len(vals) >= 2 and vals[1] >= vals[0]:
                    lo, hi = vals[0], vals[1]
                    pop = vals[2] if len(vals) >= 3 else np.nan
                else:
                    lo, hi = vals[0], np.nan
                    pop = vals[-1] if len(vals) >= 2 and vals[-1] == int(vals[-1]) else np.nan
                cand[pk] = (lo, hi, pop)
            if len(cand) > len(best):
                best = cand
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return best


def is_upcoming(post_time, now, grace_min=5):
    if not isinstance(post_time, str) or ":" not in post_time:
        return True
    h, m = map(int, post_time.split(":")[:2])
    return now.replace(hour=h, minute=m, second=0, microsecond=0) >= now - timedelta(minutes=grace_min)


def minutes_to_post(post_time, now):
    if not isinstance(post_time, str) or ":" not in post_time:
        return None
    h, m = map(int, post_time.split(":")[:2])
    return (now.replace(hour=h, minute=m, second=0, microsecond=0) - now).total_seconds() / 60


def refresh_weights(df, rid, race_date, log):
    """出馬表を取り直して馬体重・増減を更新する(発表済みの場合)"""
    from scrape_entries import parse_shutuba
    try:
        html = http_get(SHUTUBA_URL.format(race_id=rid), referer="https://race.netkeiba.com/")
        rows = parse_shutuba(html, rid, race_date)
    except Exception as e:
        log(f"    [{rid}] 出馬表の取得失敗: {e}")
        return 0
    w = {int(r["umaban"]): (r.get("horse_weight"), r.get("weight_diff")) for r in rows if r.get("umaban")}
    mask = df["race_id"].astype(str) == rid
    n = 0
    for i in df.index[mask]:
        hw, dw = w.get(int(df.at[i, "umaban"]), (None, None))
        if hw:
            df.at[i, "horse_weight"] = hw
            df.at[i, "weight_diff"] = dw
            n += 1
    return n


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pred_csv, site = sys.argv[1], sys.argv[2]
    log = print
    if not os.path.exists(pred_csv):
        log("予測ファイルがありません")
        return 2
    df = pd.read_csv(pred_csv, dtype={"race_id": str})
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    if "race_date" in df.columns and not (df["race_date"].astype(str) == today).any():
        log(f"今日({today})の予測ではないので終了")
        return 2
    for c in ("horse_weight", "weight_diff"):
        if c not in df.columns:
            df[c] = np.nan
    pt = df.groupby("race_id")["post_time"].first() if "post_time" in df.columns else pd.Series(dtype=str)
    races = [r for r in dict.fromkeys(df["race_id"]) if is_upcoming(pt.get(r), now)]
    log(f"{now:%H:%M} 記録対象 {len(races)}レース")
    if not races:
        return 2

    odir = os.path.join(site, "odds_history")
    os.makedirs(odir, exist_ok=True)
    ts = now.strftime("%Y-%m-%d %H:%M")
    rows = []
    raw_saved = {t: os.path.exists(os.path.join(odir, f"raw_{today}_type{t}.txt")) for t in PAIR_TYPES}
    weights_updated = 0
    for rid in races:
        g = df[df["race_id"] == rid]
        for _, r in g.iterrows():
            if pd.notna(r.get("odds_win")):
                rows.append((ts, rid, "単勝", str(int(r["umaban"])), float(r["odds_win"]), np.nan, r.get("market_rank", np.nan)))
        for t, bet in PAIR_TYPES.items():
            try:
                raw = http_get(ODDS_API.format(race_id=rid, type=t),
                               referer=f"https://race.netkeiba.com/odds/index.html?type=b{t}&race_id={rid}")
            except Exception as e:
                log(f"  [{rid}] {bet}オッズ取得失敗: {e}")
                continue
            pairs = parse_pair_odds(raw, bet)
            if not raw_saved[t] or not pairs:
                # 応答の形を後から確認できるよう、その日の最初の応答と、解析できなかった応答を残す
                with open(os.path.join(odir, f"raw_{today}_type{t}.txt"), "a", encoding="utf-8") as f:
                    f.write(f"--- {ts} {rid} 解析{len(pairs)}組\n{raw[:4000]}\n")
                raw_saved[t] = True
            for (a, b), (lo, hi, pop) in pairs.items():
                rows.append((ts, rid, bet, f"{a}-{b}", lo, hi, pop))
            time.sleep(0.4)
        mtp = minutes_to_post(pt.get(rid), now)
        if mtp is not None and mtp <= 80 and g["horse_weight"].isna().all():
            weights_updated += refresh_weights(df, rid, today, log)
            time.sleep(0.4)
        log(f"  [{rid}] {int(rid[-2:])}R 記録 {sum(1 for x in rows if x[1] == rid)}件")

    new = pd.DataFrame(rows, columns=HIST_COLS)
    hist_path = os.path.join(odir, f"odds_{today}.csv")
    hist = pd.concat([pd.read_csv(hist_path, dtype={"race_id": str, "combo": str}), new], ignore_index=True) \
        if os.path.exists(hist_path) else new
    hist.to_csv(hist_path, index=False)
    latest = hist.sort_values("ts").groupby(["race_id", "bet", "combo"], as_index=False).last()
    latest.to_csv(os.path.join(odir, f"latest_{today}.csv"), index=False)
    if weights_updated:
        df.to_csv(pred_csv, index=False)
        log(f"馬体重を {weights_updated}頭分更新")

    # その時点のEVを記録(発走前のレースだけ上書き = 発走前最後の値が残る)
    ev = board(df[df["race_id"].isin(races)], latest[latest["bet"] != "単勝"], hist, load_calibration())
    if not ev.empty:
        ev["ts"] = ts
        edir = os.path.join(site, "ev_log")
        os.makedirs(edir, exist_ok=True)
        ep = os.path.join(edir, f"ev_{today}.csv")
        if os.path.exists(ep):
            old = pd.read_csv(ep, dtype={"race_id": str, "combo": str})
            ev = pd.concat([old[~old["race_id"].isin(races)], ev], ignore_index=True)
        ev.to_csv(ep, index=False)
    log(f"完了: オッズ {len(new)}件 / EV {len(ev)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
