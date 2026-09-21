"""
オッズだけを取り直して予測ファイルを更新する(定期実行用)

予測モデルは動かさない。1着率・3連対率などAIの評価はそのままで、
オッズと、オッズから計算する項目(期待値・人気順位・人気との乖離・妙味)だけを最新化する。
ブラウザを使わないので1回数十秒で終わり、10分おきの定期実行に向いている。

使い方:
  python src/update_odds.py site/predictions.csv
  python src/update_odds.py site/predictions.csv --only-upcoming   # 発走前のレースだけ更新

発走済みのレースは、確定後にAPIがオッズを返さなくなる(data が空)ことがある。
その場合は既存のオッズを消さずに残す(最後に取れた値=ほぼ最終オッズが残る)。
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from fetch_odds import fetch_odds_http

JST = timezone(timedelta(hours=9))


def _value_tag(ev):
    """predict.py と同じ閾値で妙味を判定する。"""
    if pd.isna(ev):
        return ""
    if ev >= 1.3:
        return "妙味大"
    if ev >= 1.0:
        return "妙味あり"
    if ev >= 0.75:
        return "妥当"
    return "過剰人気"


def recompute_odds_columns(df: pd.DataFrame) -> pd.DataFrame:
    """オッズから導出する列を predict.py と同じ式で計算し直す。"""
    df["odds_win"] = pd.to_numeric(df["odds_win"], errors="coerce")
    df["market_rank"] = df.groupby("race_id")["odds_win"].rank(ascending=True, method="min")
    df["rank_diff"] = df["market_rank"] - df["pred_win_rank"]
    df.loc[df["odds_win"].isna(), ["market_rank", "rank_diff"]] = np.nan
    df["expected_value"] = (df["pred_win_norm"] * df["odds_win"]).round(2)
    df["value_tag"] = df["expected_value"].apply(_value_tag)
    return df


def _is_upcoming(post_time, now: datetime, grace_min: int = 10) -> bool:
    """発走時刻(例 '15:45')が、今より grace_min 分前以降ならTrue。不明なら更新対象にする。"""
    if not isinstance(post_time, str) or ":" not in post_time:
        return True
    try:
        h, m = map(int, post_time.split(":")[:2])
    except ValueError:
        return True
    post = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return post >= now - timedelta(minutes=grace_min)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="更新する predictions.csv")
    ap.add_argument("--only-upcoming", action="store_true",
                    help="発走前(と発走直後10分以内)のレースだけ更新する")
    ap.add_argument("--sleep", type=float, default=0.8,
                    help="レース間の待機秒数(アクセス負荷を抑えるため)")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print(f"{args.csv} がありません。予測がまだ一度も作られていないため終了します。")
        return 2

    df = pd.read_csv(args.csv)
    if df.empty:
        print("予測ファイルが空です。終了します。")
        return 2
    if "odds_win" not in df.columns:
        df["odds_win"] = np.nan

    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    if "race_date" in df.columns and not (df["race_date"].astype(str) == today).any():
        print(f"予測ファイルは {df['race_date'].iloc[0]} のもので、今日({today})ではありません。終了します。")
        return 2

    race_ids = list(dict.fromkeys(df["race_id"].astype(str)))
    if args.only_upcoming and "post_time" in df.columns:
        pt = df.groupby(df["race_id"].astype(str))["post_time"].first()
        race_ids = [r for r in race_ids if _is_upcoming(pt.get(r), now)]

    print(f"{now:%H:%M} オッズ更新: 対象 {len(race_ids)} レース")
    updated_races = 0
    for rid in race_ids:
        odds = fetch_odds_http(rid)
        if odds:
            mask = df["race_id"].astype(str) == rid
            new = df.loc[mask, "umaban"].map(lambda u: odds.get(int(u)) if pd.notna(u) else None)
            # 取れた馬だけ上書きし、取れなかった馬は既存値を残す
            df.loc[mask, "odds_win"] = new.where(new.notna(), df.loc[mask, "odds_win"])
            updated_races += 1
            print(f"  [{rid}] {int(rid[-2:])}R {len(odds)}頭分を更新")
        time.sleep(args.sleep)

    df = recompute_odds_columns(df)
    df.to_csv(args.csv, index=False)
    print(f"完了: {updated_races}/{len(race_ids)} レースのオッズを更新しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
