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
from fetch_odds import fetch_odds

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
    "単勝": "単勝", "単勝オッズ": "単勝", "オッズ": "単勝", "人気": "人気",
}


def fetch_shutuba_page(page, race_id: str) -> str:
    page.goto(SHUTUBA_URL.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
    # オッズはページ読み込み後にJavaScriptで差し込まれる。固定待機だと描画が遅いケース
    # (後半レースなど)で取りこぼすため、実数のオッズが現れるまで最大8秒待つ。
    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.Shutuba_Table') || document.body;
                return /\\d+\\.\\d/.test(el.innerText);
            }""",
            timeout=8000,
        )
    except Exception:
        pass  # 時間切れでも、取れる範囲でパースを続ける
    page.wait_for_timeout(800)
    return page.content()


def _scalar(v):
    """Seriesが返ってきた場合(重複列などが原因)でも必ずスカラー値に変換する防御ヘルパー"""
    if isinstance(v, pd.Series):
        return v.iloc[0] if len(v) else None
    return v


def parse_race_name(html: str) -> str | None:
    """<title>タグからレース名を推定する(例: "○○特別 出馬表 | ..." -> "○○特別")"""
    m = re.search(r"<title>([^<]*)</title>", html)
    if not m:
        return None
    title = m.group(1)
    # "出馬表"より前、"|"より前の部分を候補にする
    name = re.split(r"出馬表|\|", title)[0].strip()
    return name or None


def _parse_odds(v):
    """オッズ欄を数値に変換する。
    netkeibaはオッズ未確定時に「---.-」「**.*」「--」などのプレースホルダを出したり、
    「5.2-7.1」のような予想オッズ帯を出すことがあるため、単純なto_numericでは取り逃す。
    帯の場合は下限値を採用する。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")  # 桁区切りカンマを除去("1,234.5" -> "1234.5")
    if not s or s in ("-", "--", "---", "**", "---.-", "**.*"):
        return None
    # 数値(小数可)を全部拾い、最初の1つを使う("5.2-7.1" -> 5.2)
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if not nums:
        return None
    try:
        val = float(nums[0])
    except ValueError:
        return None
    # 単勝オッズとしてあり得ない値は無効扱い
    return val if 1.0 <= val <= 10000 else None


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

    # netkeibaの出馬表は見出しが2段(同じ文字が上下に重なる構造)になっていることがあり、
    # そのままrename()すると変換が効かない(元の列名がそのまま生き残ってしまう)ため、
    # 先にMultiIndexを1段のシンプルな文字列へ平らにしてから変換する。
    if isinstance(entry_table.columns, pd.MultiIndex):
        flat_cols = [_normalize_col(c[0]) for c in entry_table.columns]
    else:
        flat_cols = [_normalize_col(c) for c in entry_table.columns]
    entry_table.columns = flat_cols

    df = entry_table.rename(columns={c: COLUMN_MAP.get(c, c) for c in entry_table.columns})
    df = df.loc[:, ~df.columns.duplicated()]  # 列名の重複(例:"馬名"が複数)があれば先頭優先で1つにまとめる
    cond = parse_race_conditions(html)
    track_code = race_id[4:6]
    race_number = int(race_id[-2:])
    race_name = parse_race_name(html)

    rows = []
    for _, r in df.iterrows():
        horse_name = _scalar(r.get("馬名"))
        if pd.isna(horse_name):
            continue
        row = {
            "race_id": race_id,
            "race_date": race_date,
            "track_code": track_code,
            "track_name": TRACK_NAMES.get(track_code, ""),
            "race_number": race_number,
            "race_name": race_name,
            "post_time": cond.get("post_time"),
            "surface": cond.get("surface"),
            "distance": cond.get("distance"),
            "baba": cond.get("baba"),
            "weather": cond.get("weather"),
            "waku": _scalar(r.get("枠番")),
            "umaban": _scalar(r.get("馬番")),
            "horse": str(horse_name).strip(),
            "jockey": re.sub(r"^[▲△☆★◇]", "", str(_scalar(r.get("騎手"))).strip()) if pd.notna(_scalar(r.get("騎手"))) else None,
            "trainer": str(_scalar(r.get("調教師"))).strip() if pd.notna(_scalar(r.get("調教師"))) else None,
            "weight_carry": _scalar(r.get("斤量")),
            "odds_win": _parse_odds(_scalar(r.get("単勝"))),
        }
        sexage = str(_scalar(r.get("性齢", "")))
        m = re.match(r"([牡牝セ])(\d+)", sexage)
        if m:
            row["sex"], row["age"] = m.group(1), int(m.group(2))
        bw = str(_scalar(r.get("馬体重増減", "")))
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
        # オッズAPIの応答も確認する(オッズが取れない原因の切り分け用)
        api_odds = fetch_odds(page, race_id)
        browser.close()

    print(f"HTML長: {len(html)} 文字")
    print("data/diagnose_shutuba_screenshot.png にスクリーンショットを保存しました")
    print(f"\n=== オッズAPI ===")
    if api_odds:
        print(f"取得成功: {len(api_odds)}頭分")
        for u in sorted(api_odds):
            print(f"  馬番{u}: {api_odds[u]}倍")
    else:
        print("取得できず(上のログに理由が出ています)")

    import io
    tables = pd.read_html(io.StringIO(html))
    print(f"\nテーブル数: {len(tables)}")
    for i, t in enumerate(tables):
        cols = list(t.columns)[:10]
        print(f"[{i}] shape={t.shape}  columns(先頭10個)={cols}")

    entry_table = None
    for t in tables:
        if any("馬名" in _normalize_col(c) for c in t.columns) and any("騎手" in _normalize_col(c) for c in t.columns):
            if entry_table is None or len(t) > len(entry_table):
                entry_table = t
    if entry_table is not None:
        print(f"\n出馬表として選ばれたテーブルの全列名: {list(entry_table.columns)}")
        print(f"正規化後: {[_normalize_col(c) for c in entry_table.columns]}")

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
        first_error_shown = False
        for race_id in race_ids:
            try:
                html = fetch_shutuba_page(page, race_id)
                rows = parse_shutuba(html, race_id, date_str)

                # オッズは出馬表HTMLには入っておらず("---.-"のまま)、
                # JavaScriptが別APIから差し込む仕様。そのためAPIを直接叩いて補完する。
                api_odds = fetch_odds(page, race_id)
                if api_odds:
                    for r in rows:
                        u = r.get("umaban")
                        if u is not None and not pd.isna(u):
                            v = api_odds.get(int(u))
                            if v is not None:
                                r["odds_win"] = v

                n_odds = sum(1 for r in rows if r.get("odds_win") is not None)
                rno = int(race_id[-2:])
                ptime = rows[0].get("post_time") if rows else None
                flag = "" if n_odds else "  ← オッズ取得できず"
                print(f"  [{race_id}] {rno}R 発走{ptime or '?'} {len(rows)}頭 オッズ{n_odds}頭{flag}")
                all_rows.extend(rows)
            except Exception as e:
                if not first_error_shown:
                    import traceback
                    print(f"  [{race_id}] エラー(詳細):")
                    traceback.print_exc()
                    first_error_shown = True
                else:
                    print(f"  [{race_id}] エラー: {e}")
        browser.close()

    if all_rows:
        pd.DataFrame(all_rows).to_csv(out_csv, index=False)
        n_odds_rows = sum(1 for r in all_rows if r.get("odds_win") is not None)
        print(f"\n合計 {len(all_rows)}頭分を {out_csv} へ出力しました"
              f"(うちオッズ取得済み {n_odds_rows}頭)")
        return 0

    # ここから先は「出馬表が1頭も取れなかった」ケース。
    # 後続の予測ステップが FileNotFoundError で落ちると原因が分かりにくいので、
    # ここで理由を明示して終了コードを分ける。
    if not race_ids:
        # その日はJRA開催がない(平日など)。異常ではないので正常終了扱いにし、
        # 呼び出し側(ワークフロー)が後続処理をスキップできるようにする。
        print(f"\n{date_str} はJRAの開催がありません。処理をスキップします。")
        return 2

    print(f"\n{len(race_ids)}レース見つかりましたが、出馬表を1頭も取得できませんでした。")
    print("考えられる原因: 出馬表がまだ公開されていない / ページ構造の変更 / アクセス制限")
    print(f"次のコマンドで詳しく確認できます: python src/scrape_entries.py --diagnose {race_ids[0]}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD")
    parser.add_argument("--diagnose", metavar="RACE_ID")
    parser.add_argument("--out", default="data/entries_this_week.csv")
    args = parser.parse_args()

    if args.diagnose:
        diagnose(args.diagnose)
    elif args.date:
        # 終了コード: 0=成功 / 1=取得失敗(要調査) / 2=その日は開催なし(正常)
        sys.exit(scrape_date(args.date, args.out))
    else:
        print(__doc__)
