"""
予測履歴(history/predictions_YYYY-MM-DD.csv)に載っているレースの「確定結果」を取得し、
history/results_YYYY-MM-DD.csv として保存する。

- 予測の成績ページ(generate_performance.py)は、この結果ファイルと予測履歴を突き合わせて作る。
- すでに全レース分の結果が揃っている日はスキップするので、何度実行しても無駄なアクセスは増えない。
- 発走前・確定前のレースは「着順が取れない」ので保存しない(次回の実行で再取得される)。

使い方:
    python src/fetch_results.py site/history            # 未取得の日をすべて
    python src/fetch_results.py site/history 2026-10-24 # 指定日だけ

終了コード: 0=新しく保存した / 2=取得対象なし(正常)
"""
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_race_result import fetch_race_page, parse_race_result  # noqa: E402

RESULT_COLUMNS = ["race_id", "umaban", "finish", "popularity", "odds_final", "horse"]


def _to_int(v):
    """着順・馬番・人気を整数に。取消/除外/中止などは None。"""
    m = re.match(r"\s*(\d+)", str(v))
    return int(m.group(1)) if m else None


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def rows_to_results(rows: list[dict], race_id: str) -> pd.DataFrame:
    out = []
    for r in rows:
        umaban = _to_int(r.get("馬番"))
        if umaban is None:
            continue
        out.append({
            "race_id": race_id,
            "umaban": umaban,
            "finish": _to_int(r.get("着順")),  # 取消・除外・中止は空欄
            "popularity": _to_int(r.get("人気")),
            "odds_final": _to_float(r.get("単勝")),
            "horse": r.get("馬名"),
        })
    return pd.DataFrame(out, columns=RESULT_COLUMNS)


def is_complete(res: pd.DataFrame) -> bool:
    """1着が決まっていれば確定済みとみなす。"""
    return len(res) > 0 and (res["finish"] == 1).any()


def target_dates(history_dir: str, only: list[str]) -> list[str]:
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    dates = []
    for f in sorted(glob.glob(os.path.join(history_dir, "predictions_*.csv"))):
        d = os.path.basename(f)[len("predictions_"):-len(".csv")]
        if only and d not in only:
            continue
        if d > today:  # 未来の事前予測はまだ結果がない
            continue
        dates.append(d)
    return dates


def missing_race_ids(history_dir: str, date: str) -> tuple[list[str], pd.DataFrame]:
    pred = pd.read_csv(os.path.join(history_dir, f"predictions_{date}.csv"), dtype={"race_id": str})
    race_ids = sorted(pred["race_id"].unique())
    res_path = os.path.join(history_dir, f"results_{date}.csv")
    if os.path.exists(res_path):
        have = pd.read_csv(res_path, dtype={"race_id": str})
    else:
        have = pd.DataFrame(columns=RESULT_COLUMNS)
    done = {rid for rid, g in have.groupby("race_id") if is_complete(g)}
    return [rid for rid in race_ids if rid not in done], have


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    history_dir = sys.argv[1]
    only = sys.argv[2:]

    jobs = {}
    for d in target_dates(history_dir, only):
        ids, have = missing_race_ids(history_dir, d)
        if ids:
            jobs[d] = (ids, have)
    if not jobs:
        print("結果の取得対象はありません(すべて取得済み)")
        sys.exit(2)

    from playwright.sync_api import sync_playwright

    saved_any = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for d, (ids, have) in jobs.items():
            new_frames = []
            for rid in ids:
                try:
                    html = fetch_race_page(page, rid)
                    res = rows_to_results(parse_race_result(html, rid), rid)
                except Exception as e:  # 1レースの失敗で全体を止めない
                    print(f"  {rid}: 取得失敗 ({e})")
                    continue
                if is_complete(res):
                    new_frames.append(res)
                    print(f"  {rid}: {len(res)}頭 1着={res.loc[res.finish == 1, 'umaban'].tolist()}")
                else:
                    print(f"  {rid}: まだ確定していません")
            if not new_frames:
                continue
            new = pd.concat(new_frames, ignore_index=True)
            keep = have[~have["race_id"].isin(new["race_id"])]
            merged = pd.concat([keep, new], ignore_index=True).sort_values(["race_id", "umaban"])
            out = os.path.join(history_dir, f"results_{d}.csv")
            merged.to_csv(out, index=False)
            print(f"{out} に保存 ({merged['race_id'].nunique()}レース)")
            saved_any = True
        browser.close()

    sys.exit(0 if saved_any else 2)


if __name__ == "__main__":
    main()
