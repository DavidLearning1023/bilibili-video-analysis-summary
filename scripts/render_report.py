"""把报告正文片段组装成单文件 HTML，并（可选）渲染 PDF。

用法
----
    # 只出 HTML
    python scripts/render_report.py --body body.html --title "20261016-《xxx》分析总结" --out "D:/xxx.html"

    # HTML + PDF（需要 playwright，见 references/troubleshooting.md）
    python scripts/render_report.py --body body.html --title "..." --out "...html" --pdf

为什么要有这个脚本
------------------
样式表固定在 assets/template/report.css，模型只产出「正文片段」。
好处：① CSS 不进每次的输出 token（约省 4–5k token/篇）；
     ② 版式不会因为每次重写 CSS 而漂移；
     ③ 单文件 HTML 便于分享，PDF 由同一份 HTML 渲染，两者天然一致。

正文片段里不要写 <html>/<head>/<style>/<body>，也不要写 <div class="page">——
这些由 shell.html 提供。片段直接从报头 <header class="masthead"> 开始。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.normpath(os.path.join(HERE, "..", "assets", "template"))
SHELL = os.path.join(TPL, "shell.html")
CSS = os.path.join(TPL, "report.css")


def build(body_path, title, out_path):
    for p in (SHELL, CSS):
        if not os.path.exists(p):
            raise SystemExit(f"[FAIL] 模板缺失：{p}")
    body = open(body_path, encoding="utf-8").read().strip()
    shell = open(SHELL, encoding="utf-8").read()
    css = open(CSS, encoding="utf-8").read().rstrip("\n")

    # 片段里若误带了外壳，直接剥掉，避免出现两套 <style>
    body = re.sub(r"^\s*<!DOCTYPE[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<html[^>]*>|</html>|<head[^>]*>.*?</head>|<body[^>]*>|</body>",
                  "", body, flags=re.I | re.S)
    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.I | re.S)
    # 去掉片段自带的 <div class="page"> 外壳（内层内容保留）
    body = re.sub(r'^\s*<div class="page">\s*', "", body)
    body = re.sub(r'\s*</div>\s*$', "", body)

    html = (shell.replace("{{CSS}}", css)
                 .replace("{{TITLE}}", title)
                 .replace("{{BODY}}", body))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def to_pdf(html_path, pdf_path=None):
    """渲染 PDF：Playwright 内置 Chromium 跑 print 媒体，纸张与边距由 CSS @page 决定。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] 未安装 playwright，跳过 PDF。"
              "安装：pip install playwright && playwright install chromium-headless-shell",
              file=sys.stderr)
        return None
    import time
    html_path = os.path.abspath(html_path)
    if pdf_path is None:
        pdf_path = os.path.splitext(html_path)[0] + ".pdf"
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto("file:///" + html_path.replace("\\", "/"), wait_until="networkidle")
        pg.emulate_media(media="print")
        # 折叠区必须在 PDF 里全部可见。CSS 里的 details>.body{display:block}
        # 在 Chromium 上不生效——闭合 <details> 的内容由 UA 的 ::details-content
        # 以 content-visibility:hidden 隐藏，子元素改 display 也进不了盒树。
        # 所以只能在这里把 open 打开（只影响本次打印，磁盘上的 HTML 仍是折叠态）。
        pg.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        # 注意：prefer_css_page_size=True 时，纸张与边距由 CSS 的 @page 决定
        # （report.css 里是 A4 + 18mm 25mm）。这里不要再传 margin，传了也会被 CSS 覆盖，
        # 容易误导后来维护的人。
        pg.pdf(path=pdf_path, format="A4", print_background=True,
               prefer_css_page_size=True)
        b.close()
    print(f"[OK] PDF {pdf_path}  {time.time() - t0:.1f}s  "
          f"{os.path.getsize(pdf_path) / 1024:.0f} KB")
    return pdf_path


def main():
    ap = argparse.ArgumentParser(description="组装报告 HTML 并可选渲染 PDF")
    ap.add_argument("--body", required=True, help="正文片段 html 路径")
    ap.add_argument("--title", required=True, help="文档标题（与文件名一致，不含 .html）")
    ap.add_argument("--out", required=True, help="输出的单文件 html 路径")
    ap.add_argument("--pdf", action="store_true", help="同时渲染 PDF")
    ap.add_argument("--pdf-out", default=None, help="PDF 输出路径，默认与 html 同名")
    args = ap.parse_args()

    out = build(args.body, args.title, args.out)
    n_style = open(out, encoding="utf-8").read().count("<style>")
    print(f"[OK] HTML {out}  ({os.path.getsize(out) / 1024:.0f} KB, style 块 {n_style} 个)")
    if n_style != 1:
        print("[WARN] style 块不是 1 个，正文片段里可能混进了 <style>", file=sys.stderr)
    if args.pdf:
        to_pdf(out, args.pdf_out)


if __name__ == "__main__":
    main()
