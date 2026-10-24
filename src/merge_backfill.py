"""
既存の race_result_merged.csv に、バックフィルで取り直した結果(race_result_new.csv)を統合する。

- 同じレースIDが両方にある場合は、新しく取り直した側を採用する
  (2021-08〜2022年の旧取得分は芝ダート・距離・クラスの欠損が多いため、取り直し分で置き換える)
- 列は名前で突き合わせる(新しい側に無い列は空欄、新しい側にだけある列は追加)
- 最後に年別のレース数・欠損数を表示して、抜けている期間が無いか確認できるようにする

使い方:
    python src/merge_backfill.py data/race_result_merged.csv data/race_result_new.csv data/race_result_merged.csv
"""
import os
import sys

import duckdb


def main(base_csv: str, new_csv: str, out_csv: str):
    tmp_out = out_csv + ".tmp"
    con = duckdb.connect()
    # 以前の merge_results.py が残した作業列(rn など)は落とす
    def view(name, path):
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{path}', all_varchar=true, header=true)").fetchall()]
        drop = [c for c in cols if c in ("rn", "_rn", "_src")]
        excl = f" EXCLUDE ({', '.join(chr(34) + c + chr(34) for c in drop)})" if drop else ""
        con.execute(f"CREATE VIEW {name} AS SELECT *{excl} FROM read_csv_auto('{path}', all_varchar=true, header=true)")
    view("base", base_csv)
    view("new", new_csv)
    n_base = con.execute("SELECT count(*) FROM base").fetchone()[0]
    n_new = con.execute("SELECT count(*) FROM new").fetchone()[0]
    print(f"既存: {n_base:,}行 / 取り直し: {n_new:,}行")

    con.execute(f"""
    COPY (
        SELECT * EXCLUDE (_rn, _src) FROM (
            SELECT *, row_number() OVER (PARTITION BY "レース馬番ID" ORDER BY _src DESC) AS _rn
            FROM (
                SELECT *, 0 AS _src FROM base
                WHERE "レースID" NOT IN (SELECT DISTINCT "レースID" FROM new)
                UNION ALL BY NAME
                SELECT *, 1 AS _src FROM new
            )
        ) WHERE _rn = 1
        ORDER BY "レース日付", "レースID", TRY_CAST("馬番" AS INTEGER)
    ) TO '{tmp_out}' (FORMAT CSV, HEADER)
    """)
    os.replace(tmp_out, out_csv)

    con.execute(f"CREATE VIEW m AS SELECT * FROM read_csv_auto('{out_csv}', all_varchar=true, header=true)")
    total = con.execute("SELECT count(*) FROM m").fetchone()[0]
    print(f"統合後: {total:,}行 -> {out_csv}")
    print("\n年別のレース数(直近)と欠損:")
    print(f"{'年':>6} {'レース数':>8} {'行数':>8} {'芝ダ欠損':>8} {'クラス欠損':>10}")
    for y, r, n, ns, nc in con.execute("""
        SELECT substr("レース日付",1,4) y, count(DISTINCT "レースID"), count(*),
               sum(("芝・ダート区分" IS NULL)::INT), sum(("競争条件" IS NULL)::INT)
        FROM m WHERE "レース日付" >= '2018' GROUP BY 1 ORDER BY 1
    """).fetchall():
        print(f"{y:>6} {r:>8,} {n:>8,} {ns:>8,} {nc:>10,}")
    last = con.execute('SELECT max("レース日付") FROM m').fetchone()[0]
    print(f"最終日: {last}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    main(*sys.argv[1:])
