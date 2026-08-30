"""
JRA直近実績バックフィル・スクリプト
data/race_result.csv (〜2021-07-31) の後に続く期間の結果をnetkeibaから取得し、
data/race_result_new.csv に追記していく。取得後は既存のrace_result.csvと結合して
feature_engineering.py を再実行することで、モデルが「今の」馬の状態を学習・参照できるようになる。

【重要な注意】
このスクリプトはこちらの開発環境(ネットワーク制限あり)からnetkeiba.comへ実アクセスして
検証できていません。南関東スクレイパーの開発時と同様に、まず --diagnose で1レースだけ試し、
出力を見ながら scrape_race_result.py の COLUMN_MAP やセレクタを調整してください。

使い方:
  python src/backfill_results.py --diagnose 202604030408        # 1レースだけ試す
  python src/backfill_results.py 2021-08-01 2026-08-30           # 本実行(途中中断しても再開可能)
"""
import argparse
import json
import time
import csv
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from scrape_race_ids import get_race_ids_for_date, UA
from scrape_race_result import fetch_race_page, parse_race_result, RESULT_URL as RESULT_URL_FOR_DIAG

TRACK_NAMES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
OUT_CSV = "data/race_result_new.csv"
PROGRESS_FILE = "data/backfill_progress.json"
OUT_COLUMNS = [
    "レース馬番ID", "レースID", "レース日付", "競馬場コード", "競馬場名",
    "芝・ダート区分", "距離(m)", "馬場状態1", "天候",
    "枠番", "馬番", "馬名", "性別", "馬齢", "斤量", "騎手",
    "着順", "タイム", "着差", "1コーナー", "2コーナー", "3コーナー", "4コーナー",
    "上り", "単勝", "人気", "馬体重", "場体重増減", "調教師",
]


def load_progress() -> set:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(done: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)


def diagnose(race_id: str):
    """1レースだけ取得して中身を確認する"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        page.goto(RESULT_URL_FOR_DIAG.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        title = page.title()
        html = page.content()
        # スクリーンショットも保存しておく(実際に何が表示されているか目で見て確認できる)
        page.screenshot(path="data/diagnose_screenshot.png", full_page=True)
        browser.close()

    print(f"ページタイトル: {title}")
    print(f"HTML長: {len(html)} 文字")
    print(f"data/diagnose_screenshot.png にスクリーンショットを保存しました(開いて実際の画面を確認してください)")

    import re
    idx = html.find("着順")
    if idx == -1:
        print("!! HTML中に「着順」という文字列が見つかりません。")
        print("!! ありがちな原因: (1)結果がまだ確定していない(発走前・確定処理中) "
              "(2)Bot対策で別ページに転送/ブロックされている (3)会員限定表示になっている")
        # エラーダイアログや案内文らしきテキストを探す
        for kw in ["エラー", "見つかりません", "ログイン", "会員", "しばらくお待ち"]:
            if kw in html:
                pos = html.find(kw)
                print(f"  -> 「{kw}」を検出: ...{html[max(0,pos-80):pos+80]}...")
        n_tables = len(re.findall(r"<table", html))
        print(f"!! <table>タグの数: {n_tables}")
        return

    print(f"!! 「着順」という文字列は見つかりました(位置 {idx})。")
    print("周辺のHTML(これがナビメニューのリンクなら本物のテーブルはこれではありません):")
    print(html[max(0, idx-300):idx+500])

    print("\n--- pandas.read_html で見つかった全テーブル一覧 ---")
    import io
    tables = pd.read_html(io.StringIO(html))
    print(f"テーブル数: {len(tables)}")
    for i, t in enumerate(tables):
        cols = list(t.columns)[:8]
        print(f"[{i}] shape={t.shape}  columns(先頭8個)={cols}")

    rows = parse_race_result(html, race_id)
    print(f"\nparse_race_result()の結果: {len(rows)}頭分")
    for r in rows[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))


def run(start_date: str, end_date: str, delay_sec: float = 2.5):
    from playwright.sync_api import sync_playwright
    from tqdm import tqdm

    done = load_progress()
    file_exists = os.path.exists(OUT_CSV)
    dates = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end:
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    n_ok, n_err = 0, 0
    with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        if not file_exists:
            writer.writeheader()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA)

            for date_str in tqdm(dates, desc="日付"):
                try:
                    race_ids = get_race_ids_for_date(page, date_str)
                except Exception as e:
                    print(f"[{date_str}] レース一覧取得エラー: {e}")
                    n_err += 1
                    continue
                time.sleep(delay_sec)

                for race_id in race_ids:
                    if race_id in done:
                        continue
                    try:
                        html = fetch_race_page(page, race_id)
                        rows = parse_race_result(html, race_id)
                        track_code = race_id[4:6]
                        for r in rows:
                            r["レースID"] = race_id
                            r["レース日付"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                            r["競馬場コード"] = track_code
                            r["競馬場名"] = TRACK_NAMES.get(track_code, "")
                            r["レース馬番ID"] = f"{race_id}{str(r.get('馬番') or '00').zfill(2)}"
                            writer.writerow({k: r.get(k) for k in OUT_COLUMNS})
                        f.flush()
                        done.add(race_id)
                        n_ok += 1
                    except Exception as e:
                        print(f"[{race_id}] エラー: {e}")
                        n_err += 1
                    time.sleep(delay_sec)

                save_progress(done)  # 日付単位でチェックポイント保存 -> 中断しても再開可能

            browser.close()

    print(f"完了。成功={n_ok}レース, エラー={n_err}レース -> {OUT_CSV}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("start", nargs="?")
    parser.add_argument("end", nargs="?")
    parser.add_argument("--diagnose", metavar="RACE_ID")
    args = parser.parse_args()

    if args.diagnose:
        diagnose(args.diagnose)
    elif args.start and args.end:
        run(args.start, args.end)
    else:
        print(__doc__)
