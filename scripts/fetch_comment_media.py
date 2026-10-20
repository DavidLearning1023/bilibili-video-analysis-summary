#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评论区图片：决策 → 下载 → 联系表

为什么需要这个脚本
------------------
B站评论接口一直在返回 `content.pictures`，但两条通道的归一化都把它丢了：
  * MCP 的 normalizeRemoteComment() 只取 content.message
  * 本仓库 fetch_video.py 的 walk() 也只取 message（2026-10-19 已修）
实测缇宝那期 100 条顶层评论里 57 条带图、占样本赞量 96.8%，最高赞 21,055 就是一张
梗图 —— 全部曾被降级成空字符串。此脚本负责把这块补回来。

核心不是"下载"，是**先算要不要看**：
  * 图片评论占总赞 >= 15%  → must   必做（生成联系表）
  * 5% – 15%               → sample 抽样（默认只取前 10 张）
  * < 5%                   → skip   跳过，在报告里注明未纳入

两级读图（准确率的来源）
------------------------
  联系表（320px/格）→ 只能做**分类与选片**，不能读数
  原图             → 才用于读数、判结论
实测 320px 下图片类型（梗图/截图/立绘/广告/结构图）100% 可辨，但小字必糊。
所以：**联系表只路由，原图才判读**。凡是「有没有越线」「数字是多少」一律回原图。

用法
----
    # 最常用：一条命令完成决策 + 下载 + 拼表
    python scripts/fetch_comment_media.py BV1Vkag6TExf --comments 100

    # 已经有 fetch_video.py 的输出，直接复用，不重复请求
    python scripts/fetch_comment_media.py BV1Vkag6TExf --json ./out/BV1Vkag6TExf.json

    # 只看决策不下载
    python scripts/fetch_comment_media.py BV1Vkag6TExf --json ./out/BV.json --dry-run

    # 控制数量与版式
    python scripts/fetch_comment_media.py BV1Vkag6TExf --top 30 --cols 6 --cell 300

依赖
----
PIL（Pillow）。**不写死任何解释器路径**——每个用户机器上的 Python 都不一样。

本脚本在 import 失败时会**自动在本机找一个带 PIL 的解释器，并用那个解释器重跑自己**；
真的找不到才报错，且会列出候选解释器与验证命令，不会静默跳过。

要指定用哪个解释器：设环境变量 `BILI_PYTHON=<解释器路径>`。
"""

import argparse
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _interp_candidates():
    """候选解释器：BILI_PYTHON 环境变量 → 常见受管 venv → PATH 上的 python。

    不写死绝对路径：换机器、换客户端都还能用。
    """
    cands = []
    env = os.environ.get("BILI_PYTHON")
    if env:
        cands.append(env)
    home = pathlib.Path.home()
    for pat in ("workbuddy/binaries/python/envs/*/Scripts/python.exe",
                ".workbuddy/binaries/python/envs/*/Scripts/python.exe",
                ".local/bin/python*"):
        try:
            cands += [str(p) for p in sorted(home.glob(pat))]
        except Exception:
            pass
    for name in ("python", "python3"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    out, seen = [], set()
    for c in cands:
        try:
            key = os.path.normcase(os.path.abspath(c))
        except Exception:
            continue
        if key in seen or not os.path.isfile(c):
            continue
        seen.add(key)
        out.append(c)
    return out


def _interp_has_pil(interp):
    try:
        r = subprocess.run([interp, "-c", "import PIL"], capture_output=True, timeout=25)
        return r.returncode == 0
    except Exception:
        return False


def _need_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return True
    except ImportError:
        pass
    # 换一个带 PIL 的解释器重跑自己（用环境变量防止无限递归）
    if os.environ.get("BILI_PIL_REEXEC") != "1":
        try:
            self_key = os.path.normcase(os.path.abspath(sys.executable))
        except Exception:
            self_key = ""
        for interp in _interp_candidates():
            try:
                if os.path.normcase(os.path.abspath(interp)) == self_key:
                    continue
            except Exception:
                continue
            if _interp_has_pil(interp):
                print(f"[INFO] 当前解释器没有 PIL，改用 {interp} 重跑", file=sys.stderr)
                env = dict(os.environ)
                env["BILI_PIL_REEXEC"] = "1"
                try:
                    rc = subprocess.call([interp, os.path.abspath(__file__)] + sys.argv[1:],
                                         env=env)
                except Exception as e:
                    print(f"[FAIL] 换解释器失败：{e}", file=sys.stderr)
                    return False
                sys.exit(rc)
    print("[FAIL] 缺少 PIL（Pillow），联系表无法生成。", file=sys.stderr)
    print("       候选解释器：", file=sys.stderr)
    for c in _interp_candidates():
        mark = "  [有 PIL]" if _interp_has_pil(c) else ""
        print(f"         {c}{mark}", file=sys.stderr)
    print('       验证命令：<解释器> -c "import PIL; print(PIL.__version__)"', file=sys.stderr)
    print("       指定解释器：set BILI_PYTHON=<解释器路径>", file=sys.stderr)
    return False


def _clean(m):
    return re.sub(r"\[[^\]]{1,20}\]", "", m or "").strip()


def load_comments(bvid, args):
    """优先复用已有 JSON；否则走 fetch_video.py 的取数函数。"""
    if args.json:
        p = pathlib.Path(args.json)
        if not p.exists():
            raise FileNotFoundError(f"--json 指向的文件不存在：{p}")
        d = json.load(open(p, encoding="utf-8"))
        return d.get("comments") or [], d.get("comments_meta") or {}, d.get("info") or {}
    import fetch_video as fv
    # 兼容不同版本的 get_comments 签名
    sig = inspect.signature(fv.get_comments)
    cookie = fv.load_cookie(args) if hasattr(fv, "load_cookie") else None
    info = fv.get_info(bvid, cookie)
    aid = info["aid"]
    if "cookie" in sig.parameters:
        cmts, total = fv.get_comments(aid, args.comments, "hot", cookie)
    else:
        cmts, total = fv.get_comments(aid, args.comments, "hot")
    meta = fv.comment_stats(cmts) if hasattr(fv, "comment_stats") else {}
    return cmts, meta, info


def decide(meta):
    share = meta.get("picture_like_share", 0)
    if share >= 15:
        return "must", 20
    if share >= 5:
        return "sample", 10
    return "skip", 0


def download_one(item, outdir, max_bytes=15 * 1024 * 1024):
    url = item["url"]
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        ext = ".jpg"
    fn = outdir / f"{item['rank']:02d}_{item['likes']}_{item['rpid']}{ext}"
    if fn.exists() and fn.stat().st_size > 0:
        return item, fn
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=30) as r:
                # 体积上限：评论里偶有几十 MB 的动图/长截图，一口气读进内存会把并发打爆。
                # 超限直接放弃（联系表里会显示「未下载」，不影响其余帧）。
                data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                print(f"  [WARN] #{item['rank']:02d} 超过 {max_bytes // 1024 // 1024} MB，跳过",
                      file=sys.stderr)
                return item, None
            if data:
                fn.write_bytes(data)
                return item, fn
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] #{item['rank']:02d} 下载失败：{e}", file=sys.stderr)
            else:
                time.sleep(1.2 * (attempt + 1))
    return item, None


def build_sheets(items, outdir, cols, cell, per_sheet):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, max(13, cell // 21))
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    label_h = max(22, cell // 13)
    pad = 6
    sheets = []
    rows = (per_sheet + cols - 1) // cols
    total = len(items)
    for si in range(0, total, per_sheet):
        chunk = items[si:si + per_sheet]
        w = cols * (cell + pad) + pad
        h = rows * (cell + pad + label_h) + pad
        sheet = Image.new("RGB", (w, h), (250, 250, 248))
        d = ImageDraw.Draw(sheet)
        for k, it in enumerate(chunk):
            cx = pad + (k % cols) * (cell + pad)
            cy = pad + (k // cols) * (cell + pad + label_h)
            if it.get("file") and os.path.exists(it["file"]):
                try:
                    im = Image.open(it["file"])
                    if getattr(im, "n_frames", 1) > 1:
                        im.seek(0)
                    im = im.convert("RGB")
                    im.thumbnail((cell, cell))
                    sheet.paste(im, (cx + (cell - im.width) // 2,
                                     cy + label_h + (cell - im.height) // 2))
                except Exception as e:
                    d.text((cx + 6, cy + label_h + 6), "读取失败", fill=(190, 0, 0), font=font)
                    print(f"  [WARN] {it['file']} 无法读取：{e}", file=sys.stderr)
            else:
                d.text((cx + 6, cy + label_h + 6), "未下载", fill=(190, 0, 0), font=font)
            d.rectangle([cx, cy + label_h, cx + cell, cy + label_h + cell], outline=(215, 215, 205))
            # 超长图（宽高比 > 4）在 320px 格子里会被压成一条，必须提醒回读原图。
            # 实测：棍木那期 641 赞那张 1080×4713 的截图，缩略后整段不可读。
            w_, h_ = it.get("w") or 0, it.get("h") or 0
            longpic = ""
            if w_ and h_:
                if h_ / w_ > 4:
                    longpic = " 长图"
                elif w_ / h_ > 4:
                    longpic = " 宽图"
            d.text((cx + 2, cy + 3), f"#{it['rank']:02d} {it['likes']}赞{longpic}",
                   fill=(25, 25, 25), font=font)
        out = outdir / f"sheet_{si // per_sheet + 1:02d}.jpg"
        sheet.save(out, quality=88)
        sheets.append(out)
    return sheets


def main():
    ap = argparse.ArgumentParser(description="评论区图片：阈值决策 + 下载 + 联系表")
    ap.add_argument("video", nargs="?", help="BV 号或完整 URL；给了 --json 时可省略（从 JSON 里读）")
    ap.add_argument("--comments", type=int, default=100,
                    help="顶层评论条数，默认 100（给了 --json 时本参数无效，以 JSON 里的样本为准）")
    ap.add_argument("--json", help="复用 fetch_video.py 产出的 JSON，避免重复请求")
    ap.add_argument("--top", type=int, default=0, help="下载几张（0=按阈值自动：must 20 / sample 10）")
    ap.add_argument("--cols", type=int, default=5, help="联系表列数，默认 5")
    ap.add_argument("--cell", type=int, default=320, help="单格像素，默认 320（再小就认不出文字）")
    ap.add_argument("--per-sheet", type=int, default=20, help="每张联系表格数，默认 20")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--cookie-file", help="凭据 JSON（转交 fetch_video.py）")
    ap.add_argument("--dry-run", action="store_true", help="只输出决策，不下载")
    args = ap.parse_args()

    if not args.video and not args.json:
        print("[FAIL] 至少要给 `video`（BV号/URL）或 `--json`（已有取数结果）之一", file=sys.stderr)
        return 2

    # 联系表需要 PIL。**放在最前面确认**：缺 PIL 时本函数会自动换一个带 PIL 的解释器
    # 重跑自己；若等打印完前半段才检查，重跑后会出现重复输出。
    if not _need_pil():
        return 2

    bvid = None
    if args.video:
        try:
            import fetch_video as fv0
            bvid = fv0.parse_bvid(args.video)
        except Exception as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return 2

    try:
        cmts, meta, info = load_comments(bvid, args)
    except FileNotFoundError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2
    # 只给 --json 时，BV 号从 JSON 的 info 里取——不必让用户再输一次
    bvid = bvid or info.get("bvid") or "unknown"
    if not cmts:
        print("[FAIL] 没取到评论", file=sys.stderr)
        return 2

    if not meta:
        import fetch_video as fv
        meta = fv.comment_stats(cmts)

    verdict, auto_top = decide(meta)
    top_n = args.top or auto_top

    print(f"[OK] {info.get('title', bvid)}")
    print(f"[OK] 顶层 {meta.get('n_top')} 条 / 楼中楼 {meta.get('n_replies', 0)} 条")
    print(f"[OK] 含图 {meta.get('with_picture')} 条（{meta.get('picture_share')}%），"
          f"占样本赞量 {meta.get('picture_like_share')}%")
    print(f"[判读] {verdict}　→　下载上限 {top_n} 张")

    if verdict == "skip":
        print(f"\n[SKIP] 图片占总赞 {meta.get('picture_like_share')}% < 5%，本片读文字即可。"
              f"\n       报告里请写明：图片评论 {meta.get('with_picture')} 条、"
              f"占样本赞量 {meta.get('picture_like_share')}%，未纳入分析。")
        return 0
    if args.dry_run:
        return 0

    # 顶层优先 + 赞降序；同一 rpid 只取首图（多图评论极少，且拼表会挤）
    pool = [c for c in cmts if c.get("pictures")]
    pool.sort(key=lambda c: (c.get("is_reply", False), -c.get("likes", 0)))
    picked = []
    for i, c in enumerate(pool[:top_n], 1):
        p = c["pictures"][0]
        picked.append({
            "rank": i, "rpid": c["rpid"], "likes": c.get("likes", 0),
            "text": _clean(c.get("content")), "url": p["url"],
            "w": p.get("w"), "h": p.get("h"),
            "n_pics": len(c["pictures"]),
            "level": c.get("level"), "location": c.get("location"),
        })

    outdir = pathlib.Path(args.out)
    imgdir = outdir / "media"
    imgdir.mkdir(parents=True, exist_ok=True)

    print(f"\n[DOWNLOAD] {len(picked)} 张 → {imgdir}")
    ok = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(download_one, it, imgdir): it for it in picked}
        for f in as_completed(futs):
            it, fn = f.result()
            if fn:
                it["file"] = str(fn)
                ok += 1
    print(f"[OK] 下载 {ok}/{len(picked)}")

    picked.sort(key=lambda x: x["rank"])
    sheets = build_sheets(picked, imgdir, args.cols, args.cell, args.per_sheet)
    print(f"[OK] 联系表 {len(sheets)} 张：")
    for s in sheets:
        print(f"       {s}  ({os.path.getsize(s)/1024:.0f} KB)")

    manifest = outdir / "media" / "manifest.md"
    lines = [f"# 评论区图片清单 · {bvid}", "",
             f"- 顶层评论 {meta.get('n_top')} 条，含图 {meta.get('with_picture')} 条"
             f"（{meta.get('picture_share')}%），占样本赞量 {meta.get('picture_like_share')}%",
             f"- 下载 {ok} 张，联系表 {len(sheets)} 张（{args.cols} 列 × {args.cell}px）", "",
             "> 联系表只用于**分类与选片**，读数必须回原图。", "",
             "| # | 赞 | 尺寸 | 评论文字 | 原图 |", "|---|---|---|---|---|"]
    for it in picked:
        txt = (it["text"] or "（纯图片，无文字）").replace("|", "／")[:48]
        fn = os.path.basename(it.get("file", "")) or "—"
        lines.append(f"| {it['rank']:02d} | {it['likes']} | {it.get('w')}×{it.get('h')} | {txt} | {fn} |")
    manifest.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 清单 {manifest}")

    # 直接打到 stdout：模型不用再读文件就能对上「第几格是什么」
    print("\n--- 联系表内容（格号 / 赞 / 文字）---")
    for it in picked:
        txt = it["text"][:34] if it["text"] else "（纯图）"
        print(f"  #{it['rank']:02d} {it['likes']:>6}赞  {it.get('w')}x{it.get('h')}  {txt}")
    print("\n[下一步] 先读联系表定位，再回读 2–3 张原图；读数一律以原图为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
