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

# Chromium は OpenType(CFF)の日本語フォント(Noto Sans CJK など)を埋め込めず、1文字ずつ図形(Type3)にするので
# PDFが巨大になる(11/14: 全レース80MB → スマホで開けない)。PDFを作るときだけ TrueType の IPAex ゴシックに置き換える。
# 必要: apt-get install fonts-ipaexfont
PDF_FONT_CSS = """
* { font-family: "IPAexGothic", "IPAPGothic", sans-serif !important; }
.serif, h1, h2, h3, .pp-title { font-family: "IPAexMincho", "IPAexGothic", serif !important; }
"""


# 1レースが1ページに収まるように縮める(11/21: 18頭立てだと2枚に分かれることがあった)。
# 印刷と同じ幅で並べてから、カードの高さが印刷できる高さを超えていたら CSS zoom で縮める。
MM = 96 / 25.4
FIT_JS = """(h) => { let n = 0; document.querySelectorAll('.pp-card').forEach(c => {
  if (c.style.display === 'none' || c.classList.contains('off')) return;
  c.style.zoom = ''; const H = c.getBoundingClientRect().height;
  if (H > h) { c.style.zoom = (h / H).toFixed(3); n++; } }); return n; }"""


def fit(page, w_mm, h_mm):
    """印刷できる範囲(w_mm×h_mm)の幅で並べ、はみ出すカードを縮める。縮めた枚数を返す"""
    page.set_viewport_size({"width": int(w_mm * MM), "height": int(h_mm * MM)})
    page.emulate_media(media="print")
    page.wait_for_timeout(50)
    return page.evaluate(FIT_JS, h_mm * MM * 0.97)


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
            page.add_style_tag(content=PDF_FONT_CSS)
            page.wait_for_timeout(400)
            n = page.eval_on_selector_all(".pp-card", "els => els.filter(e => e.style.display !== 'none').length")
            if not n:
                continue
            path = os.path.join(out, fn)
            if "lay=1" in q:
                fit(page, 297 - 12, 210 - 12)
            page.pdf(path=path, prefer_css_page_size=True, print_background=True)
            page.emulate_media(media=None); page.set_viewport_size({"width": 1300, "height": 900})
            files.append(dict(label=label, file=fn, pages=count_pages(path), races=n))
            print(f"  {label}: {n}レース → {fn}")
        # レースごとのPDF(印刷ページの「選んだレースをPDFに」でスマホ側でつなげる)
        #   1: 1枚1レース(A4横)  2: 2レースで1枚用(A4縦の半分 = A5横。スマホ側で2つずつA4縦に並べる)
        rids = page.eval_on_selector_all(".pp-r", "els => els.map(e => e.value)")
        os.makedirs(os.path.join(out, "r"), exist_ok=True)
        for rid in rids:
            for lay in ("1", "2"):
                page.goto(f"{url}?sel=id:{rid}&lay={lay}")
                page.add_style_tag(content=PDF_FONT_CSS)
                page.wait_for_timeout(150)
                path = os.path.join(out, "r", f"{rid}_{lay}.pdf")
                if lay == "1":
                    fit(page, 297 - 12, 210 - 12)
                    page.pdf(path=path, prefer_css_page_size=True, print_background=True)
                else:
                    page.add_style_tag(content="@page { size: 210mm 148.5mm; margin: 4mm; } .pp-card { break-after: auto !important; page-break-after: auto !important; }")
                    fit(page, 210 - 8, 148.5 - 8)
                    page.pdf(path=path, width="210mm", height="148.5mm", prefer_css_page_size=True, print_background=True)
                page.emulate_media(media=None); page.set_viewport_size({"width": 1300, "height": 900})
                if count_pages(path) > 1:
                    print(f"  [WARN] {rid} lay={lay} が{count_pages(path)}枚になった")
        print(f"  レースごと: {len(rids)}レース × 2レイアウト → r/")
        b.close()
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(dict(made=datetime.now(JST).strftime("%H:%M"), ts=int(datetime.now(JST).timestamp()), files=files,
                       per_race=True), f, ensure_ascii=False)
    print(f"[INFO] {len(files)}個のPDFを {out} に作りました")


if __name__ == "__main__":
    main()
