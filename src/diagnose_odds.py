"""
オッズ取得状況の診断
data/entries_this_week.csv を読み込み、レース番号・発走時刻ごとに
オッズが取れているかどうかを一覧表示する。

「後半レースだけ取れない」のか「特定の競馬場だけ取れない」のか、
それとも「スクレイパーのパース失敗」なのかを切り分けるために使う。
"""
import pandas as pd
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/entries_this_week.csv"
df = pd.read_csv(path)

print(f"全 {len(df)} 頭 / {df['race_id'].nunique()} レース\n")

g = df.groupby("race_id").agg(
    track=("track_name", "first") if "track_name" in df.columns else ("track_code", "first"),
    race_no=("race_number", "first") if "race_number" in df.columns else ("race_id", lambda s: str(s.iloc[0])[-2:]),
    post_time=("post_time", "first") if "post_time" in df.columns else ("race_id", lambda s: "-"),
    n_horses=("horse", "size"),
    odds_ok=("odds_win", lambda s: s.notna().sum()),
).reset_index()
g["odds_pct"] = (g["odds_ok"] / g["n_horses"] * 100).round(0)

print("=== レース別のオッズ取得状況 ===")
print(g[["track", "race_no", "post_time", "n_horses", "odds_ok", "odds_pct"]]
      .sort_values(["track", "race_no"]).to_string(index=False))

print("\n=== レース番号ごとの集計(後半レースほど取れないかの確認) ===")
by_no = g.groupby("race_no").agg(
    races=("race_id", "size"),
    odds_ok_races=("odds_ok", lambda s: (s > 0).sum()),
).reset_index()
by_no["取得率%"] = (by_no["odds_ok_races"] / by_no["races"] * 100).round(0)
print(by_no.to_string(index=False))

print("\n=== 競馬場ごとの集計 ===")
by_track = g.groupby("track").agg(
    races=("race_id", "size"),
    odds_ok_races=("odds_ok", lambda s: (s > 0).sum()),
).reset_index()
by_track["取得率%"] = (by_track["odds_ok_races"] / by_track["races"] * 100).round(0)
print(by_track.to_string(index=False))
