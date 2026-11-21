"""
海外・地方での走りを netkeiba の馬ページから取ってくる(11/21)。

race_result_merged.csv(JRAの成績DB)には海外レース・地方レースが入っていないため、
  ・海外から来た馬(マイルCSのエルデナリなど)は成績がまったく無い
  ・香港マイルなど海外を走った日本馬は、その走りが抜けている
  ・半年以上あいた馬は「データが古い」とみなされ、通算成績が消されていた
という問題があった。出馬表の馬ID(entries の horse_id)で db.netkeiba.com/horse/result/<ID>/ を開き、
DBに無い走り(海外・地方)だけを data/ext_history.csv に書き出す。predict.py と past_runs.py がDBと合わせて使う。

取りに行く馬: 新馬戦以外で、DBに走りが無い馬 / DBの最後の走りから120日以上あいている馬
            (休み明けか、海外・地方を走っていたかを確かめるため)

使い方: python src/horse_history.py data/entries_this_week.csv data/race_result_merged.csv data/ext_history.csv
"""
import io
import os
import re
import sys
import time

import duckdb
import pandas as pd

GAP_DAYS = 120
URL = "https://db.netkeiba.com/horse/result/{hid}/"
COLS = ["horse", "horse_id", "race_date", "venue", "race_name", "field_size", "waku", "umaban", "odds", "popularity",
        "finish", "jockey", "weight_carry", "surface", "distance", "baba", "time", "margin", "passing", "last3f",
        "horse_weight", "weight_diff", "winner"]


def parse_result(html, horse, hid):
    tables = pd.read_html(io.StringIO(html))
    t = None
    for x in tables:
        cols = [str(c).replace(" ", "") for c in x.columns]
        if "着順" in cols and "日付" in cols:
            x.columns = cols
            t = x
            break
    if t is None:
        return []
    rows = []
    for _, r in t.iterrows():
        d = str(r.get("日付", ""))
        if not re.match(r"\d{4}/\d{2}/\d{2}", d):
            continue
        dist = str(r.get("距離", ""))
        m = re.match(r"(芝|ダ|障)\D*(\d+)", dist)
        hw = str(r.get("馬体重", ""))
        mw = re.match(r"(\d+)(?:\(([+\-]?\d+)\))?", hw)
        rows.append(dict(
            horse=horse, horse_id=hid, race_date=d.replace("/", "-"), venue=str(r.get("開催", "")),
            race_name=str(r.get("レース名", "")), field_size=pd.to_numeric(r.get("頭数"), errors="coerce"),
            waku=pd.to_numeric(r.get("枠番"), errors="coerce"), umaban=pd.to_numeric(r.get("馬番"), errors="coerce"),
            odds=pd.to_numeric(r.get("オッズ"), errors="coerce"), popularity=pd.to_numeric(r.get("人気"), errors="coerce"),
            finish=str(r.get("着順", "")), jockey=str(r.get("騎手", "")),
            weight_carry=pd.to_numeric(r.get("斤量"), errors="coerce"),
            surface={"芝": "芝", "ダ": "ダート", "障": "障害"}.get(m.group(1)) if m else None,
            distance=int(m.group(2)) if m else None, baba=str(r.get("馬場", "")),
            time=str(r.get("タイム", "")) if pd.notna(r.get("タイム")) else "",
            margin=pd.to_numeric(r.get("着差"), errors="coerce"),
            passing=str(r.get("通過", "")) if pd.notna(r.get("通過")) else "",
            last3f=pd.to_numeric(r.get("上り"), errors="coerce"),
            horse_weight=int(mw.group(1)) if mw else None,
            weight_diff=int(mw.group(2)) if mw and mw.group(2) else None,
            winner=str(r.get("勝ち馬(2着馬)", "")) if pd.notna(r.get("勝ち馬(2着馬)")) else ""))
    return rows


def targets(entries_csv, merged_csv):
    E = pd.read_csv(entries_csv, dtype={"race_id": str, "horse_id": str})
    if "horse_id" not in E.columns:
        print("[INFO] 出馬表に馬IDが無いので取りに行けません")
        return pd.DataFrame()
    E = E[E["horse_id"].notna()]
    debut = E.get("race_name", pd.Series("", index=E.index)).astype(str).str.contains("新馬")
    E = E[~debut]
    con = duckdb.connect()
    con.register("e", E[["horse"]].drop_duplicates())
    last = con.execute(f"""SELECT "馬名" AS horse, max(CAST("レース日付" AS DATE)) AS last_date
        FROM read_csv_auto('{merged_csv}', all_varchar=true, header=true) r JOIN e ON r."馬名" = e.horse GROUP BY 1""").df()
    E = E.merge(last, on="horse", how="left")
    gap = (pd.to_datetime(E["race_date"]) - pd.to_datetime(E["last_date"])).dt.days
    T = E[E["last_date"].isna() | (gap >= GAP_DAYS)].drop_duplicates("horse_id")
    return T


def main():
    entries_csv, merged_csv, out = sys.argv[1], sys.argv[2], sys.argv[3]
    T = targets(entries_csv, merged_csv)
    if T.empty:
        print("[INFO] 戦績を取りに行く馬はいません")
        pd.DataFrame(columns=COLS).to_csv(out, index=False)
        return
    print(f"[INFO] DBに走りが無い/{GAP_DAYS}日以上あいている馬 {len(T)}頭の戦績を netkeiba から取ります")
    from playwright.sync_api import sync_playwright
    from scrape_race_ids import UA
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA)
        for _, e in T.iterrows():
            try:
                pg.goto(URL.format(hid=e["horse_id"]), timeout=30000, wait_until="domcontentloaded")
                pg.wait_for_timeout(800)
                rr = parse_result(pg.content(), e["horse"], e["horse_id"])
                rr = [r for r in rr if r["race_date"] < str(e["race_date"])[:10]]
                rows += rr
                print(f"  {e['horse']}({e['horse_id']}): {len(rr)}走 (DBの最後 {e['last_date'] if pd.notna(e['last_date']) else 'なし'})")
            except Exception as ex:  # noqa: BLE001
                print(f"  [WARN] {e['horse']}: {ex}")
            time.sleep(1.0)
        b.close()
    X = pd.DataFrame(rows, columns=COLS)
    # DBにすでにある走り(同じ馬・同じ日)は除く → 残るのは海外・地方の走り
    if not X.empty:
        con = duckdb.connect()
        con.register("x", X[["horse", "race_date"]].drop_duplicates())
        have = con.execute(f"""SELECT DISTINCT "馬名" AS horse, CAST(CAST("レース日付" AS DATE) AS VARCHAR) AS race_date
            FROM read_csv_auto('{merged_csv}', all_varchar=true, header=true) r JOIN x ON r."馬名" = x.horse""").df()
        key = set(zip(have["horse"], have["race_date"]))
        X = X[[(h, d) not in key for h, d in zip(X["horse"], X["race_date"])]]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    X.to_csv(out, index=False)
    print(f"[INFO] DBに無い走り {len(X)}件を {out} に保存({X['horse'].nunique() if len(X) else 0}頭)")


if __name__ == "__main__":
    main()
