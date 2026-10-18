#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 B站视频/音频流（dash），一次做对。

为什么需要它：B站 dash 流的 CDN 有四道坎，每次现场拼 urllib 都要重踩一遍——
  ① 不带 `Referer: https://www.bilibili.com/video/BVxxx/` + 浏览器 UA → HTTP 567
     （yt-dlp 直接下同样 567，别浪费时间试它）
  ② 大文件单次请求会被静默截断（实测 1080p 声明 46.3 MB，只给 5.7 MB，EOF 正常返回）
  ③ 截断的 m4s 仍能被 ffprobe 读出时长，直到 ffmpeg 解码才报
     "Invalid NAL unit size"，报错位置离病因很远
  ④ **单连接会被 CDN 限速到 ~20 KB/s，且 Range 响应经常声明长度却提前断开**
     （实测 169 MB 单连接要 1 小时；同一时刻 8 条连接 2 分钟下完）
所以正确顺序是：先 Range 探测真实总长 → 多线程分段 → 每段用 partial 逐段推进 → 校验字节数。

用法：
  python scripts/fetch_media.py BV11fJX6GEcL --audio            # 只要音轨（最小，转写用）
  python scripts/fetch_media.py BV11fJX6GEcL --video 1080       # 要读画面时必须 ≥1080
  python scripts/fetch_media.py BV11fJX6GEcL --audio --video 1080 --out ./media
  python scripts/fetch_media.py BV11fJX6GEcL --video 1080 --jobs 16   # 网络差时加线程

只依赖标准库 + 同目录的 fetch_video.py（复用其 WBI 签名与凭据发现）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import IncompleteRead

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fetch_video as fv  # noqa: E402  （复用 wbi_fetch / load_cookie / UA）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHUNK = 1024 * 1024              # 1 MB —— 实测最优点：单连接 7 MB/s，8 并发 24 MB/s
JOBS = 8                         # 并行连接数
MIN_SEG = 2 * 1024 * 1024        # 每段至少 2 MB 才值得开一条连接
SEG_MAX = 16 * 1024 * 1024       # 单段上限 16 MB，超出就多切几段
MAX_STALL = 80                   # 连续多少次零进展就放弃该段（保留 part 可续传）
SLOW_MBPS = 1.0                  # 单块速度低于此值即视为源不健康，连续 3 块换源
REFERER = "https://www.bilibili.com/"   # main() 里改成具体视频页，缺了会被 567


def _cookie():
    """从 ~/.bilibili-mcp/config.json 取凭据（与 fetch_video 同源）。"""
    p = os.path.expanduser("~/.bilibili-mcp/config.json")
    if not os.path.exists(p):
        return None
    c = json.load(open(p, encoding="utf-8"))
    return (f"SESSDATA={c['sessdata']}; bili_jct={c['bili_jct']}; "
            f"DedeUserID={c['dedeuserid']}")


def playurl(bvid, cid, cookie):
    st, res, err = fv.wbi_fetch(
        "/x/player/wbi/playurl",
        {"bvid": bvid, "cid": cid, "fnval": 4048, "fnver": 0,
         "fourk": 1, "platform": "pc"},
        cookie,
    )
    if st != 200 or not res or res.get("code") != 0:
        raise RuntimeError(f"playurl 失败：{err or (res or {}).get('message')}")
    return res["data"]


def _headers(cookie, extra=None):
    h = {"User-Agent": UA,
         "Referer": REFERER,
         "Origin": "https://www.bilibili.com",
         "Accept": "*/*",
         "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        h["Cookie"] = cookie
    if extra:
        h.update(extra)
    return h


def probe_size(url, cookie, timeout=15):
    """Range: bytes=0-1023 → 读 Content-Range 的总长。这是分段的前提。

    超时给 15 s 而不是 60 s：多源探测时要给每个源都发一次，
    备用源挂掉时不该把整次下载拖住一分钟。
    """
    r = urllib.request.urlopen(
        urllib.request.Request(url, headers=_headers(cookie, {"Range": "bytes=0-1023"})),
        timeout=timeout,
    )
    cr = r.headers.get("Content-Range", "")
    r.close()
    if "/" not in cr:
        raise RuntimeError(f"服务器未返回 Content-Range（{cr!r}），无法确定总长")
    return int(cr.rsplit("/", 1)[1])


def _fetch_range(url, cookie, start, end, timeout=25):
    """单次 Range 请求。返回 (bytes, err)。

    CDN 常在半路断开连接：响应头声明了长度，body 却提前 EOF。
    `read()` 会抛 IncompleteRead，但**异常对象带着已读到的 partial 字节**——
    丢掉它去整块重试，就是「4 MB 块 × 40 块 × 每次重试」的时间黑洞。
    """
    req = urllib.request.Request(
        url, headers=_headers(cookie, {"Range": f"bytes={start}-{end}"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200 and (end - start + 1) < 64 * 1024:
                # 服务器忽略了 Range（返回 200 全量），只在探测时容忍
                pass
            return r.read(), None
    except IncompleteRead as e:
        return (e.partial or b""), e
    except Exception as e:  # noqa: BLE001
        return b"", e


def _segment(urls, cookie, path, start, end, chunk, seg_i=0):
    """下载 [start, end] 一段，边收边写。返回已写入字节数。

    换源有两套触发条件，缺一不可：
      - **卡住**（连续零字节）→ 每 20 次轮换一次源；
      - **太慢**（单块速度低于 SLOW_MBPS）→ 连续 3 块就换源。
    第二条是实测补的：某些源对某些字节区间没缓存（回源），速度只有 0.5 MB/s
    却仍在推进，「卡住才换源」永远触发不了，于是一个慢段就能吃掉全程一半时间。
    """
    cur = start
    if os.path.exists(path):
        cur = start + os.path.getsize(path)      # 续上次运行留下的 part
    f = open(path, "ab")
    stalls = 0
    slow = 0
    ui = seg_i                                    # 起始源按段号错开
    try:
        while cur <= end:
            sub_end = min(cur + chunk - 1, end)
            url = urls[ui % len(urls)]
            t_req = time.time()
            blob, err = _fetch_range(url, cookie, cur, sub_end)
            if blob:
                blob = blob[: sub_end - cur + 1]      # 防御：万一返回超量
                f.write(blob)
                f.flush()
                cur += len(blob)
                stalls = 0
                dt = max(time.time() - t_req, 0.001)
                if dt > (len(blob) / 1048576) / SLOW_MBPS:
                    slow += 1
                    if slow >= 3 and len(urls) > 1:
                        ui += 1                        # 换个源从当前位置继续
                        slow = 0
                else:
                    slow = 0
            else:
                stalls += 1
                time.sleep(min(0.05 * stalls, 1.0))
                if stalls % 20 == 0 and len(urls) > 1:
                    ui += 1
                if stalls > MAX_STALL:
                    break
    finally:
        f.close()
    return cur - start


def download(urls, cookie, out_path, jobs=JOBS, chunk=CHUNK, force=False):
    """多线程分段下载，返回 (total, 耗时秒, 主机)。

    为什么不是「4 MB 块顺序下」：CDN 按**连接**限速（实测 ~20 KB/s），
    顺序下载 169 MB 要一小时。切成 N 段并行，每段内部用 256 KB 子块 +
    partial 推进，同一文件 2 分钟到手。

    断点续传：out_path 已存在的部分视为可信前缀，只补剩余区间。
    分段中间态写在 `.<name>.part{i}`，校验通过后才合并——**合并前主文件不变**，
    所以中途失败不会留下半截坏文件。
    """
    total, host, last = None, urls[0].split("/")[2], None
    # 所有源都探一遍：baseUrl 与 backupUrl 指向不同码流时长度会不同，
    # 混着下会拼出一个「长度对、内容错位」的文件，而且事后查不出来。
    # 只保留长度与第一个成功源一致的那些，其余剔除。
    good = []
    for u in urls:
        try:
            t = probe_size(u, cookie)
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        if total is None:
            total = t
        if t == total:
            good.append(u)
    if total is None:
        raise RuntimeError(f"所有源都探测不到总长：{last}")
    if total <= 0:
        raise RuntimeError(f"探测到的总长异常：{total}")
    if len(good) < len(urls):
        print(f"[WARN] {len(urls) - len(good)} 个源总长与主源不一致，已剔除",
              file=sys.stderr, flush=True)
    urls = good
    host = urls[0].split("/")[2]

    if force and os.path.exists(out_path):
        os.remove(out_path)
    have = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if have > total:
        os.remove(out_path)
        have = 0
    if have == total:
        return total, 0.0, host

    base = os.path.dirname(os.path.abspath(out_path))
    stem = os.path.basename(out_path)
    t0 = time.time()

    rem = total - have
    # 段数 ≥ 并发数；段太长（>16 MB）会让单连接跑太久，容易被限速拖住，
    # 所以宁可多切几段让线程轮流上。
    n = max(min(jobs, max(1, rem // MIN_SEG + 1)),
            (rem + SEG_MAX - 1) // SEG_MAX)
    seg = (rem + n - 1) // n
    plan = []
    for i in range(n):
        a = have + i * seg
        b = min(have + (i + 1) * seg, total) - 1
        if a <= b:
            plan.append((i, a, b))
    parts = [os.path.join(base, f".{stem}.part{i}") for i, _a, _b in plan]
    for p in parts:                     # 段划分依赖 have，旧 part 一律作废
        if os.path.exists(p):
            os.remove(p)

    print(f"[..] {total/1048576:.1f} MB，已有 {have/1048576:.1f} MB，"
          f"{len(plan)} 段 / {min(jobs, len(plan))} 并发", file=sys.stderr, flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=min(jobs, len(plan))) as ex:
        futs = {ex.submit(_segment, urls, cookie, parts[i], a, b, chunk, i): i
                for i, a, b in plan}
        for fu in as_completed(futs):
            done += fu.result()
            print(f"[..] {done/1048576:6.1f} / {rem/1048576:6.1f} MB  "
                  f"{time.time()-t0:4.0f}s", file=sys.stderr, flush=True)

    bad = [(i, os.path.getsize(parts[i]) if os.path.exists(parts[i]) else 0, b - a + 1)
           for i, a, b in plan
           if (os.path.getsize(parts[i]) if os.path.exists(parts[i]) else 0) != b - a + 1]
    if bad:
        raise RuntimeError(
            f"分段不完整 {len(bad)}/{len(plan)}（保留 part 文件，重跑即续传）：{bad[:3]}")

    # 合并必须原子。直接 append 到 out_path 的话，合并中途被杀会留下
    # 「长度不足、但下一次会被当成可信前缀」的主文件——拼出来的东西长度对、
    # 内容全错，而且没有任何一步能发现。所以写临时文件再 os.replace。
    merge_tmp = out_path + ".merging"
    if os.path.exists(merge_tmp):
        os.remove(merge_tmp)
    with open(merge_tmp, "wb") as out:
        if have:                                   # 先复刻已有前缀，原文件保持不动
            with open(out_path, "rb") as f:
                shutil.copyfileobj(f, out, 4 * 1024 * 1024)
        for i, _a, _b in plan:                      # plan 按 i 升序 = 字节序
            with open(parts[i], "rb") as f:
                shutil.copyfileobj(f, out, 4 * 1024 * 1024)
    got = os.path.getsize(merge_tmp)
    if got != total:
        os.remove(merge_tmp)
        raise RuntimeError(f"合并后字节数不符：{got} != {total}（part 已保留，重跑可复用）")
    os.replace(merge_tmp, out_path)                 # 同分区原子替换：主文件不会停在半成品
    for p in parts:
        os.remove(p)
    return total, round(time.time() - t0, 1), host


def pick_audio(dash):
    auds = sorted(dash.get("audio") or [], key=lambda x: -x.get("bandwidth", 0))
    if not auds:
        raise RuntimeError("该视频没有独立音轨（可能不是 dash）")
    return auds[0]


def pick_video(dash, want):
    vids = dash.get("video") or []
    if not vids:
        raise RuntimeError("没有视频流")
    # 优先 avc1：av1 需要 libdav1d，部分 ffmpeg 构建没编进去
    cand = [v for v in vids if v.get("id") == want or v.get("height") == want]
    if not cand:
        heights = sorted({v.get("height") for v in vids})
        raise RuntimeError(f"没有 {want}P，可选：{heights}")
    cand.sort(key=lambda v: (0 if v["codecs"].startswith("avc1") else 1,
                             -v.get("bandwidth", 0)))
    return cand[0]


def main():
    ap = argparse.ArgumentParser(description="B站 dash 音视频流下载（分块 + 校验）")
    ap.add_argument("bvid")
    ap.add_argument("--audio", action="store_true", help="下载音轨（转写用）")
    ap.add_argument("--video", type=int, default=0, help="视频清晰度高度，如 1080；读表格文字必须 ≥1080")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--page", type=int, default=1, help="分P，默认 1")
    ap.add_argument("--jobs", type=int, default=JOBS,
                    help=f"并行连接数，默认 {JOBS}；网络差时加到 16")
    ap.add_argument("--chunk", type=int, default=CHUNK // 1024,
                    help=f"子块大小（KB），默认 {CHUNK//1024}。"
                         "子块太小会被 HTTP 往返开销拖死（256 KB 单连接只有 1 MB 的 1/3 速度）；"
                         "网络频繁截断时降到 256 影响也不大，因为 partial 会逐段推进")
    ap.add_argument("--force", action="store_true",
                    help="忽略已存在的同名文件，从头下载（怀疑旧文件损坏时用）")
    args = ap.parse_args()

    global REFERER
    bvid = fv.parse_bvid(args.bvid)
    REFERER = f"https://www.bilibili.com/video/{bvid}/"
    cookie = _cookie()
    os.makedirs(args.out, exist_ok=True)

    st, info, err = fv._fetch(f"/x/web-interface/view?bvid={bvid}", cookie=cookie)
    if st != 200 or not info or info.get("code") != 0:
        raise SystemExit(f"取视频信息失败：{err or (info or {}).get('message')}")
    pages = info["data"]["pages"]
    cid = pages[args.page - 1]["cid"]

    data = playurl(bvid, cid, cookie)
    dash = data.get("dash") or {}
    if not dash:
        raise SystemExit("没有 dash 流（可能是老视频/番剧），请改用 durl 方案")

    if not (args.audio or args.video):
        args.audio = True  # 默认给最小的那个

    jobs = []
    if args.audio:
        a = pick_audio(dash)
        jobs.append(("audio", a, os.path.join(args.out, "audio.m4s")))
    if args.video:
        v = pick_video(dash, args.video)
        jobs.append((f"video{v['height']}",
                     v, os.path.join(args.out, f"video{v['height']}.m4s")))

    for name, stream, path in jobs:
        urls = [stream["baseUrl"]] + list(stream.get("backupUrl") or [])
        total, secs, host = download(urls, cookie, path,
                                     jobs=args.jobs, chunk=args.chunk * 1024,
                                     force=args.force)
        mbs = total / 1048576 / max(secs, 0.1)
        print(f"[OK] {name:10s} {v_codec(stream):12s} "
              f"{total/1048576:6.1f} MB  {secs:4.1f}s  {mbs:5.1f} MB/s  "
              f"{os.path.basename(path)}  ({host})")

    print("\n下一步：")
    print(f"  ffprobe {jobs[0][2]}          # 确认时长与编码")
    vids = [p for n, _s, p in jobs if n.startswith("video")]
    if vids:
        print(f"  python scripts/grab_frames.py \"{vids[0]}\" --out frames")


def v_codec(s):
    c = s.get("codecs", "?").split(".")[0]
    if s.get("height"):
        return f"{c} {s['height']}P"
    return f"{c} {s.get('bandwidth', 0)//1000}k 音频"


if __name__ == "__main__":
    main()
