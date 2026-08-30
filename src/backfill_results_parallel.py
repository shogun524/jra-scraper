"""
JRA直近実績バックフィル・スクリプト(並列版)
backfill_results.py と同じ処理を、複数レースを同時並行で取得することで高速化する。

安全策:
- 同時実行数は --concurrency で制限(デフォルト5)
- 1件ごとにランダムな待機(0.5〜1.5秒)を入れて、機械的な一定間隔アクセスに見えないようにする
- 失敗したら3回まで自動リトライ(指数バックオフ)
- 連続エラーが10件たまったら60秒クールダウン(ブロックの可能性があるため一旦様子見する)
- 進捗はbackfill_results.pyと同じ data/backfill_progress.json に保存 -> 中断しても再開可能

使い方:
  python src/backfill_results_parallel.py 2021-08-01 2026-08-30 --concurrency 5

【注意】並列化により1レースあたりのアクセス間隔は短くなるため、逐次版よりも
netkeiba側のBot対策(403エラー等)に引っかかるリスクは上がります。
エラーが増え続ける場合は --concurrency を3などに下げて再実行してください。
"""
import argparse
import asyncio
import csv
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from scrape_race_result import parse_race_result, RESULT_URL
from backfill_results import TRACK_NAMES, OUT_COLUMNS, OUT_CSV, PROGRESS_FILE, load_progress, save_progress
from playwright.async_api import async_playwright

RACE_LIST_URL = "https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


async def get_race_ids_for_date(page, date_str: str) -> list[str]:
    await page.goto(RACE_LIST_URL.format(date=date_str), timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    html = await page.content()
    ids = sorted(set(re.findall(r"race_id=(\d{12})", html)))
    return [i for i in ids if 1 <= int(i[4:6]) <= 10]


async def fetch_one_race(browser, race_id, date_str, sem, writer_lock, writer, done, csv_file, stats):
    async with sem:
        for attempt in range(3):
            page = None
            try:
                async def _fetch():
                    nonlocal page
                    page = await browser.new_page(user_agent=UA)
                    await page.goto(RESULT_URL.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(int(random.uniform(800, 1500)))
                    return await page.content()

                # Playwright自体のtimeoutに加えて、外側にも保険のtimeoutをかける
                # (ネットワーク状況によってはPlaywright側のtimeoutが効かず無限待ちになることがあるため)
                html = await asyncio.wait_for(_fetch(), timeout=45)

                rows = parse_race_result(html, race_id)
                track_code = race_id[4:6]
                async with writer_lock:
                    for r in rows:
                        r["レースID"] = race_id
                        r["レース日付"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                        r["競馬場コード"] = track_code
                        r["競馬場名"] = TRACK_NAMES.get(track_code, "")
                        r["レース馬番ID"] = f"{race_id}{str(r.get('馬番') or '00').zfill(2)}"
                        writer.writerow({k: r.get(k) for k in OUT_COLUMNS})
                    csv_file.flush()
                    done.add(race_id)
                    if len(done) % 20 == 0:
                        save_progress(done)  # 20件ごとにもチェックポイント保存(日付完了を待たない)
                stats["ok"] += 1
                stats["consecutive_err"] = 0
                await asyncio.sleep(random.uniform(0.5, 1.5))
                return
            except Exception as e:
                stats["consecutive_err"] += 1
                if attempt == 2:
                    stats["err"] += 1
                    print(f"[{race_id}] 3回リトライしても失敗: {e}")
                else:
                    await asyncio.sleep(2 * (attempt + 1))
            finally:
                # 成功・失敗にかかわらず、開いたページは必ず閉じる(閉じ忘れによるリーク・フリーズ対策)
                # close()自体が固まることがあるため、これにもタイムアウトを付けて絶対にブロックしない
                if page is not None:
                    try:
                        await asyncio.wait_for(page.close(), timeout=10)
                    except Exception:
                        pass

        if stats["consecutive_err"] >= 10:
            print("!! 連続エラーが多いため60秒クールダウンします(ブロックされている可能性があります)")
            await asyncio.sleep(60)
            stats["consecutive_err"] = 0


DATES_DONE_FILE = "data/backfill_dates_done.json"


def load_dates_done() -> set:
    if os.path.exists(DATES_DONE_FILE):
        with open(DATES_DONE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_dates_done(dates_done: set):
    with open(DATES_DONE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(dates_done), f)


async def run(start_date: str, end_date: str, concurrency: int):
    done = load_progress()
    dates_done = load_dates_done()
    file_exists = os.path.exists(OUT_CSV)
    dates = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end:
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    sem = asyncio.Semaphore(concurrency)
    writer_lock = asyncio.Lock()
    stats = {"ok": 0, "err": 0, "consecutive_err": 0}

    with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        if not file_exists:
            writer.writeheader()

        async with async_playwright() as p:
            async def new_browser():
                b = await p.chromium.launch(headless=True)
                lp = await b.new_page(user_agent=UA)
                return b, lp

            browser, list_page = await new_browser()
            worked_dates_since_restart = 0
            RESTART_EVERY = 15  # このダウンロード件数を処理するごとにブラウザを作り直し、長時間起動による劣化を防ぐ

            for i, date_str in enumerate(dates):
                if date_str in dates_done:
                    # 完全に処理済みの日付はレース一覧の問い合わせ自体を省略(再開を高速化)
                    if i % 50 == 0:
                        print(f"...{date_str} まで処理済み分をスキップ中(進捗確認)")
                    continue
                try:
                    race_ids = await asyncio.wait_for(get_race_ids_for_date(list_page, date_str), timeout=45)
                except Exception as e:
                    print(f"[{date_str}] レース一覧取得エラー: {e} -> このページを作り直して次の日付へ")
                    try:
                        await asyncio.wait_for(list_page.close(), timeout=10)
                    except Exception:
                        pass
                    list_page = await browser.new_page(user_agent=UA)  # ページが壊れている可能性があるので作り直す
                    continue
                await asyncio.sleep(1.5)

                todo = [r for r in race_ids if r not in done]
                if todo:
                    print(f"{date_str}: {len(todo)}レース処理開始(同時実行={concurrency})")
                    tasks = [
                        fetch_one_race(browser, rid, date_str, sem, writer_lock, writer, done, f, stats)
                        for rid in todo
                    ]
                    await asyncio.gather(*tasks)
                    save_progress(done)
                    print(f"  -> 累計 成功={stats['ok']}  エラー={stats['err']}")
                    worked_dates_since_restart += 1

                dates_done.add(date_str)
                save_dates_done(dates_done)

                if worked_dates_since_restart >= RESTART_EVERY:
                    print("  -> 長時間起動による劣化を防ぐため、ブラウザを再起動します...")
                    try:
                        await asyncio.wait_for(browser.close(), timeout=15)
                    except Exception:
                        pass
                    browser, list_page = await new_browser()
                    worked_dates_since_restart = 0

            await browser.close()

    print(f"\n完了。成功={stats['ok']}レース, エラー={stats['err']}レース -> {OUT_CSV}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args.start, args.end, args.concurrency))
