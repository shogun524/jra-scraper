"""
血統(父・母父・父の父)を netkeiba から集める。ブラウザは使わず requests で読む。

2段階:
  ids : レース結果ページから「馬名 → 馬ID」を集める      → horse_ids_<part>.csv (race_id, umaban, horse, horse_id)
  ped : 馬IDごとに血統ページから父・母・母父・父の父を読む → pedigree_<part>.csv (horse_id, horse, sire, dam, damsire, sire_sire)

大量に読むので、--part i --parts n で n 分割し、GitHub Actions の matrix で並べて動かす。
すでに取った分は --have で渡したCSVから飛ばすので、途中で止まってもやり直せる。

使い方:
  python src/pedigree.py ids --races races.txt --part 0 --parts 4 --out horse_ids_0.csv
  python src/pedigree.py ped --ids horse_ids.csv --part 0 --parts 8 --out pedigree_0.csv [--have pedigree.csv]
  python src/pedigree.py test --race 202605050211     (1レース分を取ってみて表示)
"""
import argparse
import csv
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={}"
PED_URL = "https://db.netkeiba.com/horse/ped/{}/"
S = requests.Session()
S.headers["User-Agent"] = UA


def get(url, tries=3):
    for i in range(tries):
        try:
            r = S.get(url, timeout=20)
            if r.status_code == 200:
                for enc in ("euc-jp", "utf-8"):
                    try:
                        return r.content.decode(enc)
                    except UnicodeDecodeError:
                        pass
                return r.text
            if r.status_code in (403, 429):
                time.sleep(30 * (i + 1))
        except requests.RequestException:
            time.sleep(5 * (i + 1))
    return None


def parse_result_ids(html):
    """結果ページ -> [(馬番, 馬名, 馬ID)]"""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for tr in soup.select("table tr"):
        a = tr.select_one('a[href*="/horse/"]')
        if not a:
            continue
        m = re.search(r"/horse/(\w{10})", a.get("href", ""))
        if not m:
            continue
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        ub = None
        for c in tr.find_all("td"):
            cls = " ".join(c.get("class", []))
            if "Umaban" in cls or "Txt_C" in cls and c.get_text(strip=True).isdigit():
                pass
        # 馬番は「着順, 枠, 馬番」の3番目の数字
        nums = [t for t in tds[:4] if t.isdigit()]
        if len(nums) >= 3:
            ub = int(nums[2])
        elif len(nums) == 2:
            ub = int(nums[1])
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append((ub, a.get_text(strip=True), m.group(1)))
    return out


def parse_ped(html):
    """血統ページ(5代血統表) -> dict(sire, dam, damsire, sire_sire)"""
    soup = BeautifulSoup(html, "lxml")
    t = soup.select_one("table.blood_table")
    if not t:
        return None

    def name(td):
        a = td.find("a")
        return (a.get_text(strip=True) if a else td.get_text(" ", strip=True).split(" ")[0]) if td else ""
    r16 = [td for td in t.find_all("td") if td.get("rowspan") == "16"]
    r8 = [td for td in t.find_all("td") if td.get("rowspan") == "8"]
    if len(r16) < 2 or len(r8) < 4:
        return None
    clean = lambda x: re.sub(r"[\s\u00a0]*[(（].*$", "", x).strip()  # noqa: E731  「Galileo (愛)」→「Galileo」
    return dict(sire=clean(name(r16[0])), dam=clean(name(r16[1])), sire_sire=clean(name(r8[0])), damsire=clean(name(r8[2])))


def part_of(items, part, parts):
    return [x for i, x in enumerate(items) if i % parts == part]


def run_ids(a):
    races = [x.strip() for x in open(a.races) if x.strip()]
    done = set()
    if os.path.exists(a.out):
        done = {row["race_id"] for row in csv.DictReader(open(a.out, encoding="utf-8"))}
    todo = [r for r in part_of(races, a.part, a.parts) if r not in done]
    print(f"[INFO] ids: {len(todo)}レース(済み{len(done)})")
    new = not os.path.exists(a.out)
    with open(a.out, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["race_id", "umaban", "horse", "horse_id"])
        t0 = time.time()
        for i, rid in enumerate(todo):
            if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                print("[INFO] 時間切れ。続きは次回")
                break
            h = get(RESULT_URL.format(rid))
            for ub, nm, hid in (parse_result_ids(h) if h else []):
                w.writerow([rid, ub, nm, hid])
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                f.flush()
            time.sleep(a.sleep)


def run_ped(a):
    ids = {}
    for row in csv.DictReader(open(a.ids, encoding="utf-8")):
        ids[row["horse_id"]] = row["horse"]
    have = set()
    for p in [a.have, a.out]:
        if p and os.path.exists(p):
            have |= {row["horse_id"] for row in csv.DictReader(open(p, encoding="utf-8"))}
    todo = [h for h in part_of(sorted(ids), a.part, a.parts) if h not in have]
    print(f"[INFO] ped: {len(todo)}頭(済み{len(have)})")
    new = not os.path.exists(a.out)
    with open(a.out, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["horse_id", "horse", "sire", "dam", "damsire", "sire_sire"])
        t0 = time.time()
        for i, hid in enumerate(todo):
            if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                print("[INFO] 時間切れ。続きは次回")
                break
            h = get(PED_URL.format(hid))
            p = parse_ped(h) if h else None
            if p:
                w.writerow([hid, ids[hid], p["sire"], p["dam"], p["damsire"], p["sire_sire"]])
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                f.flush()
            time.sleep(a.sleep)


SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={}"


def run_runners(a):
    """予想(predictions.csv)の出走馬の 父・母父・父の父 を runner_ped.json に {馬名: {...}} で保存。
    すでに分かっている馬(--cache の json)は取りに行かない。新聞・サイトの血統欄に使う"""
    import json
    import pandas as pd
    pred = pd.read_csv(a.pred, dtype={"race_id": str})
    cache = {}
    if a.cache and os.path.exists(a.cache):
        try:
            cache = json.load(open(a.cache, encoding="utf-8"))
        except ValueError:
            cache = {}
    need = set(pred["horse"].astype(str)) - set(cache)
    print(f"[INFO] runners: 出走{pred['horse'].nunique()}頭のうち新しく調べる {len(need)}頭")
    ids = {}
    for rid in dict.fromkeys(pred.loc[pred["horse"].astype(str).isin(need), "race_id"]):
        h = get(SHUTUBA_URL.format(rid))
        for _, nm, hid in (parse_result_ids(h) if h else []):
            if nm in need:
                ids[nm] = hid
        time.sleep(a.sleep)
    for nm, hid in ids.items():
        h = get(PED_URL.format(hid))
        p = parse_ped(h) if h else None
        if p:
            cache[nm] = dict(id=hid, **p)
        time.sleep(a.sleep)
    keep = set(pred["horse"].astype(str)) | set(list(cache)[-3000:])   # 増えすぎないよう古い馬は捨てる
    cache = {k: v for k, v in cache.items() if k in keep}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[INFO] {len(ids)}頭の血統を取得、{a.out} に {len(cache)}頭分を保存")


def run_test(a):
    h = get(RESULT_URL.format(a.race))
    rows = parse_result_ids(h) if h else []
    print("result ids:", rows[:5], len(rows))
    for ub, nm, hid in rows[:3]:
        ph = get(PED_URL.format(hid))
        print(nm, hid, parse_ped(ph) if ph else "取得失敗", (ph or "")[:0])
        if ph and not parse_ped(ph):
            i = ph.find("blood_table")
            print("  blood_table at", i, ph[i:i + 1500] if i >= 0 else ph[:800])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["ids", "ped", "test", "runners"])
    ap.add_argument("--pred")
    ap.add_argument("--cache")
    ap.add_argument("--races")
    ap.add_argument("--ids")
    ap.add_argument("--have")
    ap.add_argument("--race", default="202605050211")
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--out")
    ap.add_argument("--sleep", type=float, default=0.7)
    ap.add_argument("--max-minutes", type=float, default=0)
    a = ap.parse_args()
    {"ids": run_ids, "ped": run_ped, "test": run_test, "runners": run_runners}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
