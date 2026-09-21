"""
単勝オッズ取得(netkeiba オッズAPI経由)

【なぜAPIを使うのか】
出馬表ページ(shutuba.html)のHTMLには、オッズは最初から入っていない。
実際のHTMLソースを確認したところ、全馬のオッズ欄が "---.-"、人気欄が "**" のままで、
オッズはページ表示後にJavaScriptが別APIから取得して差し込んでいる。
そのためHTMLの表を読む方式では、描画タイミング次第で取りこぼしが起きる。

このモジュールはそのAPIを直接叩いて確実にオッズを取る。
  https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=<race_id>&type=1
  type=1 が単勝。発売前・確定後などオッズが無いときは
  {"status":"middle","data":"","reason":"result odds empty"} のように data が空で返る。

レスポンスのJSON構造はnetkeiba側の仕様変更で変わりうるため、
「馬番 -> オッズ数値」を見つけ出す処理は構造を決め打ちせず、
JSONを再帰的に走査して取り出す方式にしている。
解析に失敗した場合は生レスポンスをログに残すので、そこから修正できる。
"""
import json
import re

ODDS_API = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1"


def _to_float(v):
    """'3.2' のような値を float に。オッズとして妥当な範囲のみ返す。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip().replace(",", "")
        if not re.fullmatch(r"\d+(?:\.\d+)?", s):
            return None
        f = float(s)
    else:
        return None
    # 単勝オッズは最低1.0倍。極端な値は別項目(人気順位など)の誤取得とみなす
    return f if 1.0 <= f <= 10000 else None


def _extract_odds(obj):
    """JSONを再帰的に走査して {馬番(int): 単勝オッズ(float)} を組み立てる。

    netkeibaのオッズAPIは馬番をキーにした辞書で値を返す形が基本だが、
    値が文字列だったり ["3.2","1"] のような配列だったりと揺れがあるため、
    「キーが1〜18の整数」かつ「値からオッズらしき数値が取れる」箇所を拾う。
    """
    best = {}

    def walk(node):
        if isinstance(node, dict):
            candidate = {}
            for k, v in node.items():
                if not (isinstance(k, str) and k.isdigit()):
                    continue
                umaban = int(k)
                if not (1 <= umaban <= 18):
                    continue
                if isinstance(v, (str, int, float)):
                    f = _to_float(v)
                elif isinstance(v, list) and v:
                    # ["3.2", "1"] のような形。最初にオッズとして妥当な値を採用
                    f = next((x for x in (_to_float(i) for i in v) if x is not None), None)
                elif isinstance(v, dict):
                    f = next((x for x in (_to_float(i) for i in v.values()) if x is not None), None)
                else:
                    f = None
                if f is not None:
                    candidate[umaban] = f
            # 2頭以上そろっていれば出走表らしいとみなす(最も頭数が多いものを採用)。
            # 値が1.0〜10000倍の範囲に収まることを別途チェックしているため、
            # 人気順位など別項目のdictを誤って拾う危険は小さい。
            if len(candidate) >= 2 and len(candidate) > len(best):
                best.clear()
                best.update(candidate)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return best


def fetch_odds(page, race_id: str, log=print) -> dict:
    """PlaywrightのページからオッズAPIを呼び、{馬番: 単勝オッズ} を返す。

    page はすでにnetkeibaを開いているPlaywrightのPageオブジェクト。
    同じブラウザ文脈からfetchすることでCookieやRefererが引き継がれ、
    APIが弾かれにくくなる。取得できない場合は空dictを返す。
    """
    url = ODDS_API.format(race_id=race_id)
    try:
        raw = page.evaluate(
            """async (url) => {
                const res = await fetch(url, {credentials: 'include'});
                return await res.text();
            }""",
            url,
        )
    except Exception as e:
        log(f"    オッズAPI呼び出し失敗 [{race_id}]: {e}")
        return {}

    if not raw:
        return {}

    try:
        obj = json.loads(raw)
    except Exception:
        log(f"    オッズAPIがJSONでない [{race_id}]: {raw[:200]}")
        return {}

    # data が空文字のときは「まだ発売前」または「確定後に消えた」状態
    data = obj.get("data") if isinstance(obj, dict) else None
    if data in ("", None, {}, []):
        reason = obj.get("reason") if isinstance(obj, dict) else ""
        log(f"    オッズ未提供 [{race_id}] reason={reason}")
        return {}

    # data が文字列でJSONが二重に入っている場合にも対応
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            pass

    odds = _extract_odds(data if data is not None else obj)
    if not odds:
        # 構造が変わって取れなかった場合、直せるように生レスポンスを残す
        log(f"    オッズAPIの構造を解析できず [{race_id}] raw={raw[:500]}")
    return odds


# ----------------------------------------------------------------------
# ブラウザ(Playwright)を使わない版。定期オッズ更新ジョブ用。
# オッズAPIは普通のHTTPリクエストでJSONを返すので、ブラウザを起動しなくてよい。
# 起動コストがないぶん、10分おきの定期実行でも数十秒で終わる。
# ----------------------------------------------------------------------
def _parse_response(raw: str, race_id: str, log=print) -> dict:
    """APIの応答文字列を {馬番: オッズ} に変換する(fetch_odds と共通の解析)。"""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        log(f"    オッズAPIがJSONでない [{race_id}]: {raw[:200]}")
        return {}
    data = obj.get("data") if isinstance(obj, dict) else None
    if data in ("", None, {}, []):
        reason = obj.get("reason") if isinstance(obj, dict) else ""
        log(f"    オッズ未提供 [{race_id}] reason={reason}")
        return {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            pass
    odds = _extract_odds(data if data is not None else obj)
    if not odds:
        log(f"    オッズAPIの構造を解析できず [{race_id}] raw={raw[:500]}")
    return odds


def fetch_odds_http(race_id: str, log=print, timeout: int = 15) -> dict:
    """HTTPでオッズAPIを直接呼び、{馬番: 単勝オッズ} を返す。失敗時は空dict。"""
    import urllib.request
    url = ODDS_API.format(race_id=race_id)
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"    オッズAPI呼び出し失敗 [{race_id}]: {e}")
        return {}
    return _parse_response(raw, race_id, log)
