"""
JRA出馬表(まだ結果の出ていないレース)スクレイパー
netkeiba: https://race.netkeiba.com/race/shutuba.html?race_id=XXXXXXXXXXXX

result.html用のscrape_race_result.pyと同じ「空白を除去してから列名を比較する」考え方を流用している
(過去、"着 順"のように空白入りの列名で0件パースになったのと同種の問題が起きうるため)。

【重要】このファイルもこちらの開発環境からnetkeiba.comへ実アクセスして検証できていません。
まず --diagnose で1レースだけ試してから、本番(1日分すべて)を実行してください。

使い方:
  python src/scrape_entries.py --diagnose 202604030408          # 1レースだけ試す
  python src/scrape_entries.py 2026-09-06                        # その日のJRA全レース分を取得
  python src/scrape_entries.py 2026-09-06 --out data/entries_this_week.csv
"""
import argparse
import re
import sys
import os
import json
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from scrape_race_result import _normalize_col, parse_race_conditions
from scrape_race_ids import get_race_ids_for_date, UA

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
TRACK_NAMES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

# netkeiba出馬表テーブルの列名(空白除去後) -> こちらのスキーマ列名
COLUMN_MAP = {
    "枠": "枠番", "枠番": "枠番", "馬番": "馬番",
    "馬名": "馬名", "性齢": "性齢", "斤量": "斤量", "騎手": "騎手",
    "厩舎": "調教師", "調教師": "調教師",
    "馬体重増減": "馬体重増減", "馬体重(増減)": "馬体重増減", "馬体重": "馬体重増減",
    "単勝": "単勝", "単勝オッズ": "単勝", "人気": "人気",
}


def fetch_shutuba_page(page, race_id: str) -> str:
    page.goto(SHUTUBA_URL.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    return page.content()


def parse_shutuba(html: str, race_id: str, race_date: str) -> list[dict]:
    """出馬表テーブルをパースし、predict.pyへの入力(entries CSV)と同じスキーマの行に変換する"""
    import io
    tables = pd.read_html(io.StringIO(html))
    entry_table = None
    for t in tables:
        if any("馬名" in _normalize_col(c) for c in t.columns) and any("騎手" in _normalize_col(c) for c in t.columns):
            if entry_table is None or len(t) > len(entry_table):
                entry_table = t
    if entry_table is None:
        return []

    df = entry_table.rename(columns={c: COLUMN_MAP.get(_normalize_col(c), _normalize_col(c)) for c in entry_table.columns})
    cond = parse_race_conditions(html)
    track_code = race_id[4:6]

    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("馬名")):
            continue
        row = {
            "race_id": race_id,
            "race_date": race_date,
            "track_code": track_code,
            "surface": cond.get("surface"),
            "distance": cond.get("distance"),
            "baba": cond.get("baba"),
            "weather": cond.get("weather"),
            "waku": r.get("枠番"),
            "umaban": r.get("馬番"),
            "horse": str(r.get("馬名")).strip(),
            "jockey": str(r.get("騎手")).strip() if pd.notna(r.get("騎手")) else None,
            "trainer": str(r.get("調教師")).strip() if pd.notna(r.get("調教師")) else None,
            "weight_carry": r.get("斤量"),
            "odds_win": pd.to_numeric(r.get("単勝"), errors="coerce"),
        }
        sexage = str(r.get("性齢", ""))
        m = re.match(r"([牡牝セ])(\d+)", sexage)
        if m:
            row["sex"], row["age"] = m.group(1), int(m.group(2))
        bw = str(r.get("馬体重増減", ""))
        m = re.match(r"(\d+)", bw)
        row["horse_weight"] = int(m.group(1)) if m else None
        rows.append(row)
    return rows


def diagnose(race_id: str):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        response = page.goto(SHUTUBA_URL.format(race_id=race_id), timeout=30000, wait_until="load")
        print(f"HTTPステータス: {response.status if response else '(応答なし)'}")
        page.wait_for_timeout(2000)
        html = page.content()
        page.screenshot(path="data/diagnose_shutuba_screenshot.png", full_page=True)
        browser.close()

    print(f"HTML長: {len(html)} 文字")
    print("data/diagnose_shutuba_screenshot.png にスクリーンショットを保存しました")

    import io
    tables = pd.read_html(io.StringIO(html))
    print(f"\nテーブル数: {len(tables)}")
    for i, t in enumerate(tables):
        cols = list(t.columns)[:10]
        print(f"[{i}] shape={t.shape}  columns(先頭10個)={cols}")

    rows = parse_shutuba(html, race_id, "2026-01-01")
    print(f"\nparse_shutuba()の結果: {len(rows)}頭分")
    for r in rows[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))


def scrape_date(date_str: str, out_csv: str):
    """date_str: 'YYYY-MM-DD'。その日のJRA全レースの出馬表を取得してout_csvへ書き出す"""
    from playwright.sync_api import sync_playwright
    date_compact = date_str.replace("-", "")
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        race_ids = get_race_ids_for_date(page, date_compact)
        print(f"{date_str}: {len(race_ids)}レース見つかりました")
        for race_id in race_ids:
            try:
                html = fetch_shutuba_page(page, race_id)
                rows = parse_shutuba(html, race_id, date_str)
                print(f"  [{race_id}] {len(rows)}頭")
                all_rows.extend(rows)
            except Exception as e:
                print(f"  [{race_id}] エラー: {e}")
        browser.close()

    if all_rows:
        pd.DataFrame(all_rows).to_csv(out_csv, index=False)
        print(f"\n合計 {len(all_rows)}頭分を {out_csv} へ出力しました")
    else:
        print("\n0頭でした。出馬表がまだ公開されていないか、パースに失敗しています。--diagnose で確認してください。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD")
    parser.add_argument("--diagnose", metavar="RACE_ID")
    parser.add_argument("--out", default="data/entries_this_week.csv")
    args = parser.parse_args()

    if args.diagnose:
        diagnose(args.diagnose)
    elif args.date:
        scrape_date(args.date, args.out)
    else:
        print(__doc__)
