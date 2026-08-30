"""
指定日のJRAレースID一覧を取得する。
netkeiba: https://race.netkeiba.com/top/race_list.html?kaisai_date=YYYYMMDD

【注意】このスクリプトはnetkeiba.comへの実アクセスをこちらの開発環境(ネットワーク制限あり)で
検証できていません。まずは診断用に diagnose_scraper.py を1レースだけ実行し、
取得できたHTML/件数を見ながら調整してください(南関東スクレイパーの時と同じ進め方を想定)。
"""
from playwright.sync_api import sync_playwright
import re
import time

RACE_LIST_URL = "https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def get_race_ids_for_date(page, date_str: str) -> list[str]:
    """date_str: 'YYYYMMDD'. 戻り値: race_id(12桁文字列)のリスト"""
    url = RACE_LIST_URL.format(date=date_str)
    page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)  # レンダリング待ち。開催がない日はレース一覧が空のまま
    html = page.content()
    ids = sorted(set(re.findall(r"race_id=(\d{12})", html)))
    # 中央競馬(JRA)のみに絞る: 場コード 01〜10 のみ(11以降は地方競馬)
    jra_ids = [i for i in ids if 1 <= int(i[4:6]) <= 10]
    return jra_ids


def get_race_ids_for_range(start_date: str, end_date: str, delay_sec: float = 2.0) -> dict:
    """start_date, end_date: 'YYYY-MM-DD'. 戻り値: {date_str: [race_id, ...]}"""
    import pandas as pd
    dates = pd.date_range(start_date, end_date, freq="D")
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        for d in dates:
            date_str = d.strftime("%Y%m%d")
            try:
                ids = get_race_ids_for_date(page, date_str)
                if ids:
                    result[date_str] = ids
                    print(f"{date_str}: {len(ids)}レース")
            except Exception as e:
                print(f"{date_str}: ERROR {e}")
            time.sleep(delay_sec)  # レート制限対策(南関東スクレイパーで403の原因になった経緯があるため保守的に)
        browser.close()
    return result


if __name__ == "__main__":
    import sys, json
    start, end = sys.argv[1], sys.argv[2]
    result = get_race_ids_for_range(start, end)
    with open("data/race_ids.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in result.values())
    print(f"合計 {total} レース分のIDを data/race_ids.json に保存しました")
