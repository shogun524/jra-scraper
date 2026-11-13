"""
レース当日の途中で、終わったレースの結果(着順・馬番)だけを取りに行き history/results_<今日>.csv に足す。
当日のトラックバイアス(track_bias.py)を、その日のそれまでのレースから計算するために使う。

- 対象: 今日の予想に載っていて、発走から12分以上たち、まだ確定結果を保存していないレース
- 取得はブラウザを使わず requests で結果ページを読む(5分おきのオッズ更新の中で動かすので軽くする)。
  読めなかったレースは飛ばす(夜の results_update.yml がブラウザで取り直す)。

使い方: python src/live_results.py site/history site/predictions.csv
終了コード: 0(何もなくても0)
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_results import RESULT_COLUMNS, is_complete, rows_to_results  # noqa: E402
from scrape_race_result import RESULT_URL, parse_race_result  # noqa: E402

JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
AFTER_MIN = 12


def due_races(pred: pd.DataFrame, today: str, now: datetime) -> list[str]:
    out = []
    for rid, g in pred.groupby("race_id"):
        pt = str(g["post_time"].iloc[0] or "")
        try:
            hh, mm = map(int, pt.split(":")[:2])
        except ValueError:
            continue
        post = datetime.strptime(today, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=JST)
        if now >= post + timedelta(minutes=AFTER_MIN):
            out.append(str(rid))
    return out


def fetch_html(rid: str) -> str:
    r = requests.get(RESULT_URL.format(race_id=rid), headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    for enc in ("euc-jp", "utf-8"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.text


def main():
    hist, pred_csv = sys.argv[1], sys.argv[2]
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    try:
        pred = pd.read_csv(pred_csv, dtype={"race_id": str})
    except (OSError, ValueError):
        print("[INFO] 予想がありません")
        return
    if "race_date" in pred.columns and not pred["race_date"].astype(str).str.startswith(today).any():
        print(f"[INFO] 今日({today})の予想ではありません")
        return
    out = os.path.join(hist, f"results_{today}.csv")
    have = pd.read_csv(out, dtype={"race_id": str}) if os.path.exists(out) else pd.DataFrame(columns=RESULT_COLUMNS)
    done = {rid for rid, g in have.groupby("race_id") if is_complete(g)}
    todo = [r for r in due_races(pred, today, now) if r not in done]
    print(f"[INFO] 結果を取りに行くレース: {len(todo)}")
    frames = []
    for rid in todo:
        try:
            res = rows_to_results(parse_race_result(fetch_html(rid), rid), rid)
        except Exception as e:  # noqa: BLE001
            print(f"  {rid}: 取得失敗 ({e})")
            continue
        if is_complete(res):
            frames.append(res)
            print(f"  {rid}: 1着={res.loc[res.finish == 1, 'umaban'].tolist()}")
        else:
            print(f"  {rid}: まだ確定していない/読めない")
        time.sleep(0.5)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        merged = pd.concat([have[~have["race_id"].isin(new["race_id"])], new], ignore_index=True).sort_values(["race_id", "umaban"])
        os.makedirs(hist, exist_ok=True)
        merged.to_csv(out, index=False)
        print(f"[INFO] {out} に保存({merged['race_id'].nunique()}レース)")


if __name__ == "__main__":
    main()
