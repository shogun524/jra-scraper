"""
当日のトラックバイアス(内外)。

同じ日・同じ競馬場・同じ芝/ダートで、そのレースより前に終わったレースの「3着以内に来た馬の馬番の位置(馬番÷頭数)」の平均。
  小さい = 内枠の馬が来ている(内有利)、大きい = 外枠の馬が来ている(外有利)。何もなくても平均は約0.54。

検証(2012〜2026年10月、前のレースが2つ以上ある46万頭。確定単勝オッズに上乗せする力があるか):
  市場はこれを織り込み切れていない(2012〜17年・2024年・2025〜26年の3つの期間とも同じ向き)。
  特に「外有利(0.58以上)の日の内枠」は市場の見込みより勝ちにくい(勝率で −0.4〜−0.8ポイント、単勝回収率59〜69%)。
  「内有利(0.42以下)の日の内枠」は単勝回収率81〜83%(全体は約75%)。
  ただしこれだけで回収率100%は超えない。穴馬を選ぶときの判断材料として使う。

使い方: compute(predictions_df, results_df) -> {race_id: dict(n=前のレース数, val=値, label=表示, side='内'/'外'/None)}
"""
import pandas as pd

LABELS = [(0.42, "内有利", "内"), (0.47, "やや内有利", "内"), (0.58, "フラット", None), (0.62, "やや外有利", "外"), (9, "外有利", "外")]
MIN_RACES = 2


def label_of(val):
    for th, lab, side in LABELS:
        if val <= th:
            return lab, side
    return LABELS[-1][1], LABELS[-1][2]


def compute(pred: pd.DataFrame, res: pd.DataFrame | None) -> dict:
    if res is None or res.empty or pred is None or pred.empty or "surface" not in pred.columns:
        return {}
    p = pred.copy()
    p["race_id"] = p["race_id"].astype(str)
    p["race_number"] = pd.to_numeric(p["race_number"], errors="coerce")
    races = p.groupby("race_id").agg(venue=("track_name", "first"), surface=("surface", "first"),
                                     rno=("race_number", "first"), n=("umaban", "size")).reset_index()
    r = res.copy()
    r["race_id"] = r["race_id"].astype(str)
    r["finish"] = pd.to_numeric(r["finish"], errors="coerce")
    r["umaban"] = pd.to_numeric(r["umaban"], errors="coerce")
    size = r.groupby("race_id")["umaban"].transform("size")
    r["pos"] = r["umaban"] / size
    top = r[r["finish"] <= 3].groupby("race_id")["pos"].mean()
    races["top_pos"] = races["race_id"].map(top)
    out = {}
    for (_, _), g in races.groupby(["venue", "surface"]):
        g = g.sort_values("rno")
        for _, row in g.iterrows():
            prev = g[(g["rno"] < row["rno"]) & g["top_pos"].notna()]
            if len(prev) < MIN_RACES:
                out[row["race_id"]] = dict(n=len(prev), val=None, label="", side=None)
                continue
            val = float(prev["top_pos"].mean())
            lab, side = label_of(val)
            out[row["race_id"]] = dict(n=len(prev), val=val, label=lab, side=side, surface=str(row["surface"]))
    return out


def favored(umaban, n, side):
    """その馬がバイアスの有利な側(内=頭数の内側1/3・外=外側1/3)なら True、不利な側なら False、どちらでもなければ None"""
    try:
        rel = float(umaban) / float(n)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if side is None:
        return None
    inner, outer = rel <= 1 / 3, rel > 2 / 3
    if side == "内":
        return True if inner else (False if outer else None)
    return True if outer else (False if inner else None)
