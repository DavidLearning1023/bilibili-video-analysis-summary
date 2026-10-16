#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站视频取数工具（自包含，仅用 Python 标准库）

取三样东西，输出 JSON（可选同时输出 markdown 摘要）：
  1. 元数据  —— 标题 / UP主 / 时长 / 分P / 统计 / 简介
  2. 字幕    —— 优先人工 CC，其次 AI 字幕（需登录；带时间戳）
  3. 评论    —— 热度或时间排序，游标分页拉取，按 rpid 去重（含楼中楼）

设计要点（都是踩过坑之后写进去的）：
  * /x/web-interface/view 不带 WBI 签名时，在部分出口 IP（如境外线路）会被风控返回 412；
    带 WBI 签名的 /x/web-interface/wbi/view 正常。本脚本一律走 WBI。
  * 评论接口的 pn 页码参数已被 B站废弃（传了会被忽略，每次返回同一批数据 → 结果成倍重复）。
    必须用响应里的 cursor.next 做游标翻页，并按 rpid 去重。
  * 评论接口 ps 上限为 30。

用法：
    python fetch_video.py BV1r2ey6EEh5
    python fetch_video.py "https://www.bilibili.com/video/BV1r2ey6EEh5/" --comments 150
    python fetch_video.py BV1r2ey6EEh5 --comments 0          # 不取评论
    python fetch_video.py BV1r2ey6EEh5 --no-subtitle
    python fetch_video.py BV1r2ey6EEh5 --out ./out --markdown

凭据（仅取字幕需要；评论/元数据无需登录）：
    优先 --cookie-file，其次环境变量 BILI_SESSDATA，最后自动尝试
    ~/.bilibili-mcp/config.json（B站 MCP 的凭据文件，若存在）
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# WBI 签名用的固定重排表
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
             26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
             20, 34, 44, 52]

AI_LANGS = ["ai-zh", "ai-en", "ai-ja", "ai-ko", "ai-es", "ai-fr", "ai-de", "ai-pt", "ai-ar"]
PS_MAX = 30          # 上游单页上限（实测 30）
RATE_LIMIT_MS = 400  # 请求间隔，避免触发风控

_last_request = [0.0]
_mixin_key = [None]


def _throttle():
    now = time.time() * 1000
    wait = RATE_LIMIT_MS - (now - _last_request[0])
    if wait > 0:
        time.sleep(wait / 1000.0)
    _last_request[0] = time.time() * 1000


def _fetch(path, params=None, cookie=None, timeout=25):
    """返回 (http_status, parsed_json_or_None, error_text)"""
    _throttle()
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
               "Accept": "application/json, text/plain, */*",
               "Accept-Language": "zh-CN,zh;q=0.9", "Origin": "https://www.bilibili.com"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
            return r.status, json.loads(body), None
    except urllib.error.HTTPError as e:
        hint = "（HTTP 412 = 风控拦截；本脚本已用 WBI 签名，若仍 412 请稍后重试或降低频率）" if e.code == 412 else ""
        return e.code, None, f"HTTP {e.code} {hint}"
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


def wbi_key():
    if _mixin_key[0]:
        return _mixin_key[0]
    st, d, err = _fetch("/x/web-interface/nav")
    if st != 200 or not d:
        raise RuntimeError(f"无法获取 WBI 密钥：{err}")
    img = d["data"]["wbi_img"]
    raw = (img["img_url"].rsplit("/", 1)[-1].split(".")[0]
           + img["sub_url"].rsplit("/", 1)[-1].split(".")[0])
    _mixin_key[0] = "".join(raw[i] for i in MIXIN_TAB)[:32]
    return _mixin_key[0]


def wbi_fetch(path, params, cookie=None):
    """带 WBI 签名的请求"""
    mk = wbi_key()
    p = {k: v for k, v in params.items() if v is not None}
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode([
        (k, "".join(c for c in str(v) if c not in "!'()*")) for k, v in sorted(p.items())
    ])
    p["w_rid"] = hashlib.md5((q + mk).encode()).hexdigest()
    return _fetch(path, p, cookie)


def parse_bvid(raw):
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if not m:
        raise ValueError(f"无法从输入中解析 BV 号：{raw}")
    return m.group(1)


def load_cookie(args):
    """凭据来源：--cookie-file > 环境变量 > B站 MCP 的配置文件"""
    candidates = []
    if args.cookie_file:
        candidates.append((pathlib.Path(args.cookie_file), "命令行指定"))
    env = os.environ.get("BILI_SESSDATA")
    if env:
        parts = [s.strip() for s in env.split(";") if s.strip()]
        if all("=" not in s for s in parts):
            return "; ".join(f"{k}={v}" for k, v in zip(("SESSDATA", "bili_jct", "DedeUserID"), parts))
        return env
    candidates.append((pathlib.Path.home() / ".bilibili-mcp" / "config.json", "B站 MCP 配置"))
    for path, label in candidates:
        if not path.exists():
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sess = d.get("sessdata") or d.get("SESSDATA")
        if not sess:
            continue
        bits = [f"SESSDATA={sess}"]
        for key, name in (("bili_jct", "bili_jct"), ("dedeuserid", "DedeUserID")):
            if d.get(key):
                bits.append(f"{name}={d[key]}")
        print(f"[INFO] 使用凭据来源：{label}", file=sys.stderr)
        return "; ".join(bits)
    return None


def get_info(bvid, cookie=None):
    st, d, err = wbi_fetch("/x/web-interface/wbi/view", {"bvid": bvid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        st2, d2, err2 = _fetch("/x/web-interface/view", {"bvid": bvid}, cookie)
        if st2 != 200 or not d2 or d2.get("code") != 0:
            raise RuntimeError(f"获取视频信息失败：{err or err2 or (d or {}).get('message')}")
        d = d2
    v = d["data"]
    return {
        "bvid": v.get("bvid"),
        "aid": v.get("aid"),
        "title": v.get("title"),
        "author": (v.get("owner") or {}).get("name"),
        "duration": v.get("duration"),
        "pubdate": v.get("pubdate"),
        "desc": v.get("desc"),
        "stats": v.get("stat"),
        "pages": [{"page": p.get("page"), "cid": p.get("cid"), "title": p.get("part"),
                   "duration": p.get("duration")} for p in (v.get("pages") or [])],
    }


def get_transcript(bvid, cid, cookie=None, prefer="zh-Hans"):
    """返回 {data_source, language, lines:[{from,to,content}]} 或 None"""
    st, d, err = wbi_fetch("/x/player/wbi/v2", {"bvid": bvid, "cid": cid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        st, d, err = _fetch("/x/player/v2", {"bvid": bvid, "cid": cid}, cookie)
        if st != 200 or not d or d.get("code") != 0:
            return None
    subs = ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return None
    human = [s for s in subs if not str(s.get("lan", "")).startswith("ai-")]
    ai = [s for s in subs if str(s.get("lan", "")).startswith("ai-")]
    target = None
    for s in human:
        if str(s.get("lan", "")).startswith("zh"):
            target = s
            break
    if not target and human:
        target = human[0]
    source = "subtitle"
    if not target:
        for lang in AI_LANGS:
            for s in ai:
                if s.get("lan") == lang:
                    target = s
                    break
            if target:
                break
        source = "ai_subtitle"
    if not target:
        return None
    url = target.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=25) as r:
            body = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None
    lines = []
    for row in body.get("body") or []:
        txt = (row.get("content") or "").strip()
        if txt:
            lines.append({"from": row.get("from"), "to": row.get("to"), "content": txt})
    if not lines:
        return None
    return {"data_source": source, "language": target.get("lan"), "lines": lines}


def get_comments(oid, limit=100, sort="hot", cookie=None):
    """游标分页 + rpid 去重。返回 list[dict]"""
    path = "/x/v2/reply/wbi/main"
    use_wbi = True
    mode = 3 if sort == "hot" else 2
    out, seen, cursor = [], set(), None
    max_pages = (limit // PS_MAX) + 3
    for _ in range(max_pages):
        if len(out) >= limit:
            break
        params = {"oid": oid, "type": 1, "mode": mode, "ps": PS_MAX}
        if cursor:
            params["next"] = cursor
        if use_wbi:
            st, d, err = wbi_fetch(path, params, cookie)
            if st != 200 or not d or d.get("code") != 0:
                use_wbi = False
                st, d, err = _fetch("/x/v2/reply/main", params, cookie)
        else:
            st, d, err = _fetch("/x/v2/reply/main", params, cookie)
        if st != 200 or not d or d.get("code") != 0:
            break

        def walk(node, is_reply=False):
            """展平主评论与其楼中楼回复"""
            if not isinstance(node, dict):
                return
            member = node.get("member") or {}
            content = node.get("content") or {}
            msg = (content.get("message") or "").strip()
            rpid = node.get("rpid")
            if msg and rpid is not None and rpid not in seen:
                seen.add(rpid)
                out.append({
                    "rpid": rpid,
                    "author": member.get("uname") or "匿名用户",
                    "likes": node.get("like") or 0,
                    # rcount = 该楼的楼中楼回复总数（顶层评论才有）。
                    # 观点分布的「争议度」轴要靠它——点赞是"认同"、回复是"争议"，
                    # 是两种不同的信号，不能互相替代。楼中楼自身 rcount 恒为 0。
                    "rcount": node.get("rcount") or 0,
                    "is_reply": is_reply,
                    "content": re.sub(r"\[[a-zA-Z0-9_]+\]", "", msg).strip(),
                })
            if not is_reply:
                replies = node.get("replies") or []
                if isinstance(replies, list):
                    for rp in replies[:3]:
                        walk(rp, True)

        data = d.get("data") or {}
        for rep in (data.get("replies") or []):
            if len(out) >= limit:
                break
            walk(rep)
        cur = data.get("cursor") or {}
        cursor = cur.get("next")
        if not cursor or cur.get("is_end"):
            break
    return out[:limit], ((d.get("data") or {}).get("cursor") or {}).get("all_count")


def fmt_ts(sec):
    if sec is None:
        return "??:??"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def main():
    ap = argparse.ArgumentParser(description="B站视频取数（元数据 / 字幕 / 评论）")
    ap.add_argument("video", help="BV 号或完整 URL")
    ap.add_argument("--page", type=int, default=1, help="分P 编号，默认 1")
    ap.add_argument("--comments", type=int, default=100, help="评论条数上限，0 表示不取，默认 100")
    ap.add_argument("--sort", choices=["hot", "time"], default="hot", help="评论排序，默认 hot")
    ap.add_argument("--no-subtitle", action="store_true", help="不取字幕")
    ap.add_argument("--cookie-file", help="凭据 JSON 文件（含 sessdata）")
    ap.add_argument("--out", default=".", help="输出目录，默认当前目录")
    ap.add_argument("--markdown", action="store_true", help="同时输出 markdown 摘要")
    args = ap.parse_args()

    bvid = parse_bvid(args.video)
    cookie = load_cookie(args)

    print(f"[INFO] 视频 {bvid}", file=sys.stderr)
    info = get_info(bvid, cookie)
    print(f"[OK] {info['title']}  |  {info['author']}  |  {info['duration']}s  |  {len(info['pages'])}P",
          file=sys.stderr)

    result = {"info": info, "transcript": None, "comments": [], "comments_total": None}

    if not args.no_subtitle:
        idx = max(0, min(args.page - 1, len(info["pages"]) - 1))
        cid = info["pages"][idx]["cid"] if info["pages"] else None
        if cid:
            tr = get_transcript(bvid, cid, cookie)
            if tr:
                result["transcript"] = tr
                print(f"[OK] 字幕：{tr['language']} / {tr['data_source']} / {len(tr['lines'])} 行",
                      file=sys.stderr)
            else:
                print("[WARN] 无字幕（或未登录导致不可见）；如需转写请另配 ASR", file=sys.stderr)

    if args.comments > 0:
        cmts, total = get_comments(info["aid"], args.comments, args.sort, cookie)
        result["comments"] = cmts
        result["comments_total"] = total
        print(f"[OK] 评论：{len(cmts)} 条去重（全站 {total}）", file=sys.stderr)

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"{bvid}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 已写入 {json_path}", file=sys.stderr)

    if args.markdown:
        md = [f"# {info['title']}", "",
              f"- BV号：`{bvid}`　｜　UP主：{info['author']}　｜　时长：{fmt_ts(info['duration'])}",
              f"- 分P：{len(info['pages'])}　｜　播放 {info['stats'].get('view')}　点赞 {info['stats'].get('like')}"
              f"　收藏 {info['stats'].get('favorite')}　评论 {info['stats'].get('reply')}", ""]
        if info.get("desc"):
            md += ["## 简介", "", info["desc"].strip(), ""]
        tr = result["transcript"]
        if tr:
            md += [f"## 字幕（{tr['language']} / {tr['data_source']}）", ""]
            md += [f"[{fmt_ts(l['from'])}] {l['content']}" for l in tr["lines"]]
            md += [""]
        if result["comments"]:
            md += [f"## 评论（按{('热度' if args.sort == 'hot' else '时间')}，{len(result['comments'])} 条）", ""]
            for c in sorted(result["comments"], key=lambda x: -x["likes"]):
                tag = "（楼中楼）" if c["is_reply"] else ""
                md += [f"- **{c['likes']}赞** {c['author']}{tag}：{c['content']}"]
        md_path = outdir / f"{bvid}.md"
        md_path.write_text("\n".join(md), encoding="utf-8")
        print(f"[OK] 已写入 {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
