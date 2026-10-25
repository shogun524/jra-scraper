"""
払戻(配当)のバックフィル。race_result_merged.csv にあるレースIDの結果ページを開き、
払戻表を data/payouts.csv に1組1行で追記していく。

- レースIDは既存データから取るので、取りこぼしやすいレース一覧ページは使わない
- 既に payouts.csv にあるレースは飛ばすので、途中で止まっても続きから再開できる
- --max-minutes を過ぎるか、失敗が連続したら打ち切る(終了コード3 = 続きあり)

使い方:
    python src/fetch_payouts.py data/race_result_merged.csv data/payouts.csv --since 2024-01-01 --max-minutes 300
終了コード: 0=対象をすべて取得 / 3=途中で打ち切り(再実行で続きから)
"""
import argparse
import asyncio
import csv
import os
import random
import sys
import time

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_race_result import PAYOUT_COLUMNS, RESULT_URL, parse_payouts  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FAIL_STREAK_LIMIT = 25  # 連続でこれだけ失敗したら、アクセスが制限されたと判断して打ち切る


def target_race_ids(merged_csv, out_csv, since, until):
    con = duckdb.connect()
    until_sql = f"AND \"レース日付\" <= '{until}'" if until else ""
    q = f"""SELECT DISTINCT "レースID" FROM read_csv_auto('{merged_csv}', all_varchar=true, header=true)
            WHERE "レース日付" >= '{since}' {until_sql}
            ORDER BY 1"""
    ids = [r[0] for r in con.execute(q).fetchall()]
    have = set()
    if os.path.exists(out_csv) and os.path.getsize(out_csv) > 0:
        have = {r[0] for r in con.execute(
            f"SELECT DISTINCT race_id FROM read_csv_auto('{out_csv}', all_varchar=true, header=true)").fetchall()}
    return [i for i in ids if i not in have], len(ids)


async def run(ids, out_csv, concurrency, max_minutes):
    from playwright.async_api import async_playwright
    t0 = time.time()
    stats = {"ok": 0, "err": 0, "streak": 0, "stop": False}
    lock = asyncio.Lock()
    new_file = not os.path.exists(out_csv) or os.path.getsize(out_csv) == 0
    f = open(out_csv, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=PAYOUT_COLUMNS)
    if new_file:
        w.writeheader()
    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def one(rid):
            if stats["stop"]:
                return
            async with sem:
                if stats["stop"]:
                    return
                if max_minutes and (time.time() - t0) / 60 > max_minutes:
                    stats["stop"] = True
                    return
                for attempt in range(3):
                    page = None
                    try:
                        page = await browser.new_page(user_agent=UA)
                        await asyncio.wait_for(page.goto(RESULT_URL.format(race_id=rid), timeout=30000,
                                                         wait_until="domcontentloaded"), timeout=40)
                        await page.wait_for_timeout(int(random.uniform(600, 1200)))
                        rows = parse_payouts(await page.content(), rid)
                        if not rows:
                            raise ValueError("払戻表が見つかりません")
                        async with lock:
                            for r in rows:
                                w.writerow(r)
                            f.flush()
                        stats["ok"] += 1
                        stats["streak"] = 0
                        if stats["ok"] % 200 == 0:
                            print(f"  {stats['ok']}レース取得 ({(time.time() - t0) / 60:.0f}分)", flush=True)
                        await asyncio.sleep(random.uniform(0.3, 1.0))
                        return
                    except Exception as e:
                        if attempt == 2:
                            stats["err"] += 1
                            stats["streak"] += 1
                            print(f"  [{rid}] 失敗: {e}", flush=True)
                            if stats["streak"] >= FAIL_STREAK_LIMIT:
                                print("!! 失敗が続いたので打ち切ります(次の実行で続きから)", flush=True)
                                stats["stop"] = True
                        else:
                            await asyncio.sleep(2 * (attempt + 1))
                    finally:
                        if page is not None:
                            try:
                                await asyncio.wait_for(page.close(), timeout=10)
                            except Exception:
                                pass

        # 一度に全部タスク化せず、少しずつ流す(打ち切り時に無駄なタスクを溜めない)
        batch = concurrency * 20
        for i in range(0, len(ids), batch):
            if stats["stop"]:
                break
            await asyncio.gather(*(one(r) for r in ids[i:i + batch]))
        await browser.close()
    f.close()
    print(f"成功={stats['ok']} 失敗={stats['err']} ({(time.time() - t0) / 60:.0f}分)")
    return not stats["stop"] and stats["err"] == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("merged_csv")
    ap.add_argument("out_csv")
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--until", default="")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--max-minutes", type=float, default=None)
    a = ap.parse_args()
    ids, total = target_race_ids(a.merged_csv, a.out_csv, a.since, a.until)
    print(f"対象 {total}レース中、未取得 {len(ids)}レース")
    if not ids:
        sys.exit(0)
    ok = asyncio.run(run(ids, a.out_csv, a.concurrency, a.max_minutes))
    ids_left, _ = target_race_ids(a.merged_csv, a.out_csv, a.since, a.until)
    print(f"残り {len(ids_left)}レース")
    # 中止レースなど払戻が無いレースもあるので、打ち切りでなく残りがごく少数なら完了扱い
    sys.exit(0 if len(ids_left) <= 20 else 3)


if __name__ == "__main__":
    main()
