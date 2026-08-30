import pandas as pd
import json

df = pd.read_csv("data/race_result_new.csv")
print(f"総行数: {len(df):,}")
print(f"ユニークなレースID数: {df['レースID'].nunique():,}")
print(f"1レースあたり平均頭数: {len(df) / max(df['レースID'].nunique(),1):.1f}")
print(f"日付範囲: {df['レース日付'].min()} 〜 {df['レース日付'].max()}")
print()
print("年別レース数・行数:")
df['year'] = df['レース日付'].astype(str).str[:4]
print(df.groupby('year').agg(races=('レースID','nunique'), rows=('レースID','size')))

with open("data/backfill_progress.json", encoding="utf-8") as fp:
    done_races = json.load(fp)
print(f"\nbackfill_progress.json(成功扱いレース数): {len(done_races):,}")
print(f"-> CSVのユニークレースID数との差: {len(done_races) - df['レースID'].nunique():,} 件が「成功扱いだが0行」の疑いあり")

with open("data/backfill_dates_done.json", encoding="utf-8") as fp:
    dates_done = json.load(fp)
print(f"\nbackfill_dates_done.json(処理済み日数): {len(dates_done):,}")
