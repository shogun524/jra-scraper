"""
印刷用(競馬新聞)の「近4走」データを作る。
出馬表(predictions.csv)に載っている馬について、race_result_merged.csv からそのレースより前の走りを新しい順に4つ取り出し、
past_runs.json に {馬名: {"trainer": 厩舎, "runs": [1走ごとの dict, ...]}} で保存する。

1走ごとの中身: d=日付, v=競馬場, cls=クラス(重賞・OPはレース名), sd=芝ダ距離, baba=馬場, n=頭数, ub=馬番, pop=人気,
  fin=着順(数字以外は「中止」など), t=タイム, diff=勝ち馬とのタイム差(勝った時は2着との差をマイナスで),
  pas=通過順, l3f=上り3F, l3r=上り順位, hw=馬体重, hwd=増減, jky=騎手, kin=斤量, odds=単勝

使い方: python src/past_runs.py data/predictions.csv data/race_result_merged.csv data/past_runs.json [--n 4]
"""
import argparse
import json
import math
import re

import duckdb
import pandas as pd

CLASS_SHORT = [("新馬", "新馬"), ("未勝利", "未勝利"), ("1勝クラス", "1勝C"), ("2勝クラス", "2勝C"), ("3勝クラス", "3勝C"),
               ("500万", "1勝C"), ("1000万", "2勝C"), ("1600万", "3勝C"), ("オープン", "OP")]


def short_class(cond, name):
    cond, name = str(cond or ""), str(name or "")
    if "障害" in name:
        return "障害"
    for k, v in CLASS_SHORT:
        if k in cond:
            if v == "OP" and name and not re.fullmatch(r".*(オープン|OP)$", name):
                return re.sub(r"\s+", "", name)[:7]   # 重賞・特別のOPはレース名
            return v
    return re.sub(r"\s+", "", name)[:7] or cond[:6]


def secs(t):
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", str(t or "").strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 if m.group(1) else 0) + float(m.group(2))


def num(x, typ=float):
    try:
        v = float(x)
        return None if math.isnan(v) else typ(v)
    except (TypeError, ValueError):
        return None


def build(pred_csv, merged_csv, n=4):
    P = pd.read_csv(pred_csv, dtype={"race_id": str})
    first_date = P.groupby("horse")["race_date"].min().astype(str).str[:10]
    horses = pd.DataFrame({"horse": first_date.index, "before": first_date.values})
    con = duckdb.connect()
    con.register("h", horses)
    q = f"""
    WITH r AS (SELECT * FROM read_csv_auto('{merged_csv}', all_varchar=true, header=true)),
    mine AS (SELECT r.* FROM r JOIN h ON r."馬名" = h.horse AND r."レース日付" < h.before),
    picked AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY "馬名" ORDER BY "レース日付" DESC) AS k FROM mine) WHERE k <= {n})
    SELECT * FROM picked
    """
    R = con.execute(q).df()
    con.register("R", R)
    races = con.execute(f"""SELECT "レースID" AS rid, "着順" AS fin, "タイム" AS t, "上り" AS l3f
                            FROM read_csv_auto('{merged_csv}', all_varchar=true, header=true)
                            WHERE "レースID" IN (SELECT DISTINCT "レースID" FROM R)""").df()
    # レースごと: 頭数・勝ちタイム・2着タイム・上りの順位
    races["fin_n"] = pd.to_numeric(races.fin, errors="coerce")
    races["ts"] = races.t.map(secs)
    races["l3"] = pd.to_numeric(races.l3f, errors="coerce")
    info = {}
    for rid, g in races.groupby("rid"):
        g2 = g.dropna(subset=["fin_n"]).sort_values("fin_n")
        tw = g2.ts.iloc[0] if len(g2) else None
        t2 = g2.ts.iloc[1] if len(g2) > 1 else None
        l3 = sorted(g.l3.dropna().tolist())
        info[rid] = dict(n=len(g), tw=tw, t2=t2, l3=l3)
    out = {}
    for horse, g in R.sort_values("レース日付", ascending=False).groupby("馬名", sort=False):
        runs = []
        for _, x in g.iterrows():
            ri = info.get(x["レースID"], {})
            fin = num(x["着順"], int)
            ts = secs(x["タイム"])
            diff = None
            if ts is not None and ri.get("tw") is not None:
                diff = (ts - ri["t2"]) if fin == 1 and ri.get("t2") is not None else ts - ri["tw"]
            l3 = num(x["上り"])
            l3r = (ri["l3"].index(l3) + 1) if l3 is not None and l3 in ri.get("l3", []) else None
            pas = "-".join(str(num(x[c], int)) for c in ["1コーナー", "2コーナー", "3コーナー", "4コーナー"] if num(x[c], int))
            surf = str(x["芝・ダート区分"] or "")
            runs.append(dict(
                d=str(x["レース日付"])[2:].replace("-", "."), v=str(x["競馬場名"] or ""),
                cls=short_class(x["競争条件"], x["レース名"]),
                sd=("障" if "障" in surf else surf[:1]) + (str(num(x["距離(m)"], int) or "")),
                baba=str(x["馬場状態1"] or "")[:1], n=ri.get("n"), ub=num(x["馬番"], int), pop=num(x["人気"], int),
                fin=fin if fin is not None else str(x["着順"] or ""), t=str(x["タイム"]) if ts else "",
                diff=None if diff is None else round(diff, 1), pas=pas, l3f=l3, l3r=l3r,
                hw=num(x["馬体重"], int), hwd=num(x["場体重増減"], int), jky=str(x["騎手"] or "")[:4],
                kin=num(x["斤量"]), odds=num(x["単勝"])))
        out[horse] = dict(trainer=str(g.iloc[0]["調教師"] or ""), runs=runs)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pred_csv")
    ap.add_argument("merged_csv")
    ap.add_argument("out_json")
    ap.add_argument("--n", type=int, default=4)
    a = ap.parse_args()
    out = build(a.pred_csv, a.merged_csv, a.n)
    with open(a.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[INFO] {len(out)}頭の近{a.n}走を {a.out_json} に保存しました")


if __name__ == "__main__":
    main()
