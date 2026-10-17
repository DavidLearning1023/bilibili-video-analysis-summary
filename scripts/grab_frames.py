#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽帧工具：一次找出「信息页」并给出可直接引用的时间点。

为什么需要它：手工拼 ffmpeg 命令抽帧有三个反复踩的坑——
  ① 用定时抽帧（每 2 秒一张）对幻灯片类视频是浪费：43 张里通常只有 10 张有信息；
  ② 抽完还要人工在几十张里找「哪张是榜单表」，来回读图很贵；
  ③ 想核对某句口播，得裁字幕条，写错 crop 坐标就得到一张全黑的图（实测错两次）。
本脚本把三件事做成三条命令：
  --scene   镜头检测，一个场景一张（默认，幻灯片类首选）
  --at      指定秒数抽帧（已知位置时最快）
  --sub     裁字幕条堆叠（核对口播原句专用）

用法：
  python scripts/grab_frames.py video1080.m4s --out frames            # 镜头检测 + 索引表
  python scripts/grab_frames.py video1080.m4s --out frames --sheet 4  # 再拼 2×2 联系表
  python scripts/grab_frames.py v.m4s --at 65,119,204 --out frames
  python scripts/grab_frames.py v.m4s --sub 99,100,101,102 --out sub  # 字幕条竖排

输出：图片 + 一张「序号 / 秒 / 时间码 / 文件名」对照表，写进 frames/index.tsv
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

CANDIDATES = [
    os.environ.get("FFMPEG_BIN", ""),
    "ffmpeg",
    r"D:\FFmpeg-20251218\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]


def ffmpeg():
    for c in CANDIDATES:
        if not c:
            continue
        if os.path.isfile(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    sys.exit("[FAIL] 找不到 ffmpeg。设环境变量 FFMPEG_BIN 指向 ffmpeg.exe，或把 bin 加进 PATH。")


def hms(sec):
    return f"{int(sec)//3600:02d}:{int(sec)%3600//60:02d}:{int(sec)%60:02d}"


def run(cmd, quiet=True):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if p.returncode != 0:
        sys.stderr.write(p.stderr[-1500:])
        raise SystemExit(f"[FAIL] ffmpeg 退出码 {p.returncode}")
    return p.stderr


def parse_srt(path, offset=0.6):
    """取每条字幕的起始秒。这是**最好的抽帧锚点**：讲到一个新话题时，幻灯片通常在那一刻换。

    比全片镜头检测好在哪：实测一个 7 分钟带人像小窗的视频，镜头检测能报出 82 个「场景」
    （人像和字幕一直在动），而转写只有 17 段——17 张图正好覆盖全部信息页。
    """
    txt = open(path, encoding="utf-8", errors="ignore").read()
    out = []
    for m in re.finditer(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->", txt):
        h, mi, s, ms = (int(x) for x in m.groups())
        out.append(h * 3600 + mi * 60 + s + ms / 1000 + offset)
    return out


def thin(secs, min_gap):
    """丢掉与上一张间隔过小的帧（人像镜头切换会连发）。"""
    keep, last = [], -1e9
    for t in secs:
        if t - last >= min_gap:
            keep.append(t)
            last = t
    return keep


def scene_times(video, thr):
    """一次解码拿到所有镜头切换点（不落图，很快）。"""
    err = run([ffmpeg(), "-hide_banner", "-i", video,
               "-vf", f"select='gt(scene,{thr})',metadata=print", "-f", "null", "-"])
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]


def extract_at(video, secs, outdir, prefix, q=3):
    made = []
    for i, t in enumerate(secs, 1):
        name = os.path.join(outdir, f"{prefix}{i:03d}_t{int(t):04d}.jpg")
        run([ffmpeg(), "-hide_banner", "-ss", f"{t}", "-i", video,
             "-frames:v", "1", "-q:v", str(q), "-y", name])
        if not os.path.exists(name):
            sys.stderr.write(f"[WARN] {t:.1f}s 抽帧失败\n")
            continue
        made.append((i, t, name))
    return made


def parse_srt_cues(path):
    """返回 [(start, end, text)]，给 manifest 用（把帧挂到最近的口播上）。"""
    txt = open(path, encoding="utf-8", errors="ignore").read()
    cues = []
    for block in re.split(r"\n\s*\n", txt.strip()):
        m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                      r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})", block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = " ".join(block.split("\n")[2:]).strip()
        cues.append((s, e, body))
    return cues


def nearest_cue(cues, t):
    for s, e, txt in cues:
        if s <= t <= e:
            return txt
    prev = [c for c in cues if c[0] <= t]
    return prev[-1][2] if prev else ""


def dense_extract(video, fps, dedupe, outdir, prefix="d", q=3):
    """密集采样 + 可选去重。

    dedupe 用 ffmpeg 的 mpdecimate：**在抽帧之后**比较相邻帧，把几乎一样的丢掉。
    幻灯片类视频这一步通常能砍掉一半以上，而且不需要 PIL / numpy。
    """
    vf = f"fps={fps}"
    if dedupe:
        vf += ",mpdecimate"
    vf += ",showinfo"
    pat = os.path.join(outdir, f"{prefix}%04d.jpg")
    err = run([ffmpeg(), "-hide_banner", "-i", video, "-vf", vf,
               "-vsync", "vfr", "-q:v", str(q), "-y", pat])
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]
    files = sorted(f for f in os.listdir(outdir)
                   if f.startswith(prefix) and f.endswith(".jpg"))
    return [(i, t, os.path.join(outdir, files[i - 1]))
            for i, t in enumerate(times, 1) if i <= len(files)]


def contact_sheets(frames, outdir, n, width=640):
    """n 张拼一张（2×2 或 2×1），先看结构再决定读哪张原图。"""
    sheets = []
    for k in range(0, len(frames), n):
        grp = frames[k:k + n]
        if len(grp) < 2:
            continue
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error"]
        for _i, _t, p in grp:
            cmd += ["-i", p]
        filt = ";".join(f"[{i}:v]scale={width}:-1[v{i}]" for i in range(len(grp)))
        cols = 2 if len(grp) > 1 else 1
        rows = (len(grp) + cols - 1) // cols
        # 不足一行时补空
        layout = "|".join(
            f"{(i % cols) * width}_{(i // cols) * int(width * 9 / 16)}"
            for i in range(len(grp))
        )
        chain = "".join(f"[v{i}]" for i in range(len(grp)))
        filt += f";{chain}xstack=inputs={len(grp)}:layout={layout}:fill=black"
        out = os.path.join(outdir, f"sheet_{k // n + 1:02d}.jpg")
        cmd += ["-filter_complex", filt, "-q:v", "3", "-y", out]
        run(cmd)
        sheets.append(out)
    return sheets


def main():
    ap = argparse.ArgumentParser(description="镜头检测 / 定点 / 字幕条抽帧")
    ap.add_argument("video")
    ap.add_argument("--out", default="frames")
    ap.add_argument("--scene", action="store_true", help="镜头检测模式（默认）")
    ap.add_argument("--thr", type=float, default=0.25, help="场景阈值，默认 0.25；幻灯片切换常在 0.2–0.35")
    ap.add_argument("--at", default="", help="指定秒数，逗号分隔，如 65,119,204")
    ap.add_argument("--from-srt", default="", help="用字幕每条起始时间当锚点（最省图；先转写再抽帧时首选）")
    ap.add_argument("--min-gap", type=float, default=4.0,
                    help="两帧最小间隔秒数，默认 4；人像镜头会连发，间隔太密的丢掉")
    ap.add_argument("--sub", default="", help="字幕条模式：指定秒数，裁底部横条后竖排成一张")
    ap.add_argument("--dense", type=float, default=0.0,
                    help="密集采样：每秒抽多少帧（0=不开）。0.2=每5秒一张，1=每秒一张，2=快速动作")
    ap.add_argument("--dedupe", action="store_true",
                    help="配合 --dense：用 mpdecimate 丢掉几乎相同的帧（幻灯片类常省一半以上）")
    ap.add_argument("--srt", default="", help="给 manifest 挂口播：帧会标注所在字幕句")
    ap.add_argument("--sheet", type=int, default=0, help="每 N 张拼一张联系表（建议 4）")
    ap.add_argument("--width", type=int, default=640, help="联系表里每张的宽度")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # 同一目录重复跑时，旧帧会残留，导致「序号 ↔ 时间」错位（静默错，比报错危险）。
    # 抽帧前只清掉本次前缀的旧文件，不碰目录里的别的东西。
    prefix = "d" if args.dense else "s"
    for f in os.listdir(args.out):
        if f.startswith(prefix) and f.endswith(".jpg") and f[len(prefix):len(prefix) + 4].isdigit():
            os.remove(os.path.join(args.out, f))

    if args.sub:
        secs = [float(x) for x in args.sub.split(",") if x.strip()]
        strips = []
        for i, t in enumerate(secs, 1):
            p = os.path.join(args.out, f"sub_{i:02d}_t{int(t):04d}.jpg")
            # 底部 14% 通常是字幕区；先整幅抽出来再裁，避免坐标猜错导致全黑
            run([ffmpeg(), "-hide_banner", "-ss", f"{t}", "-i", args.video, "-frames:v", "1",
                 "-vf", "crop=iw:ih*0.14:0:ih*0.82,scale=iw*1.5:-1", "-q:v", "2", "-y", p])
            strips.append(p)
        stack = os.path.join(args.out, "subtitle_stack.jpg")
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error"]
        for p in strips:
            cmd += ["-i", p]
        cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(strips)))
                + f"vstack=inputs={len(strips)}", "-q:v", "2", "-y", stack]
        run(cmd)
        print(f"[OK] 字幕条 {len(strips)} 条 → {stack}")
        return

    if args.at:
        secs = [float(x) for x in args.at.split(",") if x.strip()]
        made = extract_at(args.video, secs, args.out, "s")
        mode = f"定点 {len(made)} 张"
    elif args.dense:
        made = dense_extract(args.video, args.dense, args.dedupe, args.out, "d")
        mode = (f"密集 {args.dense} fps" + ("（已去重）" if args.dedupe else "")
                + f" → {len(made)} 张")
    elif args.from_srt:
        secs = thin(parse_srt(args.from_srt), args.min_gap)
        made = extract_at(args.video, secs, args.out, "s")
        mode = f"转写锚点 {len(made)} 张（来自 {os.path.basename(args.from_srt)}）"
    else:
        times = scene_times(args.video, args.thr)
        if not times:
            sys.exit("[FAIL] 没检测到场景切换，换 --thr 0.15 或改用 --at / --from-srt")
        kept = thin(times, args.min_gap)
        made = extract_at(args.video, kept, args.out, "s")
        mode = f"镜头检测 {len(times)} 个切换 → 间隔过滤后 {len(made)} 张（阈值 {args.thr}，最小间隔 {args.min_gap}s）"

    idx = os.path.join(args.out, "index.tsv")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("no\tsec\t time\t\tfile\n")
        for i, t, p in made:
            f.write(f"{i}\t{t:.1f}\t{hms(t)}\t{os.path.basename(p)}\n")

    # manifest：把帧按时间线排好并挂上所在口播句。
    # 这是"理解原意"的关键件——顺序和上下文由文件承载，不靠读图的人自己记。
    # 密集采样时按「同一句口播」合并成段：435 行逐帧表没人读得下去，
    # 而「一段 → 一组帧」正好是可理解的结构。
    srt_path = args.srt or args.from_srt
    if srt_path and os.path.exists(srt_path):
        cues = parse_srt_cues(srt_path)

        def cue_idx(t):
            for k, (s, e, _txt) in enumerate(cues):
                if s <= t <= e:
                    return k
            prev = [k for k, c in enumerate(cues) if c[0] <= t]
            return prev[-1] if prev else 0

        groups = []
        for i, t, p in made:
            k = cue_idx(t)
            if groups and groups[-1][0] == k:
                groups[-1][2].append((i, t, p))
            else:
                groups.append([k, t, [(i, t, p)]])

        mf = os.path.join(args.out, "manifest.md")
        with open(mf, "w", encoding="utf-8") as f:
            f.write(f"# 抽帧时间线（{len(made)} 帧 / {len(groups)} 段）\n\n")
            f.write("按时间顺序排列，按口播切段。每一段 = 一个话题 + 该话题期间的帧，"
                    "用来重建「先讲什么、后讲什么」的因果顺序。\n\n")
            f.write("| 段 | 时间范围 | 帧 | 张数 | 口播 |\n|---|---|---|---|---|\n")
            for gi, (k, t0, items) in enumerate(groups, 1):
                t1 = items[-1][1]
                a, b = items[0][2], items[-1][2]
                span = (f"{os.path.basename(a)}–{os.path.basename(b)}"
                        if len(items) > 1 else os.path.basename(a))
                txt = cues[k][2].replace("|", "/")[:120] if k < len(cues) else ""
                f.write(f"| {gi} | {hms(t0)}–{hms(t1)} | {span} | {len(items)} | {txt} |\n")
        print(f"[OK] 时间线 manifest → {mf}  （{len(groups)} 段）")

    print(f"[OK] {mode} → {args.out}")
    print(f"{'#':>3} {'时间码':>9}  文件")
    for i, t, p in made:
        print(f"{i:>3} {hms(t):>9}  {os.path.basename(p)}")

    if args.sheet:
        sheets = contact_sheets(made, args.out, args.sheet, args.width)
        print(f"\n[OK] 联系表 {len(sheets)} 张（每张 {args.sheet} 帧，宽 {args.width}px）：")
        for s in sheets:
            print("  " + os.path.basename(s))
        print("先读联系表定位，再回读 3–5 张原图——不要逐张读原图。")
    print(f"\n索引：{idx}")


if __name__ == "__main__":
    main()
