"""
印刷用ページ(print.html)から、できあがりのPDFを作る(GitHub Actions で動かす)。
スマホのアプリ内ブラウザ(LINE・X・Gmail など)では印刷ボタンが動かないので、ダウンロードするだけで済むようにする。

作る物(site/pdf/ に置き、pdf/index.json に一覧):
  全レース(1枚1レース・A4横) / 2レースで1枚(A4縦) / AI買い目のレース / 荒れそうなレース / 競馬場ごと
print.html の ?sel=…&lay=… でレースとレイアウトを選んで、Chromium で印刷する。

使い方: python src/print_pdf.py site/print.html site/pdf
必要: pip install playwright pypdf && python -m playwright install --with-deps chromium(日本語フォントは fonts-noto-cjk)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def count_pages(path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001
        return 0


def fresh(out, max_age_sec):
    """前回作ってから max_age_sec 秒以内なら True(作り直さない)"""
    import time
    try:
        with open(os.path.join(out, "index.json"), encoding="utf-8") as f:
            return time.time() - json.load(f).get("ts", 0) < max_age_sec
    except (OSError, ValueError):
        return False


def main():
    src, out = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3 and fresh(out, int(sys.argv[3])):
        print("[INFO] 前回作ってから時間がたっていないので作り直しません")
        return
    os.makedirs(out, exist_ok=True)
    from playwright.sync_api import sync_playwright
    url = "file://" + os.path.abspath(src)
    targets = [("全レース", "all.pdf", "sel=all&lay=1"), ("2レースで1枚", "compact.pdf", "sel=all&lay=2"),
               ("AI買い目のレース", "bet.pdf", "sel=bet&lay=1"), ("荒れそうなレース", "are.pdf", "sel=are&lay=1")]
    files = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1300, "height": 900})
        page.goto(url)
        page.wait_for_timeout(300)
        venues = page.eval_on_selector_all(".pp-venue", "els => els.map(e => e.dataset.venue)")
        for v in venues:
            targets.append((v, f"{v}.pdf", f"sel=v:{v}&lay=1"))
        for label, fn, q in targets:
            page.goto(f"{url}?{q}")
            page.wait_for_timeout(400)
            n = page.eval_on_selector_all(".pp-card", "els => els.filter(e => e.style.display !== 'none').length")
            if not n:
                continue
            path = os.path.join(out, fn)
            page.pdf(path=path, prefer_css_page_size=True, print_background=True)
            files.append(dict(label=label, file=fn, pages=count_pages(path), races=n))
            print(f"  {label}: {n}レース → {fn}")
        b.close()
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(dict(made=datetime.now(JST).strftime("%H:%M"), ts=int(datetime.now(JST).timestamp()), files=files), f, ensure_ascii=False)
    print(f"[INFO] {len(files)}個のPDFを {out} に作りました")


if __name__ == "__main__":
    main()
