"""
オッズ+補正モデル(本命・印を決めるモデル)。

背景(2019〜2026年の検証):
  旧AI(LightGBM、オッズを使わない)は市場(単勝オッズ)より予想が粗く、
  オッズに上乗せしても何も足さなかった(市場+旧AIで旧AIの重みは −0.07 = ほぼ0)。
  旧AIの◎の勝率は26.8%、1番人気は33.5%。拮抗レースでは「上位3頭で一番人気」の方が◎より6〜7pt勝つ。
  そこで「オッズから見た勝つ確率」を土台にして、市場が織り込み切れていない要素だけを足す形にする:

      勝つ確率 ∝ exp( β·log(1/単勝オッズ) + Σ w_k·要素_k )   (レースごとに合計1)

  要素は近走・前走の人気や着順・距離適性・騎手・キャリアなど(出馬表の時点で分かるもの)。
  2012〜2022年で学習 → 2023年〜で確認: ◎の勝率34.0%・単勝回収率81.6%(1番人気33.6%・79.4%)、
  「オッズの見込みより1.15倍以上高く見ている馬」は毎年 単勝回収率86〜104%(全体は約75%)。

オッズが無いレース(前日発売の前など)は旧AIの値をそのまま使う。

使い方:
  python src/market_model.py fit data/features.parquet data/market_model.json   # 学習(retrain.yml で実行)
  from market_model import apply; df = apply(df)                                  # 予想に当てはめる
"""
import json
import os
import sys

import numpy as np
import pandas as pd

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "market_model.json")

FEATS = ["recent5_top3rate", "recent5_avg_finish", "last_race_finish", "prev_popularity", "prev2_finish",
         "dist_aptitude_top3rate", "jockey_winrate", "age_n", "is_female", "jockey_trainer_combo_winrate",
         "career_starts", "prev_c4_minus_fin", "field_size_change"]
MARKS = {1: "◎", 2: "○", 3: "▲", 4: "△", 5: "△"}
VALUE_RATIO = 1.15   # オッズの見込みよりこれ以上高く見ている馬を「妙味」とする


# ---------------------------------------------------------------- 要素(学習用)
def add_prev_features(F: pd.DataFrame) -> pd.DataFrame:
    """features.parquet(1行1頭・全期間)に前走・前々走の情報を足す"""
    F = F.copy()
    F["race_date"] = pd.to_datetime(F["race_date"])
    F = F.sort_values(["horse", "race_date", "race_id"])
    g = F.groupby("horse")
    F["prev_popularity"] = g["popularity"].shift(1)
    F["prev2_finish"] = g["finish"].shift(2)
    F["prev_c4_minus_fin"] = g["corner4"].shift(1) - g["finish"].shift(1)
    F["field_size_change"] = F["field_size"] - g["field_size"].shift(1)
    return F


def common_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["age_n"] = pd.to_numeric(df.get("age"), errors="coerce")
    df["is_female"] = df.get("sex", pd.Series("", index=df.index)).astype(str).str.contains("牝").astype(float)
    return df


# ---------------------------------------------------------------- 学習
def _design(df, spec):
    cols = [np.log(1 / df["odds_win"].astype(float).values)]
    for f in spec["feats"]:
        x = (pd.to_numeric(df[f], errors="coerce").astype(float).values if f in df.columns
             else np.full(len(df), np.nan))
        mu, sd = spec["mu"][f], spec["sd"][f]
        z = np.clip(np.where(np.isnan(x), 0, (x - mu) / sd), -4, 4)
        cols.append(z)
        cols.append(np.isnan(x).astype(float))
    return np.column_stack(cols)


def _fit_logit(X, gid, y):
    from scipy.optimize import minimize
    starts = np.r_[0, np.flatnonzero(gid[1:] != gid[:-1]) + 1]
    idx = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(gid)]))

    def f(w):
        s = X @ w
        m = np.maximum.reduceat(s, starts)
        e = np.exp(s - m[idx])
        Z = np.add.reduceat(e, starts)
        p = e / Z[idx]
        L = -(y * (s - m[idx] - np.log(Z[idx]))).sum()
        G = -((y - p)[:, None] * X).sum(0)
        return L / len(starts), G / len(starts)
    w0 = np.zeros(X.shape[1]); w0[0] = 1.0
    r = minimize(f, w0, jac=True, method="L-BFGS-B")
    return r.x, f


def _fit_market(d):
    """オッズだけのモデルの β(オッズから見た勝つ確率 ∝ (1/オッズ)^β)"""
    X = np.log(1 / d["odds_win"].astype(float).values)[:, None]
    w, _ = _fit_logit(X, d["race_id"].values, d["y"].values)
    return float(w[0])


def fit(features_parquet, out_json, test_from="2023-01-01"):
    cols = ["race_id", "race_date", "horse", "umaban", "finish", "odds_win", "popularity", "corner4", "field_size",
            "age", "sex"] + [f for f in FEATS if f not in ("prev_popularity", "prev2_finish", "prev_c4_minus_fin",
                                                           "field_size_change", "age_n", "is_female")]
    F = pd.read_parquet(features_parquet, columns=list(dict.fromkeys(cols)))
    F = add_prev_features(F)
    F = F[(F["race_date"] >= "2012-01-01") & F["odds_win"].notna() & (F["odds_win"] > 0) & F["finish"].notna()]
    F = common_features(F)
    F["y"] = (F["finish"] == 1).astype(float)
    F = F[F.groupby("race_id")["y"].transform("sum") == 1].sort_values(["race_id", "umaban"]).reset_index(drop=True)

    def spec_from(d):
        mu = {f: float(np.nanmean(pd.to_numeric(d[f], errors="coerce"))) for f in FEATS}
        sd = {f: float(np.nanstd(pd.to_numeric(d[f], errors="coerce")) or 1.0) for f in FEATS}
        return dict(feats=FEATS, mu=mu, sd=sd)

    report = {}
    tr, te = F[F["race_date"] < test_from], F[F["race_date"] >= test_from]
    if len(te):
        sp = spec_from(tr)
        w, _ = _fit_logit(_design(tr, sp), tr["race_id"].values, tr["y"].values)
        sp["w"] = w.tolist()
        sp["beta_market"] = _fit_market(tr)
        pt = _probs(te, sp)
        pm = _market(te, sp["beta_market"])
        te = te.assign(p=pt, pm=pm)
        n = te["race_id"].nunique()
        ll = lambda c: float(-np.log(te.loc[te.y == 1, c]).sum() / n)  # noqa: E731
        top = te.loc[te.groupby("race_id")["p"].idxmax()]
        fav = te.loc[te.groupby("race_id")["odds_win"].idxmin()]
        val = te[te["p"] / te["pm"] >= VALUE_RATIO]
        report = dict(test_from=test_from, races=n, logloss_model=round(ll("p"), 4), logloss_market=round(ll("pm"), 4),
                      honmei_win=round(float(top.y.mean()), 3), honmei_roi=round(float((top.y * top.odds_win).mean() * 100), 1),
                      fav_win=round(float(fav.y.mean()), 3), fav_roi=round(float((fav.y * fav.odds_win).mean() * 100), 1),
                      value_n=int(len(val)), value_win=round(float(val.y.mean()), 3),
                      value_roi=round(float((val.y * val.odds_win).mean() * 100), 1),
                      all_roi=round(float((te.y * te.odds_win).mean() * 100), 1))
        print("[確認]", report)
    sp = spec_from(F)
    w, _ = _fit_logit(_design(F, sp), F["race_id"].values, F["y"].values)
    sp["w"] = w.tolist()
    sp["beta_market"] = _fit_market(F)
    sp["report"] = report
    sp["trained_until"] = str(F["race_date"].max().date())
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(sp, f, ensure_ascii=False, indent=1)
    print(f"[INFO] {out_json} に保存(β={w[0]:.3f}・オッズだけのβ={sp['beta_market']:.3f}、{F.race_id.nunique()}レースで学習)")
    for f_, c in zip(FEATS, w[1::2]):
        print(f"  {f_:30s} {c:+.3f}")


# ---------------------------------------------------------------- 当てはめ
def _softmax_by(s, gid):
    s = pd.Series(s, index=gid.index)
    e = np.exp(s - s.groupby(gid).transform("max"))
    return (e / e.groupby(gid).transform("sum")).values


def _probs(df, spec):
    s = _design(df, spec) @ np.asarray(spec["w"])
    return _softmax_by(s, df["race_id"].astype(str))


def _market(df, beta):
    return _softmax_by(beta * np.log(1 / df["odds_win"].astype(float).values), df["race_id"].astype(str))


def harville_top3(p, g2=0.838, g3=0.713):
    """1着の確率から3着以内の確率。2着は p^g2、3着は p^g3 で条件付け(ev_board.py と同じ補正。素のHarvilleは人気薄の2・3着を高く見すぎる)"""
    p = np.clip(np.asarray(p, float), 1e-9, None)
    p = p / p.sum()
    n = len(p)
    if n <= 3:
        return np.ones(n)
    q = p ** g2; q = q / q.sum()
    r = p ** g3; r = r / r.sum()
    P2 = p[:, None] * q[None, :] / (1 - q[:, None])          # [1着, 2着]
    np.fill_diagonal(P2, 0)
    T = P2[:, :, None] * r[None, None, :] / np.clip(1 - r[:, None, None] - r[None, :, None], 1e-9, None)
    idx = np.arange(n)
    T[idx, idx, :] = 0; T[idx, :, idx] = 0; T[:, idx, idx] = 0
    return np.clip(p + P2.sum(0) + T.sum((0, 1)), 0, 1)


_SPEC = None


def load(path=MODEL_PATH):
    global _SPEC
    if _SPEC is None:
        try:
            with open(path, encoding="utf-8") as f:
                _SPEC = json.load(f)
        except (OSError, ValueError):
            _SPEC = {}
    return _SPEC


def apply(df: pd.DataFrame, spec=None) -> pd.DataFrame:
    """オッズのそろっているレースの 1着率・3連対率・順位・印 をオッズ+補正モデルで置き換える。
    旧AIの値は lgb_win / lgb_top3 に残す(何度呼んでも同じ結果になる)。"""
    spec = spec or load()
    if not spec or "w" not in spec or df is None or df.empty or "odds_win" not in df.columns:
        return df
    df = common_features(df)
    for c, src in (("lgb_win", "pred_win_norm"), ("lgb_top3", "pred_top3_norm")):
        if c not in df.columns:
            df[c] = df[src]
    rid = df["race_id"].astype(str)
    odds = pd.to_numeric(df["odds_win"], errors="coerce")
    ok = odds.gt(1.0).groupby(rid).transform("all") & odds.groupby(rid).transform("size").ge(2)
    df["model"] = np.where(ok, "odds", "ai")
    df["market_win"] = np.nan
    df["value_ratio"] = np.nan
    if ok.any():
        sub = df[ok].copy()
        sub["odds_win"] = odds[ok]
        p = _probs(sub, spec)
        pm = _market(sub, float(spec.get("beta_market", 1.0)))
        df.loc[ok, "pred_win_norm"] = p
        df.loc[ok, "market_win"] = pm
        df.loc[ok, "value_ratio"] = np.round(p / pm, 3)
        t3 = pd.Series(np.nan, index=sub.index)
        for _, g in sub.assign(_p=p).groupby(rid[ok]):
            t3.loc[g.index] = harville_top3(g["_p"].values)
        df.loc[ok, "pred_top3_norm"] = t3
    no = ~ok
    df.loc[no, "pred_win_norm"] = df.loc[no, "lgb_win"]
    df.loc[no, "pred_top3_norm"] = df.loc[no, "lgb_top3"]
    _derive(df)
    return df


def _value_tag(r):
    if pd.isna(r):
        return None
    return "妙味大" if r >= 1.3 else "妙味あり" if r >= VALUE_RATIO else "過剰人気" if r <= 0.8 else "妥当"


def _derive(df):
    """1着率・3連対率から決まる列(predict.py と同じ定義)を作り直す"""
    g = df.groupby(df["race_id"].astype(str))
    df["pred_win_rank"] = g["pred_win_norm"].rank(ascending=False, method="min").astype(int)
    df["pred_top3_rank"] = g["pred_top3_norm"].rank(ascending=False, method="min").astype(int)
    df["mark"] = df["pred_win_rank"].map(MARKS).fillna("")
    df["gap_from_top"] = (g["pred_win_norm"].transform("max") - df["pred_win_norm"]).mul(100).round(1)
    df["stability"] = (df["pred_top3_norm"] / df["pred_win_norm"].clip(lower=1e-6)).round(2)
    score = df["pred_win_norm"] * 0.7 + (df["pred_top3_norm"] / 3) * 0.3
    df["ai_score"] = (score / score.groupby(df["race_id"].astype(str)).transform("max") * 100).round(0)

    def comp(s):
        t = s.nlargest(3)
        return round((t.iloc[0] - t.iloc[2]) * 100, 1) if len(t) >= 3 else np.nan
    df["race_competitiveness"] = g["pred_win_norm"].transform(comp)
    # 妙味: オッズの見込みと比べて何倍高く見ているか(検証: 1.15倍以上の馬は単勝回収率84%・全体72%)
    if "value_ratio" in df.columns:
        vt = df["value_ratio"].map(_value_tag)
        df["value_tag"] = vt.where(vt.notna(), df.get("value_tag", ""))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "fit":
        fit(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
