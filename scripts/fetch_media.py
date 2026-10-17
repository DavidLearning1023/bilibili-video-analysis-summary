#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 B站视频/音频流（dash），一次做对。

为什么需要它：B站 dash 流的 CDN 有三道坎，每次现场拼 urllib 都要重踩一遍——
  ① 不带 `Referer: https://www.bilibili.com/video/BVxxx/` + 浏览器 UA → HTTP 567
     （yt-dlp 直接下同样 567，别浪费时间试它）
  ② 大文件单次请求会被静默截断（实测 1080p 声明 46.3 MB，只给 5.7 MB，EOF 正常返回）
  ③ 截断的 m4s 仍能被 ffprobe 读出时长，直到 ffmpeg 解码才报
     "Invalid NAL unit size"，报错位置离病因很远
所以正确顺序是：先 Range 探测真实总长 → 再按块下载 → 最后校验字节数。

用法：
  python scripts/fetch_media.py BV11fJX6GEcL --audio            # 只要音轨（最小，转写用）
  python scripts/fetch_media.py BV11fJX6GEcL --video 1080       # 要读画面时必须 ≥1080
  python scripts/fetch_media.py BV11fJX6GEcL --audio --video 1080 --out ./media

只依赖标准库 + 同目录的 fetch_video.py（复用其 WBI 签名与凭据发现）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fetch_video as fv  # noqa: E402  （复用 wbi_fetch / load_cookie / UA）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHUNK = 4 * 1024 * 1024          # 4 MB —— 实测 2 MB 太碎、8 MB 偶发截断
MAX_RETRY = 4
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


def probe_size(url, cookie):
    """Range: bytes=0-1023 → 读 Content-Range 的总长。这是块下载的前提。"""
    r = urllib.request.urlopen(
        urllib.request.Request(url, headers=_headers(cookie, {"Range": "bytes=0-1023"})),
        timeout=60,
    )
    cr = r.headers.get("Content-Range", "")
    r.close()
    if "/" not in cr:
        raise RuntimeError(f"服务器未返回 Content-Range（{cr!r}），无法确定总长")
    return int(cr.rsplit("/", 1)[1])


def download(urls, cookie, out_path):
    """按 4 MB 分块下载并拼接。urls 是 [baseUrl] + backupUrl，逐个换源重试。"""
    last = None
    for url in urls:
        host = url.split("/")[2]
        try:
            total = probe_size(url, cookie)
            got = 0
            t0 = time.time()
            with open(out_path, "wb") as f:
                start = 0
                while start < total:
                    end = min(start + CHUNK - 1, total - 1)
                    data = None
                    for attempt in range(MAX_RETRY):
                        try:
                            with urllib.request.urlopen(
                                urllib.request.Request(
                                    url, headers=_headers(cookie, {"Range": f"bytes={start}-{end}"})),
                                timeout=90,
                            ) as r:
                                data = r.read()
                            break
                        except Exception as e:  # noqa: BLE001
                            last = e
                            time.sleep(1.2 * (attempt + 1))
                    if data is None:
                        raise RuntimeError(f"块 {start}-{end} 连续 {MAX_RETRY} 次失败：{last}")
                    f.write(data)
                    got += len(data)
                    start = end + 1
            if got != total:
                raise RuntimeError(f"字节数不符：{got} != {total}")
            return total, round(time.time() - t0, 1), host
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 源 {host} 失败：{e}", file=sys.stderr)
            last = e
            if os.path.exists(out_path):
                os.remove(out_path)
            continue
    raise RuntimeError(f"所有下载源都失败：{last}")


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
        total, secs, host = download(urls, cookie, path)
        print(f"[OK] {name:10s} {v_codec(stream):12s} "
              f"{total/1048576:6.1f} MB  {secs:4.1f}s  {os.path.basename(path)}  ({host})")

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
