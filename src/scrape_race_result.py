"""
1レース分の結果ページをパースし、race_result.csv と同じスキーマの行(dict)に変換する。
netkeiba: https://race.netkeiba.com/race/result.html?race_id=XXXXXXXXXXXX

【注意】ここの列名・セレクタは過去の一般的なnetkeibaの構造を基に書いており、
この開発環境からは実サイトへのアクセスができないため未検証です。
まず diagnose_scraper.py で1レースだけ試し、実際に返ってきたテーブルの列名を
print(df.columns) で確認してから、COLUMN_MAP を実データに合わせて調整してください。
(南関東スクレイパーでも "文字コード" "全角半角" 等のズレで同様の調整をしています)
"""
import re
import pandas as pd

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"

# netkeiba結果テーブルの列名 -> こちらのスキーマ列名
COLUMN_MAP = {
    "着順": "着順", "枠": "枠番", "枠番": "枠番", "馬 番": "馬番", "馬番": "馬番",
    "馬名": "馬名", "性齢": "性齢", "斤量": "斤量", "騎手": "騎手",
    "タイム": "タイム", "着差": "着差", "人気": "人気", "単勝": "単勝",
    "後3F": "上り", "上り": "上り", "コーナー通過順": "通過", "通過": "通過",
    "厩舎": "調教師", "馬主": "馬主", "賞金(万円)": "賞金(万円)", "賞金": "賞金(万円)",
    "馬体重(増減)": "馬体重増減", "馬体重": "馬体重増減",
}


def fetch_race_page(page, race_id: str) -> str:
    page.goto(RESULT_URL.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    return page.content()


def parse_race_conditions(html: str) -> dict:
    """レース見出し部分から芝/ダート・距離・馬場・天候などを抽出"""
    cond = {}
    m = re.search(r"(芝|ダート)(左|右)?(外)?(\d{3,4})m", html)
    if m:
        cond["surface"] = m.group(1)
        cond["distance"] = int(m.group(4))
    m = re.search(r"天候[:：]?\s*(晴|曇|雨|小雨|雪|小雪)", html)
    if m:
        cond["weather"] = m.group(1)
    m = re.search(r"馬場[:：]?\s*(良|稍重|稍|重|不良)", html)
    if m:
        baba = m.group(1)
        cond["baba"] = "稍重" if baba == "稍" else baba
    return cond


def parse_race_result(html: str, race_id: str) -> list[dict]:
    """結果テーブルをDataFrame化 -> dictのリストへ"""
    tables = pd.read_html(html)
    # 「着順」列を持つ一番大きいテーブルを結果テーブルとみなす
    result_table = None
    for t in tables:
        if any("着順" in str(c) for c in t.columns):
            if result_table is None or len(t) > len(result_table):
                result_table = t
    if result_table is None:
        return []

    df = result_table.rename(columns={c: COLUMN_MAP.get(str(c).strip(), str(c).strip()) for c in result_table.columns})
    cond = parse_race_conditions(html)

    rows = []
    for _, r in df.iterrows():
        row = {
            "レースID": race_id,
            "着順": r.get("着順"),
            "枠番": r.get("枠番"),
            "馬番": r.get("馬番"),
            "馬名": r.get("馬名"),
            "斤量": r.get("斤量"),
            "騎手": r.get("騎手"),
            "タイム": r.get("タイム"),
            "着差": r.get("着差"),
            "人気": r.get("人気"),
            "単勝": r.get("単勝"),
            "上り": r.get("上り"),
            "調教師": r.get("調教師"),
            "馬体重": None, "場体重増減": None,
            "芝・ダート区分": cond.get("surface"),
            "距離(m)": cond.get("distance"),
            "馬場状態1": cond.get("baba"),
            "天候": cond.get("weather"),
        }
        # 性齢(例:"牡3")を性別/馬齢に分割
        sexage = str(r.get("性齢", ""))
        m = re.match(r"([牡牝セ])(\d+)", sexage)
        if m:
            row["性別"], row["馬齢"] = m.group(1), int(m.group(2))
        # 馬体重(増減)(例:"496(+4)")を分割
        bw = str(r.get("馬体重増減", ""))
        m = re.match(r"(\d+)\(([+\-]?\d+)\)", bw)
        if m:
            row["馬体重"], row["場体重増減"] = int(m.group(1)), int(m.group(2))
        # 通過順(例:"5-5-6-4")から各コーナー通過順位を分割
        passage = str(r.get("通過", ""))
        parts = passage.split("-")
        for i, key in enumerate(["1コーナー", "2コーナー", "3コーナー", "4コーナー"]):
            row[key] = parts[i] if i < len(parts) else None
        rows.append(row)
    return rows


if __name__ == "__main__":
    # 診断用: 1レースだけ試す (diagnose_scraper.py からも同じ関数を使う)
    import sys, json
    from playwright.sync_api import sync_playwright
    race_id = sys.argv[1] if len(sys.argv) > 1 else "202604030408"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        html = fetch_race_page(page, race_id)
        browser.close()
    rows = parse_race_result(html, race_id)
    print(json.dumps(rows[:3], ensure_ascii=False, indent=2))
    print(f"{len(rows)}頭分パースしました")
