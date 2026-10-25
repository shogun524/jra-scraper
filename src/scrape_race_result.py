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
import unicodedata
import pandas as pd

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"

# netkeiba結果テーブルの列名(空白除去後) -> こちらのスキーマ列名
COLUMN_MAP = {
    "着順": "着順", "枠": "枠番", "枠番": "枠番", "馬番": "馬番",
    "馬名": "馬名", "性齢": "性齢", "斤量": "斤量", "騎手": "騎手",
    "タイム": "タイム", "着差": "着差", "人気": "人気", "単勝": "単勝", "単勝オッズ": "単勝",
    "後3F": "上り", "上り": "上り", "コーナー通過順": "通過", "通過": "通過",
    "厩舎": "調教師", "馬主": "馬主", "賞金(万円)": "賞金(万円)", "賞金": "賞金(万円)",
    "馬体重増減": "馬体重増減", "馬体重(増減)": "馬体重増減", "馬体重": "馬体重増減",
}


def fetch_race_page(page, race_id: str) -> str:
    page.goto(RESULT_URL.format(race_id=race_id), timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    return page.content()


def parse_race_conditions(html: str) -> dict:
    """レース見出し部分から芝/ダート・距離・馬場・天候などを抽出。
    タグ跨ぎでの文字列分断を避けるため、まずBeautifulSoupでレース情報欄のテキストを取り出してから正規表現をかける。"""
    from bs4 import BeautifulSoup
    cond = {}
    soup = BeautifulSoup(html, "html.parser")
    race_data = soup.find(class_=re.compile("RaceData01"))
    text = race_data.get_text(" ", strip=True) if race_data else html

    # "ダート"の正式表記だけでなく、netkeibaでよく使われる省略形"ダ"も拾う(これが無いとダート戦が軒並み欠損する)
    m = re.search(r"(芝|ダート|ダ)(左|右)?(外)?(\d{3,4})m", text)
    if m:
        cond["surface"] = "ダート" if m.group(1) in ("ダート", "ダ") else "芝"
        cond["distance"] = int(m.group(4))
    m = re.search(r"天候[:：]?\s*(晴|曇|雨|小雨|雪|小雪)", text)
    if m:
        cond["weather"] = m.group(1)
    m = re.search(r"馬場[:：]?\s*(良|稍重|稍|重|不良)", text)
    if m:
        baba = m.group(1)
        cond["baba"] = "稍重" if baba == "稍" else baba
    m = re.search(r"(\d{1,2}:\d{2})発走", text)
    if m:
        cond["post_time"] = m.group(1)
    return cond


def normalize_person(name):
    """騎手名などの表記ゆれを揃える。
    出馬表は「Ｍデムーロ」、結果ページは「Ｍ．デムーロ」のように全角・ドットの有無が違うため、
    NFKCで半角化し、ドット・中黒・空白と見習い記号(▲△☆★◇)を取り除く。"""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return None
    s = unicodedata.normalize("NFKC", str(name)).strip()
    s = re.sub(r"^[▲△☆★◇]+", "", s)
    s = re.sub(r"[.・\s]", "", s)
    return s or None


def jockey_key(name):
    """騎手の照合用キー。netkeibaの結果ページは外国人騎手名を3文字で切る(ルメール→ルメー)一方、
    出馬表は「北村友」「北村友一」が混在するため、記号を除いた先頭3文字で突き合わせる。
    feature_engineering.py / predict.py の SQL 側(JOCKEY_KEY_SQL)と同じ規則。"""
    s = normalize_person(name)
    return s[:3] if s else None


# SQL版(DuckDB)。全角英数は取り直し時に normalize_person で半角化済みの前提
JOCKEY_KEY_SQL = """left(regexp_replace("騎手", '^[▲△☆★◇]+|[.．・ 　]', '', 'g'), 3)"""


def normalize_trainer(name):
    """調教師名の表記ゆれを揃える。「美浦堀」「[東] 堀」「(美)堀」「栗東・友道」などから所属表記を除いて姓だけにする。"""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return None
    s = unicodedata.normalize("NFKC", str(name)).strip()
    s = re.sub(r"[\[(（【][^\])）】]*[\])）】]", "", s)  # [東] (美) などの括弧書き
    s = re.sub(r"^(美浦|栗東|地方|海外|外国)", "", s)
    s = re.sub(r"[.・\s]", "", s)
    return s or None


# クラスの序列(feature_engineering.py の race_class と同じ値)
CLASS_ORDER = [("新馬", 0), ("未出走", 1), ("未勝利", 1), ("1勝クラス", 5), ("2勝クラス", 9),
               ("3勝クラス", 14), ("オープン", 20)]


def parse_race_class_text(html: str):
    """レース見出し(RaceData02)から競争条件を「3歳以上1勝クラス」の形で取り出す。
    結果ページ・出馬表ページの両方で使える。取れなければ None。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(class_=re.compile("RaceData02"))
    text = el.get_text(" ", strip=True) if el else ""
    name_el = soup.find(class_=re.compile("RaceName"))
    text += " " + (name_el.get_text(" ", strip=True) if name_el else "")
    text = unicodedata.normalize("NFKC", text)
    cls = None
    for key in ("新馬", "未出走", "未勝利", "1勝クラス", "2勝クラス", "3勝クラス", "オープン"):
        if key in text:
            cls = key
            break
    if cls is None:
        m = re.search(r"(\d+)万下", text)
        if m:
            cls = f"{m.group(1)}万下"
        elif re.search(r"G(?:[1-3]|I{1,3})|\(L\)|\(OP\)", text):
            cls = "オープン"
    if cls is None:
        return None
    m = re.search(r"(\d歳(?:以上)?)", text)
    return f"{m.group(1) if m else ''}{cls}"


def race_class_value(text):
    """競争条件の文字列 -> 数値の序列(新馬0 / 未勝利1 / 1勝5 / 2勝9 / 3勝14 / オープン20)。
    旧表記「500万下」は 500/100=5 のように換算する(1勝クラス相当)。"""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    t = unicodedata.normalize("NFKC", str(text))
    for key, val in CLASS_ORDER:
        if key in t:
            return val
    m = re.search(r"(\d+)万下", t)
    if m:
        return int(m.group(1)) / 100
    if re.search(r"G(?:[1-3]|I{1,3})|\(L\)|OP|ステークス", t):
        return 20
    return None


def parse_race_name_text(html: str):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(class_=re.compile("RaceName"))
    return unicodedata.normalize("NFKC", el.get_text(" ", strip=True)) if el else None


def _normalize_col(c) -> str:
    """列名から空白(半角/全角/改行)を除去して比較しやすくする。
    netkeibaは "着 順" のように見た目上は同じでも空白入りの列名になっていることがある。"""
    return re.sub(r"[\s\u3000]+", "", str(c))


def parse_corner_section(html: str) -> dict:
    """ページ下部の「コーナー通過順位」セクションから、馬番→各コーナー通過順位を復元する。

    結果テーブルの「通過」列が空のレースでも、このセクションには
      3コーナー  2(1,4)(3,8)(5,6)9(10,11)7
    のような形式で全馬の通過順が載っている。カッコは「横並び(ほぼ同位置)」を意味する。
    戻り値: {馬番: {"1コーナー": 順位, ... }}
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    result = {}
    for corner_no in (1, 2, 3, 4):
        # 改行をまたがないよう [^\S\n](改行以外の空白)に限定する。
        # \s を使うと空欄の「1コーナー」が次行「3コーナー」の中身まで飲み込んでしまう。
        m = re.search(rf"{corner_no}コーナー[ \t\u3000]*([0-9()（）,，、=\*\-][0-9()（）,，、=\*\- \t\u3000]*)", text)
        if not m:
            continue
        seq = m.group(1).strip()
        if not seq or not re.search(r"\d", seq):
            continue
        # カッコで括られたグループは同順位扱い。左から順に順位を振る。
        pos = 0
        # "(1,4)" や "1" を順に取り出す
        for token in re.finditer(r"\(([^)]*)\)|(\d+)", seq):
            if token.group(1) is not None:
                nums = re.findall(r"\d+", token.group(1))
            else:
                nums = [token.group(2)]
            if not nums:
                continue
            pos += 1
            for n in nums:
                result.setdefault(int(n), {})[f"{corner_no}コーナー"] = pos
    return result


def parse_race_result(html: str, race_id: str) -> list[dict]:
    """結果テーブルをDataFrame化 -> dictのリストへ"""
    import io
    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")
    except (ValueError, ImportError):
        return []  # 表が1つも無い(未確定・アクセス制限ページなど)
    # 「着順」列を持つ一番大きいテーブルを結果テーブルとみなす(空白ズレを吸収して比較)
    result_table = None
    for t in tables:
        if any("着順" in _normalize_col(c) for c in t.columns):
            if result_table is None or len(t) > len(result_table):
                result_table = t
    if result_table is None:
        return []

    df = result_table.rename(columns={c: COLUMN_MAP.get(_normalize_col(c), _normalize_col(c)) for c in result_table.columns})
    cond = parse_race_conditions(html)
    corner_section = parse_corner_section(html)
    class_text = parse_race_class_text(html)
    race_name = parse_race_name_text(html)

    rows = []
    for _, r in df.iterrows():
        row = {
            "レースID": race_id,
            "着順": r.get("着順"),
            "枠番": r.get("枠番"),
            "馬番": r.get("馬番"),
            "馬名": r.get("馬名"),
            "斤量": r.get("斤量"),
            "騎手": normalize_person(r.get("騎手")),
            "タイム": r.get("タイム"),
            "着差": r.get("着差"),
            "人気": r.get("人気"),
            "単勝": r.get("単勝"),
            "上り": r.get("上り"),
            "調教師": normalize_trainer(r.get("調教師")),
            "馬体重": None, "場体重増減": None,
            "芝・ダート区分": cond.get("surface"),
            "距離(m)": cond.get("distance"),
            "馬場状態1": cond.get("baba"),
            "天候": cond.get("weather"),
            "競争条件": class_text,
            "レース名": race_name,
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
        # 通過順(例:"5-5-6-4")から各コーナー通過順位を分割(値が無い場合はNoneのまま)
        passage = r.get("通過")
        if pd.notna(passage):
            # 通過順は「通過したコーナーの順」で、最後の値が常に4コーナー(最終コーナー)。
            # コーナーが2つのコース("3-3")で前から詰めると4コーナーが空欄になり脚質が取れなくなるため、後ろ詰めにする。
            parts = [x for x in str(passage).split("-") if x.strip()]
            keys = ["1コーナー", "2コーナー", "3コーナー", "4コーナー"]
            for key in keys:
                row[key] = None
            for key, val in zip(keys[4 - len(parts):] if len(parts) <= 4 else keys, parts[-4:]):
                row[key] = val
        else:
            # 結果テーブルに通過列が無い/空のレースは、ページ下部の
            # 「コーナー通過順位」セクションから復元する(これが無いと脚質が算出できない)
            umaban = row.get("馬番")
            if pd.notna(umaban) and corner_section:
                for key, val in corner_section.get(int(umaban), {}).items():
                    row[key] = val
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


# ---------------------------------------------------------------------------
# 払戻(配当)
# ---------------------------------------------------------------------------
# 結果ページの払戻表(table.Payout_Detail_Table)の行クラス -> (券種名, 1組あたりの頭数/枠数)
PAYOUT_TYPES = {
    "Tansho": ("単勝", 1), "Fukusho": ("複勝", 1), "Wakuren": ("枠連", 2), "Umaren": ("馬連", 2),
    "Wide": ("ワイド", 2), "Umatan": ("馬単", 2), "Fuku3": ("3連複", 3), "Tan3": ("3連単", 3),
}
PAYOUT_COLUMNS = ["race_id", "bet_type", "combo", "payout", "popularity"]


def parse_payouts(html: str, race_id: str) -> list[dict]:
    """払戻表を1組1行に展開する。
    combo は馬番(枠連は枠番)を "-" でつないだ文字列。順序が関係ない券種(馬連・ワイド・3連複・枠連)は
    小さい順に並べ替えて正規化する。payout は100円あたりの払戻金(円)。同着で複数組ある場合もすべて出す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("table.Payout_Detail_Table tr"):
        cls = next((c for c in (tr.get("class") or []) if c in PAYOUT_TYPES), None)
        if cls is None:
            continue
        name, k = PAYOUT_TYPES[cls]
        res_td, pay_td, nin_td = tr.select_one("td.Result"), tr.select_one("td.Payout"), tr.select_one("td.Ninki")
        if res_td is None or pay_td is None:
            continue
        nums = [int(x) for x in re.findall(r"\d+", " ".join(sp.get_text(strip=True) for sp in res_td.select("span")))]
        pays = [int(x.replace(",", "")) for x in re.findall(r"([\d,]+)円", pay_td.get_text(" ", strip=True))]
        ninki = [int(x) for x in re.findall(r"(\d+)人気", nin_td.get_text(" ", strip=True))] if nin_td else []
        if not pays or len(nums) != len(pays) * k:
            continue  # 想定外の形(発売なし・特払いなど)は飛ばす
        for i, pay in enumerate(pays):
            combo = nums[i * k:(i + 1) * k]
            if name in ("馬連", "ワイド", "3連複", "枠連"):
                combo = sorted(combo)
            rows.append({
                "race_id": race_id, "bet_type": name, "combo": "-".join(map(str, combo)),
                "payout": pay, "popularity": ninki[i] if i < len(ninki) else None,
            })
    return rows
