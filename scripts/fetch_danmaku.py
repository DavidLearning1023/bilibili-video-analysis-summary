#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弹幕：被完全忽略的第三条通道

为什么值得单独做一个脚本
------------------------
评论告诉你"他们事后怎么想"，弹幕告诉你"他们当场在哪里炸了"。三个结构性优势：

  1. **秒级对齐**：每条弹幕带视频内时间戳，可直接对上口播段落；
     评论只有 ctime（发表时间），与视频内容没有对应关系。
  2. **全量**：一条 25 分钟视频能拿到 2,228 条，而评论样本通常只取 100–300 条。
  3. **反应纯度高**：是"看片那一刻"的反应，未经事后编辑，也不受热度排序影响。

最实用的一条：**广告披露常常藏在弹幕里**。实测《刹车断裂事件》那期，简介为空、
无商单标注，但第一条弹幕（t=635.2s）就是「本期推广西昊电竞椅」。
同一期 t=779.5s 还有「指正，中国汽研和中汽研是两个机构」——观众自己做出了纠偏，
而且是精确到秒的，评论区里没有这个信息。

只做三件事（不做全量 NLP）
--------------------------
  ① 秒级密度曲线 → 观众最在意哪一段
  ② 高重复句      → 集体口号 / 共识（"前方高能""爷青回"）
  ③ 披露类弹幕    → 广告披露、作者补充（**内容启发式，不是身份验证**）

⚠️ 一条否证（别走回头路）：网上传「p[6] = crc32(uid)，可反查 UP 主」，实测不成立。
   《刹车断裂事件》UP 主 mid=278761367，crc32=9a4fc7ef；而那条确定是作者自述的
   「本期推广西昊电竞椅」哈希是 0db460b5。又试了取反、int 4/8 字节、md5、sha1 五种变体，
   全部对不上；protobuf 的 seg.so 里也搜不到 varint(mid)。
   所以脚本不承诺「识别 UP 主」，只输出「疑似」，并标出来源。

用法
----
    python scripts/fetch_danmaku.py BV1dmJi6yEAi
    python scripts/fetch_danmaku.py BV1dmJi6yEAi --json ./out/BV.json   # 复用已取的元数据
    python scripts/fetch_danmaku.py BV1dmJi6yEAi --top 8 --min-repeat 2
    python scripts/fetch_danmaku.py BV1dmJi6yEAi --out ./m --save-json

输出全部走 stdout（紧凑文本），模型直接读，不必再读文件。

接口
----
    https://api.bilibili.com/x/v1/dm/list.so?oid={cid}
返回 XML，无需登录。`<d p="t,mode,size,color,ctime,pool,uid_hash,dmid,...">文本</d>`
  * p[0] 视频内秒数  p[5] 弹幕池  p[6] = crc32(uid) 的十六进制（**7 或 8 位，比较时补零**）
"""

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _bvid_of(s):
    if "bilibili.com" in s:
        m = re.search(r"BV[0-9A-Za-z]{10}", s)
        if m:
            return m.group(0)
    return s.strip()


def load_meta(bvid, args):
    if args.json:
        d = json.load(open(args.json, encoding="utf-8"))
        return d.get("info") or {}
    import fetch_video as fv
    cookie = fv.load_cookie(args) if hasattr(fv, "load_cookie") else None
    return fv.get_info(bvid, cookie)


def fetch_xml(cid, bvid):
    """拉弹幕 XML。

    ⚠️ 坑（2026-10-19 实测）：CDN 返回 `Content-Encoding: deflate`，但实际是
    **raw deflate（无 zlib 头）**，用 zlib.decompress() 会报 "incorrect header check"，
    必须用 `zlib.decompress(data, -zlib.MAX_WBITS)`。
    且无论请求头怎么声明，服务端都返回同一份压缩内容（连 `Accept-Encoding: identity` 也一样）。
    curl 拿到的字节完全相同——所以这不是 urllib 的问题。
    """
    url = "https://api.bilibili.com/x/v1/dm/list.so?" + urllib.parse.urlencode({"oid": cid})
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read()

    if raw[:1] == b"<":            # 已经是明文
        return raw
    if raw[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(raw)
    import zlib as _z
    for wbits in (-_z.MAX_WBITS, _z.MAX_WBITS, _z.MAX_WBITS | 32):
        try:
            out = _z.decompress(raw, wbits)
            if out[:1] == b"<":
                return out
        except Exception:
            continue
    return raw


def parse(raw):
    root = ET.fromstring(raw)
    out = []
    for d in root.findall("d"):
        p = (d.get("p") or "").split(",")
        if len(p) < 8:
            continue
        try:
            t = float(p[0])
        except ValueError:
            continue
        out.append({
            "t": t,
            "mode": p[1],
            "pool": p[5],
            "uhash": (p[6] or "").lower().zfill(8),
            "text": (d.text or "").strip(),
        })
    return [x for x in out if x["text"]]


def fmt_t(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bar(n, mx, width=40):
    if mx <= 0:
        return ""
    k = max(0, round(n / mx * width))
    return "#" * k


def main():
    ap = argparse.ArgumentParser(description="弹幕分析：密度曲线 / 高重复句 / UP主弹幕")
    ap.add_argument("video", help="BV 号或 URL")
    ap.add_argument("--json", help="复用 fetch_video.py 的 JSON（含 cid 与 author_mid）")
    ap.add_argument("--top", type=int, default=5, help="输出最密的几个分钟段，默认 5")
    ap.add_argument("--min-repeat", type=int, default=3, help="高重复句阈值，默认 3")
    ap.add_argument("--samples", type=int, default=6, help="每段抽样弹幕条数，默认 6")
    ap.add_argument("--out", default="", help="输出目录（配合 --save-json）")
    ap.add_argument("--save-json", action="store_true", help="把解析结果落盘")
    ap.add_argument("--xml", help="直接用本地已保存的弹幕 XML（离线调试 / 接口 412 时复用）")
    ap.add_argument("--cookie-file", help="凭据 JSON（仅取元数据时需要）")
    args = ap.parse_args()

    bvid = _bvid_of(args.video)
    info = load_meta(bvid, args)
    cid = None
    pages = info.get("pages") or []
    if pages:
        cid = pages[0].get("cid")
    if not cid:
        print("[FAIL] 取不到 cid（元数据缺 pages）", file=sys.stderr)
        return 2

    if args.xml:
        raw = pathlib.Path(args.xml).read_bytes()
        if raw[:2] == b"\x1f\x8b" or raw[:1] != b"<":
            import zlib as _z
            for wbits in (-_z.MAX_WBITS, _z.MAX_WBITS):
                try:
                    raw = _z.decompress(raw, wbits)
                    break
                except Exception:
                    continue
    else:
        try:
            raw = fetch_xml(cid, bvid)
        except urllib.error.HTTPError as e:
            if e.code == 412:
                print("[FAIL] 412 = B站风控限流（IP 级，换 cookie 也没用，curl 同样被拦）。\n"
                      "       等 5–10 分钟再跑；或先用浏览器打开一次视频页「暖机」。\n"
                      "       已有 XML 时可用 --xml 离线分析。", file=sys.stderr)
            else:
                print(f"[FAIL] 弹幕接口失败：{e}", file=sys.stderr)
            return 2
        except Exception as e:
            print(f"[FAIL] 弹幕接口失败：{e}", file=sys.stderr)
            return 2

    ds = parse(raw)
    if not ds:
        print("[WARN] 弹幕为空（可能被关闭或需要登录）", file=sys.stderr)
        return 0

    dur = info.get("duration") or 0
    stat = info.get("stats") or {}
    reply = stat.get("reply") or 0
    dm_total = stat.get("danmaku") or len(ds)

    print(f"[OK] {info.get('title', bvid)}")
    print(f"[OK] 弹幕 {len(ds)} 条（stat 报 {dm_total}）｜ 时长 {fmt_t(dur)}"
          f" ｜ 评论 {reply}")
    if dm_total:
        ratio = reply / dm_total if dm_total else 0
        # ⚠️ 不要给这个比值硬套因果。2026-10-19 实测：
        #   缇宝（18 秒）2.89:1，刹车（25:24）3.19:1 —— 时长差 85 倍，比值几乎一样。
        #   所以「评论多于弹幕 ⇒ 视频太短、弹幕来不及发」只对个别片子成立，不能当通用判据。
        print(f"[OK] 评论/弹幕 = {ratio:.2f}:1"
              f"（受题材、时长、UP 主弹幕设置共同影响，不要单独归因）")

    # ① 密度曲线（按分钟）
    buckets = Counter(int(d["t"] // 60) for d in ds)
    mx = max(buckets.values()) if buckets else 0
    print("\n--- 弹幕密度（每分钟）---")
    for m in range(max(buckets) + 1):
        n = buckets.get(m, 0)
        print(f"  {m:02d}  {n:>4}  {bar(n, mx)}")

    # ② 最密的分钟段 + 样本
    print(f"\n--- 最密集的 {args.top} 个分钟段 ---")
    for m, n in buckets.most_common(args.top):
        lo, hi = m * 60, m * 60 + 60
        seg = [d for d in ds if lo <= d["t"] < hi]
        seg.sort(key=lambda x: x["t"])
        span = f"{fmt_t(lo)}–{fmt_t(min(hi, dur or hi))}"
        print(f"\n  【{span}】{n} 条")
        # 段内均匀抽样，避免只看开头
        step = max(1, len(seg) // args.samples)
        for d in seg[::step][:args.samples]:
            print(f"    {fmt_t(d['t'])}  {d['text'][:60]}")

    # ③ 高重复句（集体口号）
    txt = [d["text"] for d in ds]
    rep = [(t, c) for t, c in Counter(txt).most_common(30) if c >= args.min_repeat]
    print(f"\n--- 高重复句（≥{args.min_repeat} 次）---")
    if rep:
        for t, c in rep[:15]:
            first = min((d["t"] for d in ds if d["text"] == t), default=0)
            print(f"  {c:>3}×  {t[:44]}   （首现 {fmt_t(first)}）")
    else:
        print("  （无）——说明这期没有形成集体口号")

    # ④ 披露关键词扫描
    #
    # ⚠️ 三条已放弃的路（2026-10-19 用户确认，别再试）：
    #   ① 「UP 主弹幕识别」—— 网上传 p[6] = crc32(uid)，实测不成立：
    #      《刹车断裂事件》UP 主 mid=278761367 → crc32 = 9a4fc7ef，
    #      而那条确定是作者自述的「本期推广西昊电竞椅」哈希是 0db460b5。
    #      又试 ^0xffffffff、int 4/8 字节、md5[:8]、sha1[:8] 五种变体全不匹配；
    #      protobuf 的 seg.so 里也搜不到 varint(mid)。B站只给哈希，且不是简单 crc32。
    #   ② 「置顶评论」—— top_replies 接口形态未打通，收益不值。
    #   ③ 「up_action」（UP 主是否点赞/回复某评论）—— 两个样本里恒为 0，不可用。
    #
    # 保留的只有**内容扫描**：扫"推广/赞助"这类词，回答"这条视频有没有广告"，
    # 不回答"这句话是谁说的"。用户自己看上下文就能判断语气。
    print("\n--- 披露关键词扫描（只找线索，不判断发言人）---")
    kw = ("本期", "推广", "赞助", "广告", "合作", "恰饭", "商务", "感谢")
    hits = [d for d in ds if any(k in d["text"] for k in kw) and len(d["text"]) <= 60]
    if hits:
        for d in sorted(hits, key=lambda x: x["t"])[:12]:
            print(f"  {fmt_t(d['t'])}  {d['text'][:70]}")
        print(f"  （共 {len(hits)} 条含披露关键词，{fmt_t(min(x['t'] for x in hits))} 起首次出现；"
              f"按句子的第一人称/第三人称自行判断是作者还是观众）")
    else:
        print("  （无）——未发现广告披露线索")

    if args.save_json:
        outdir = pathlib.Path(args.out or ".")
        outdir.mkdir(parents=True, exist_ok=True)
        p = outdir / f"{bvid}_danmaku.json"
        p.write_text(json.dumps({
            "bvid": bvid, "cid": cid, "n": len(ds),
            "reply": reply, "danmaku_stat": dm_total,
            "buckets": dict(sorted(buckets.items())),
            "items": ds,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[OK] 已写入 {p}")

    print("\n[用法] 密度曲线只回答「哪一段炸了」；把它对齐到口播段落，"
          "写进「延伸发散」与「评论区结构性偏差」两处，不单开一章。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
