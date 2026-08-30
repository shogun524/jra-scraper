"""
race_result.csv(〜2021-07-31) と race_result_new.csv(バックフィル分) を結合する。
結合後は feature_engineering.py と train_model.py を再実行してください。
"""
import duckdb

con = duckdb.connect()
con.execute("""
COPY (
    SELECT * FROM read_csv_auto('data/race_result.csv', encoding='utf-8')
    UNION ALL BY NAME
    SELECT * FROM read_csv_auto('data/race_result_new.csv', encoding='utf-8')
) TO 'data/race_result_merged.csv' (FORMAT CSV, HEADER, ENCODING 'utf-8')
""")
n = con.execute("SELECT count(*) FROM read_csv_auto('data/race_result_merged.csv', encoding='utf-8')").fetchone()[0]
print(f"結合完了: {n:,}行 -> data/race_result_merged.csv")
print("次に data/race_result.csv をこのファイルに差し替えて、")
print("  python src/feature_engineering.py")
print("  python src/train_model.py")
print("を再実行してください。")
