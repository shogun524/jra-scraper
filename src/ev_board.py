"""
EVボード: AI予想・オッズの歪み・オッズの動き・馬体重を組み合わせて、単勝/馬連/ワイドの期待値(EV)を計算する。

確率の作り方(data/ev_calibration.json の係数。fit_ev_calibration.py が過去の結果から推定・毎週更新):
    p ∝ p_ai^a × p_mkt^b × exp(c × オッズの動き)
  - p_ai : AIの1着率 / p_mkt: 今の単勝オッズから計算した市場の確率 / 動き: その日の最初の記録→今の log(最初/今)
  - 馬連・ワイドの的中確率は、この1着率から Harville 法(1着率を順に条件付きで掛ける)で計算する
EV = 的中確率 × 今のオッズ(ワイドは下限オッズ)。1.0を超えると「この確率が正しければ割安」。

注意: 係数は過去データで推定した値で、EV>1 の買い目が実際に儲かるかはまだ確かめられていない。
成績ページで、EV≧1.0 として表示した買い目を実際の払戻で採点している。
"""
import json
import os
from itertools import combinations

import numpy as np
import pandas as pd

# g2, g3: 2着・3着の確率を「1着率^γ」で作る補正(Henery/Lo-Bacon-Shone型)。
# そのままのHarville法は人気薄が2・3着に来る確率を高く見積もりすぎ、人気薄絡みの馬連・ワイドのEVが過大になる。
# JRA 2019〜2026年(26,387レース)の着順から推定: γ2=0.838, γ3=0.713(Harvilleは γ=1)
DEFAULT_CAL = {"a": 0.0, "b": 1.0, "c_move": 0.0, "g2": 0.838, "g3": 0.713}
CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ev_calibration.json")


def load_calibration(path: str = CAL_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return {**DEFAULT_CAL, **json.load(f)}
    except Exception:
        return dict(DEFAULT_CAL)


def harville_pair_tables(p, g2: float = 1.0, g3: float = 1.0):
    """p(合計1)から 馬連Q[i,j] と ワイドW[i,j](ともに3着内)を返す。
    2着は q = p^g2、3着は r = p^g3 を正規化した確率で条件付けする(g=1 なら通常のHarville)"""
    n = len(p)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, None)
    p = p / p.sum()
    q = p ** g2
    q = q / q.sum()
    r = p ** g3
    r = r / r.sum()
    a = p[:, None, None]
    b = q[None, :, None] / np.clip(1 - q[:, None, None], 1e-9, None)
    c = r[None, None, :] / np.clip(1 - r[:, None, None] - r[None, :, None], 1e-9, None)
    T = a * b * c
    idx = np.arange(n)
    T[idx, idx, :] = 0
    T[idx, :, idx] = 0
    T[:, idx, idx] = 0
    S = T + T.transpose(0, 2, 1) + T.transpose(1, 0, 2) + T.transpose(1, 2, 0) + T.transpose(2, 0, 1) + T.transpose(2, 1, 0)
    P12 = p[:, None] * q[None, :] / np.clip(1 - q[:, None], 1e-9, None)
    np.fill_diagonal(P12, 0)
    return P12 + P12.T, S.sum(axis=2)


def win_moves(history: pd.DataFrame | None, race_id: str) -> dict:
    """単勝オッズの動き {馬番: (最初のオッズ, 今のオッズ, log(最初/今))}"""
    if history is None or history.empty:
        return {}
    h = history[(history["race_id"] == race_id) & (history["bet"] == "単勝")].sort_values("ts")
    if h.empty:
        return {}
    out = {}
    for combo, g in h.groupby("combo"):
        o0, o1 = g["odds"].iloc[0], g["odds"].iloc[-1]
        if pd.notna(o0) and pd.notna(o1) and o0 > 0 and o1 > 0:
            out[int(combo)] = (float(o0), float(o1), float(np.log(o0 / o1)))
    return out


def blended_win_probs(g: pd.DataFrame, cal: dict, moves: dict) -> np.ndarray | None:
    """1レース分(馬ごとの行)の混合1着率。単勝オッズが揃っていなければ None"""
    if g["odds_win"].isna().any() or (g["odds_win"] <= 0).any():
        return None
    p_ai = (g["pred_win_norm"] / g["pred_win_norm"].sum()).to_numpy(dtype=float)
    if "model" in g.columns and (g["model"] == "odds").all():
        # オッズ+補正モデル(market_model.py)の1着率はオッズを織り込み済みなので、そのまま使う
        cal = {**cal, "a": 1.0, "b": 0.0}
    inv = 1 / g["odds_win"].to_numpy(dtype=float)
    p_mkt = inv / inv.sum()
    mv = np.array([moves.get(int(u), (0, 0, 0.0))[2] for u in g["umaban"]])
    s = cal["a"] * np.log(np.clip(p_ai, 1e-5, None)) + cal["b"] * np.log(np.clip(p_mkt, 1e-5, None)) + cal["c_move"] * mv
    e = np.exp(s - s.max())
    return e / e.sum()


# EVを出すオッズの上限。これより大きいオッズは
#  - 99999.9倍・9999.9倍 = まだ1票も入っていない組の仮の値(前日発売の売れ始めに大量に出る)
#  - 数百倍以上 = 票が薄くて1票で大きく動き、確率×オッズが数十〜数百になって意味をなさない
# ので、EVボードからは外す(単勝100倍・馬連300倍・ワイド(下限)100倍まで。オッズ急落ページの範囲とそろえた)
EV_MAX_ODDS = {"単勝": 100.0, "馬連": 300.0, "ワイド": 100.0}
# 馬連・ワイドは、その券種のオッズ自体が見ている確率の何倍まで「歪み」として認めるか。
# 前日や朝の売れ始めは馬連・ワイドの票が薄く、単勝から作った確率と10倍以上ずれて EV が10を超える組が並んだ(11/13 前日オッズ)。
# 一定以上のずれは「票が薄いだけ」とみなして外す(EVは最大でも約1.24にとどまる)
EV_MAX_RATIO = 1.6   # 11/14: 3.0だと薄い馬連が軒並み上限(EV2.3〜2.5)に張り付いた。本当の歪みは大きくても1.5倍程度とみて1.6に


def race_ev(g: pd.DataFrame, combos: pd.DataFrame | None, history: pd.DataFrame | None, cal: dict) -> pd.DataFrame:
    """1レースの EV 表(単勝・馬連・ワイド)。列: race_id, bet, combo, odds, odds_high, p, ev, move, weight_diff"""
    g = g.sort_values("umaban").reset_index(drop=True)
    rid = str(g["race_id"].iloc[0])
    moves = win_moves(history, rid)
    p = blended_win_probs(g, cal, moves)
    if p is None:
        return pd.DataFrame()
    u = g["umaban"].astype(int).tolist()
    wd = dict(zip(u, g["weight_diff"])) if "weight_diff" in g.columns else {}
    rows = []
    for i, um in enumerate(u):
        o = float(g["odds_win"].iloc[i])
        if not (o > 0) or o > EV_MAX_ODDS["単勝"]:
            continue
        mv = moves.get(um)
        rows.append(dict(race_id=rid, bet="単勝", combo=str(um), odds=o, odds_high=np.nan, p=p[i], ev=p[i] * o,
                         move=mv[2] if mv else np.nan, odds_first=mv[0] if mv else np.nan,
                         weight_diff=wd.get(um, np.nan)))
    if combos is not None and not combos.empty:
        Q, W = harville_pair_tables(p, cal.get("g2", 1.0), cal.get("g3", 1.0))
        pos = {um: i for i, um in enumerate(u)}
        c = combos[combos["race_id"] == rid]
        for _, r in c.iterrows():
            try:
                x, y = map(int, str(r["combo"]).split("-"))
            except ValueError:
                continue
            if x not in pos or y not in pos:
                continue
            i, j = pos[x], pos[y]
            prob = Q[i, j] if r["bet"] == "馬連" else W[i, j]
            o = r["odds"]
            if pd.isna(o) or o <= 0 or float(o) > EV_MAX_ODDS.get(r["bet"], 300.0):
                continue
            rows.append(dict(race_id=rid, bet=r["bet"], combo=f"{min(x, y)}-{max(x, y)}", odds=float(o),
                             odds_high=r.get("odds_high", np.nan), p=prob, ev=prob * float(o),
                             move=np.nan, odds_first=np.nan, weight_diff=np.nan))
    out = pd.DataFrame(rows)
    if not out.empty:
        pair = out["bet"].isin(["馬連", "ワイド"])
        if pair.any():
            inv = 1.0 / out.loc[pair, "odds"]
            q = inv / inv.groupby(out.loc[pair, "bet"]).transform("sum")   # その券種のオッズが見ている確率
            ratio = out.loc[pair, "p"] / q
            # ワイドは3組が当たりなので合計が3になるよう直す
            ratio = ratio.where(out.loc[pair, "bet"] != "ワイド", ratio / 3)
            drop = ratio[ratio > EV_MAX_RATIO].index
            out = out.drop(index=drop)
    return out.reset_index(drop=True)


def board(pred: pd.DataFrame, combos: pd.DataFrame | None, history: pd.DataFrame | None,
          cal: dict | None = None) -> pd.DataFrame:
    """全レースの EV 表"""
    cal = cal or load_calibration()
    out = [race_ev(g, combos, history, cal) for _, g in pred.groupby("race_id")]
    out = [x for x in out if not x.empty]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def load_odds_files(site_dir: str, date: str):
    """site/odds_history の最新オッズ(馬連・ワイド)とその日の記録を読む。無ければ None"""
    d = os.path.join(site_dir, "odds_history")
    latest = os.path.join(d, f"latest_{date}.csv")
    hist = os.path.join(d, f"odds_{date}.csv")
    lt = pd.read_csv(latest, dtype={"race_id": str, "combo": str}) if os.path.exists(latest) else None
    hs = pd.read_csv(hist, dtype={"race_id": str, "combo": str}) if os.path.exists(hist) else None
    return lt, hs
