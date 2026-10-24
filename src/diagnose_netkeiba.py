"""
netkeiba の取得状況を診断する(バックフィルで2023年以降のレース一覧が0件になる原因の切り分け用)。

各日付について、レース一覧ページ(race_list.html と、その中身を返す race_list_sub.html)を開き、
HTTPステータス・最終URL・タイトル・race_id の件数・本文の冒頭を記録する。
各レースIDについて、結果ページを開いて結果表が取れるか・タイトル(日付入り)を記録する。

使い方:
    python src/diagnose_netkeiba.py --dates 20221231 20230212 20240107 --races 202305010101 202306010101 --out diag
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_race_result import parse_race_result  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LIST_URLS = {
    "race_list": "https://race.netkeiba.com/top/race_list.html?kaisai_date={d}",
    "race_list_sub": "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={d}",
}
RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={r}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=[])
    ap.add_argument("--races", nargs="+", default=[])
    ap.add_argument("--out", default="diag")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    from playwright.sync_api import sync_playwright

    lines = []

    def log(s):
        print(s)
        lines.append(s)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        for d in a.dates:
            for name, url in LIST_URLS.items():
                u = url.format(d=d)
                try:
                    resp = page.goto(u, timeout=30000, wait_until="load")
                    page.wait_for_timeout(2500)
                    html = page.content()
                    ids = sorted(set(re.findall(r"race_id=(\d{12})", html)))
                    ids_any = sorted(set(re.findall(r"(20\d{2}(?:0[1-9]|10)\d{6})", html)))
                    text = page.inner_text("body")[:600].replace("\n", " / ")
                    log(f"[{d} {name}] status={resp.status if resp else None} url={page.url} title={page.title()!r} "
                        f"len={len(html)} race_id={len(ids)} {ids[:3]} 12桁候補={len(ids_any)}")
                    log(f"    本文冒頭: {text}")
                    with open(f"{a.out}/{d}_{name}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    if name == "race_list":
                        page.screenshot(path=f"{a.out}/{d}_{name}.png", full_page=True)
                except Exception as e:
                    log(f"[{d} {name}] ERROR {e}")
        for r in a.races:
            try:
                resp = page.goto(RESULT_URL.format(r=r), timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                html = page.content()
                rows = parse_race_result(html, r)
                log(f"[result {r}] status={resp.status if resp else None} title={page.title()!r} 行数={len(rows)} "
                    f"例={[(x.get('着順'), x.get('馬名'), x.get('競争条件')) for x in rows[:2]]}")
                with open(f"{a.out}/result_{r}.html", "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception as e:
                log(f"[result {r}] ERROR {e}")
        browser.close()
    with open(f"{a.out}/summary.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
