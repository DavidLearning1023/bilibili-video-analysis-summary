#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 版式自检：先用数字筛，再只对可疑页出图。

为什么需要它：交付前"翻一遍 PDF"是对的，但不必**逐页出图再逐页读**——
实测 20 页的报告读 8 张整页图，占了整轮输入的很大一块，而真正有问题的只有 1 页。
本脚本先算每页的字数、内容覆盖比、链接数、有没有漏印的「收起」，
把「哪一页值得看」缩到 2–3 页，再用 --images 出图。

用法：
  python scripts/check_pdf.py report.pdf
  python scripts/check_pdf.py report.pdf --images 3      # 只出最可疑的 3 页 + 首页
  python scripts/check_pdf.py report.pdf --outdir .tmp

判读标准（本脚本内置）：
  · 每页字数 < 420      → 可能有大面积空白（break-inside 把内容顶到下一页）
  · 内容覆盖比 < 45%    → 同上，更可靠
  · 文本里出现「收起」  → 折叠区在打印时没被展平（report.css 那条 details[open] 规则缺失）
  · 全文 0 链接         → 时间戳链接丢了
退出码 1 = 有告警，0 = 干净。
"""
from __future__ import annotations

import argparse
import os
import re
import sys


def load(path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # 旧名
        except ImportError:
            sys.exit("[FAIL] 需要 pymupdf：pip install pymupdf")
    return pymupdf.open(path)


def coverage(page):
    """内容底线位置：本页最后一个内容块的下沿 ÷ 页高。

    直接量「内容只铺到页面的百分之多少」——比算包围盒面积稳健得多，
    而且恰好对应要发现的问题：`break-inside:avoid` 把一大块内容顶到下一页后，
    当前页会剩下一大截白，这个比值就会明显偏低（正常页接近 0.9）。"""
    boxes = []
    for b in page.get_text("blocks"):
        boxes.append((b[1], b[3]))
    for d in page.get_drawings():
        r = d.get("rect")
        if r:
            boxes.append((r.y0, r.y1))
    if not boxes:
        return 0.0
    # 去掉页脚（页脚通常在最底部且有固定间距），用倒数第二块判断更准
    boxes.sort(key=lambda x: x[1])
    bottom = boxes[-1][1] if len(boxes) == 1 else boxes[-2][1]
    return max(0.0, min(1.0, bottom / page.rect.height))


def main():
    ap = argparse.ArgumentParser(description="PDF 版式自检")
    ap.add_argument("pdf")
    ap.add_argument("--images", type=int, default=0, help="导出最可疑的 N 页（另加首页）")
    ap.add_argument("--outdir", default="._pdfcheck")
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--min-chars", type=int, default=420)
    ap.add_argument("--min-cov", type=float, default=0.70)
    args = ap.parse_args()

    doc = load(args.pdf)
    rows, warn = [], []
    total_links = 0
    for i, pg in enumerate(doc, 1):
        text = pg.get_text()
        chars = len(text.replace("\n", "").replace(" ", ""))
        cov = coverage(pg)
        links = len(pg.get_links())
        total_links += links
        rows.append((i, chars, cov, links))
        # 只认「内容底线位置」——它才是空白页的直接证据。
        # 字数单独用会误报：表格页留白很多但字数少，其实并不空。
        if cov < args.min_cov:
            warn.append((i, chars, cov))

    print(f"页数 {doc.page_count} · 链接 {total_links} 个 · 文件 {os.path.getsize(args.pdf)/1024:.0f} KB")
    print(f"{'页':>3} {'字数':>6} {'覆盖':>7} {'链接':>5}")
    for i, chars, cov, links in rows:
        flag = "  ← 页尾留白过多" if cov < args.min_cov else ""
        print(f"{i:>3} {chars:>6} {cov*100:>6.0f}% {links:>5}{flag}")

    full = "\n".join(pg.get_text() for pg in doc)
    problems = []
    # 「收起 / 展开」是 summary::after 的 CSS 产物，会独立成行；
    # 正文里的「没展开」这类词组不该误报，所以只认独立成词的情况。
    if re.search(r"(^|\n)\s*(收起|展开)\s*(\n|$)", full):
        problems.append("文本里出现独立的「收起 / 展开」——折叠区在打印时没展平（见 report.css 的 details[open] 规则）")
    if total_links == 0:
        problems.append("全文 0 个链接——时间戳链接丢失")
    for i, chars, cov in warn:
        problems.append(f"第 {i} 页偏空（字数 {chars}，覆盖 {cov*100:.0f}%）")

    if problems:
        print("\n[WARN]")
        for p in problems:
            print("  - " + p)
    else:
        print("\n[OK] 无明显版式问题")

    if args.images:
        os.makedirs(args.outdir, exist_ok=True)
        picks = [1] + [i for i, _c, _v in sorted(warn, key=lambda x: x[2])[:args.images]]
        seen, made = set(), []
        for i in picks:
            if i in seen or i < 1 or i > doc.page_count:
                continue
            seen.add(i)
            out = os.path.join(args.outdir, f"p{i:02d}.png")
            doc[i - 1].get_pixmap(dpi=args.dpi).save(out)
            made.append(out)
        print("\n出图（先看这几张就够）：")
        for m in made:
            print("  " + m)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
