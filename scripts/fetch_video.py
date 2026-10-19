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
    """从各种输入里取 BV 号：裸 BV 号 / 完整 URL / **b23.tv 短链**。

    ⚠️ 短链需要跟随一次重定向才能拿到 BV 号。SKILL 的触发边界写着支持 b23.tv，
    但早期实现只做正则匹配，短链会直接失败——文档承诺了、代码没做，属于必须补的洞。
    注意：这段**只用构造的短链格式做了逻辑核对，没有真实 b23.tv 样本跑过**，
    首次遇到短链时请确认 `r.geturl()` 是否已跳转到 `/video/BV...`。
    """
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if m:
        return m.group(1)
    if re.search(r"https?://(b23\.tv|bili2233\.cn)/", raw):
        try:
            req = urllib.request.Request(raw, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                final = r.geturl()
            m = re.search(r"(BV[0-9A-Za-z]{10})", final)
            if m:
                return m.group(1)
        except Exception as e:
            raise ValueError(f"短链解析失败（{e}）：{raw}；请改用完整 URL 或裸 BV 号")
    raise ValueError(f"无法从输入中解析 BV 号：{raw}")


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
    owner = v.get("owner") or {}
    return {
        "bvid": v.get("bvid"),
        "aid": v.get("aid"),
        "title": v.get("title"),
        "author": owner.get("name"),
        # mid 是 UP 主的唯一数字身份。注意：**它不能用来反查弹幕发送者**——
        # 网上传的「弹幕 p[6] = crc32(mid)」已实测否证（见 troubleshooting §6.7 ④）。
        # 保留它是因为后续若要按 mid 匹配评论区作者（comment.member.mid）仍然需要。
        "author_mid": owner.get("mid"),
        "duration": v.get("duration"),
        "pubdate": v.get("pubdate"),
        "desc": v.get("desc"),
        "pic": v.get("pic"),
        "tname": v.get("tname") or v.get("tname_v2") or None,
        "stats": v.get("stat"),
        "pages": [{"page": p.get("page"), "cid": p.get("cid"), "title": p.get("part"),
                   "duration": p.get("duration")} for p in (v.get("pages") or [])],
        "tags": _get_tags(bvid, cookie),
    }


def _get_tags(bvid, cookie=None):
    """视频标签（分区之外的第二层分类）。取不到就返回 []，不让它拖垮主流程。

    ⚠️ 2026-10-19 实测的顺序问题：
      * `/x/tag/archive/tags` → 200 / **2 KB** / data 是 **list**（不是 dict）
      * `/x/web-interface/view/detail` → **412**（需要更严的签名，当前环境必失败）
    所以必须**轻接口优先、detail 只作兜底**。之前反着写，每次取数都白打一次 412：
    既慢，又平白增加一次风控暴露。
    另：data 是 list 时用 `.get()` 会抛 AttributeError，会被 except 吞掉变成静默返回 []——
    这类"异常被吞"最难查，所以这里显式判类型。
    """
    for path in ("/x/tag/archive/tags", "/x/web-interface/view/detail"):
        try:
            st, d, _ = _fetch(path, {"bvid": bvid}, cookie)
            if st != 200 or not d or d.get("code") != 0:
                continue
            data = d.get("data")
            if isinstance(data, list):
                tags = data
            elif isinstance(data, dict):
                tags = data.get("Tags") or data.get("tags") or []
            else:
                continue
            out = []
            for t in tags:
                if not isinstance(t, dict):
                    continue
                name = t.get("tag_name") or t.get("name")
                if name:
                    out.append(name)
            if out:
                return out
        except Exception:
            continue
    return []


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
    """游标分页 + rpid 去重。

    ⚠️ limit 的语义（2026-10-19 修正）：**limit 是顶层评论条数**，楼中楼是附加的，
    不占名额。旧实现按「总条数」截断，导致 `--comments 120` 实际只拿到 45 条顶层
    （75 条楼中楼吃掉 62.5% 的配额），三轴分布与「图片占总赞」的口径都被稀释。
    现在返回 length 可能 > limit，但顶层恒 <= limit。
    """
    path = "/x/v2/reply/wbi/main"
    use_wbi = True
    mode = 3 if sort == "hot" else 2
    out, seen, cursor = [], set(), None
    # 每页 ps=30，但上游偶尔只回 28–29 条；按"每页最少 20 条顶层"估上限，
    # 保证 `--comments 300` 也拿得满。max_pages 只是安全阀，正常会在 top_count 达标时提前 break。
    max_pages = (limit // 20) + 5
    top_count = 0
    for _ in range(max_pages):
        if top_count >= limit:
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
            """展平主评论与其楼中楼回复

            2026-10-19 修：原实现只取 content.message，会把「纯图片评论」整条丢掉
            （message 为空 ⇒ `if msg` 为假），并把 [doge] 这类表情占位符清洗成空串。
            实测缇宝那期 178 条里 87 条带图、占样本赞量 96.8%，最高赞 21,055 就是一张图，
            全部被降级成空字符串。现在保留 pictures / emote / level / location / ctime，
            并允许「有图无文」的评论进入样本。
            """
            if not isinstance(node, dict):
                return
            member = node.get("member") or {}
            content = node.get("content") or {}
            msg = (content.get("message") or "").strip()
            rpid = node.get("rpid")

            pics = []
            for p in (content.get("pictures") or []):
                if not isinstance(p, dict):
                    continue
                src = p.get("img_src") or p.get("img_url")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                pics.append({
                    "url": src,
                    "w": p.get("img_width"),
                    "h": p.get("img_height"),
                    "size": p.get("img_size"),
                })
            emotes = [k for k in (content.get("emote") or {}) if isinstance(k, str)]

            added = False
            if rpid is not None and rpid not in seen and (msg or pics or emotes):
                seen.add(rpid)
                added = True
                rc = node.get("reply_control") or {}
                loc = (rc.get("location") or "").replace("IP属地：", "").replace("IP属地:", "").strip()
                li = member.get("level_info") or {}
                # 清洗规则：**中英文表情占位符一律从正文里去掉**，表情名单独存进 emotes。
                # 旧正则 `\[[a-zA-Z0-9_]+\]` 只认英文，`[抓狂]`/`[傲娇]` 这类中文表情会留在正文里，
                # 与 emotes 字段重复，也让「清洗后为空 = 纯表情」的判断失效。
                # B站表情格式固定为 `[名称]`（≤16 字、无空格），正文里极少出现同形方括号。
                clean_text = re.sub(r"\[[^\[\]\n]{1,16}\]", "", msg).strip()
                kind = "picture" if pics else ("emote" if emotes else "text")
                out.append({
                    "rpid": rpid,
                    "author": member.get("uname") or "匿名用户",
                    "likes": node.get("like") or 0,
                    # rcount = 该楼的楼中楼回复总数（顶层评论才有）。
                    # 观点分布的「争议度」轴要靠它——点赞是"认同"、回复是"争议"，
                    # 是两种不同的信号，不能互相替代。楼中楼自身 rcount 恒为 0。
                    "rcount": node.get("rcount") or 0,
                    "is_reply": is_reply,
                    "content": clean_text,
                    # kind 让下游可以分开统计：picture / emote / text。
                    # 纯表情评论以前会因为正文非空而混进"有效文本"，现在能一眼分开。
                    "kind": kind,
                    # 下面五项是 2026-10-19 补的：原来全被归一化掉了
                    "pictures": pics,
                    "emotes": emotes,
                    "level": li.get("current_level"),
                    "location": loc or None,
                    "ctime": node.get("ctime"),
                    "is_senior": member.get("is_senior_member") or 0,
                })
            if not is_reply:
                replies = node.get("replies") or []
                if isinstance(replies, list):
                    for rp in replies[:3]:
                        walk(rp, True)
            # 必须返回"这一次是否真的新增了顶层"，不能只看 is_reply：
            # 游标翻页偶有重叠，去重跳过的条目若仍计数，top_count 会虚增、提前 break 少拿。
            return added and (not is_reply)

        data = d.get("data") or {}
        for rep in (data.get("replies") or []):
            if top_count >= limit:
                break
            if walk(rep):
                top_count += 1
        cur = data.get("cursor") or {}
        cursor = cur.get("next")
        if not cursor or cur.get("is_end"):
            break
    return out, ((d.get("data") or {}).get("cursor") or {}).get("all_count")


def fmt_ts(sec):
    if sec is None:
        return "??:??"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def comment_stats(cmts):
    """评论区的便宜指标——不读图就能算，用于决定「要不要读图」。

    picture_like_share 是关键判据：实测缇宝 57.0% 的条数占 96.8% 的赞，
    巫师只有 5.0% 的条数却占 28.3% 的赞（必须看），社保 7.0% 只占 0.8%（可跳过）。
    所以判据用「图片评论占总赞」，不用「含图条数占比」。

    ⚠️ 两条口径纪律（2026-10-19 实测，别踩）：

    1. **一律按顶层评论算**。楼中楼每楼只取 3 条（截断），样本有偏，
       混进来会把图片占比稀释、把等级分布压平。

    2. **判据固定按「前 100 条顶层」计算，与实际 `--comments` 解耦**。
       同一条视频实测（社保那期）：取 50/100/200/300 条顶层时，
       含图条数恒为 7，但「图片占赞」分别是 1.4% / 0.8% / 0.7% / 0.6%——
       **随样本深度单调下降**，因为分子不变、分母随长尾膨胀。
       若不固定深度，同一个视频会因采样量不同而判出 must / skip 两种结果。
       阈值 15% 是在 100 条深度下标定的，所以判据也必须在 100 条深度上算。
    """
    if not cmts:
        return {}
    top = [c for c in cmts if not c.get("is_reply")]
    alltop = top or cmts          # 极端情况下全是楼中楼，退回全集

    # ↓ 判据专用：固定前 100 条（按热度已排序），不随采样量漂移
    base = alltop[:100]
    total_like = sum(c["likes"] for c in base) or 1
    with_pic = [c for c in base if c.get("pictures")]
    pic_like = sum(c["likes"] for c in with_pic)
    pure_pic = [c for c in with_pic if not c["content"]]
    pure_emo = [c for c in base if c.get("kind") == "emote"]

    # ↓ 描述统计用全样本，样本越大越稳
    lv_count, lv_like = {}, {}
    body_like = sum(c["likes"] for c in alltop) or 1
    for c in alltop:
        lv = c.get("level") or 0
        lv_count[lv] = lv_count.get(lv, 0) + 1
        lv_like[lv] = lv_like.get(lv, 0) + c["likes"]
    return {
        "n_top": len(alltop),
        "n_replies": len(cmts) - len(top),
        "judge_depth": len(base),      # 判据实际用的深度（≤100）
        "total_likes": total_like,
        "with_picture": len(with_pic),
        "picture_share": round(len(with_pic) / len(base) * 100, 1),
        "picture_like_share": round(pic_like / total_like * 100, 1),
        "pure_picture": len(pure_pic),
        "pure_picture_like_share": round(sum(c["likes"] for c in pure_pic) / total_like * 100, 1),
        # 纯表情（无图无文）——这类评论对正文分析零贡献，但不该被静默丢掉，
        # 它的条数/赞量是「表演场」形态的直接证据（缇宝那期 12% 的条数）。
        "pure_emote": len(pure_emo),
        "pure_emote_like_share": round(sum(c["likes"] for c in pure_emo) / total_like * 100, 1),
        # 评论区形态判别要用（见 video-types.md 三·五）。两个都从顶层样本算：
        #   reply_intensity = 平均每层有多少条楼中楼 → 争论强度（用 rcount 真值，不用抓到的截断条数）
        #   reply_to_like   = 楼中楼总数 ÷ 赞总数（%）→ 评论活跃度，对应「评论/赞 >15%」这个信号
        "reply_intensity": round(sum(c.get("rcount") or 0 for c in alltop) / max(len(alltop), 1), 2),
        "reply_to_like": round(sum(c.get("rcount") or 0 for c in alltop)
                                / max(sum(c["likes"] for c in alltop), 1) * 100, 1),
        "picture_verdict": ("must" if pic_like / total_like >= 0.15
                            else "sample" if pic_like / total_like >= 0.05
                            else "skip"),
        "level_count": dict(sorted(lv_count.items(), reverse=True)),
        "level_like_share": {k: round(v / body_like * 100, 1)
                             for k, v in sorted(lv_like.items(), reverse=True)},
        "locations": _top_locations(alltop),
        "senior_members": sum(1 for c in alltop if c.get("is_senior")),
    }


def _top_locations(cmts, n=8):
    from collections import Counter
    c = Counter(x["location"] for x in cmts if x.get("location"))
    return dict(c.most_common(n))


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

    result = {"info": info, "transcript": None, "comments": [], "comments_total": None,
              "comments_meta": None}

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
        st = comment_stats(cmts)
        result["comments_meta"] = st
        print(f"[OK] 评论：{len(cmts)} 条去重（全站 {total}）", file=sys.stderr)
        if st:
            # 分子分母都用判据深度（≤100），不要混用 n_top——否则会打印出 "7/300（7.0%）" 这种自相矛盾
            print(f"[OK] 顶层 {st['n_top']} 条 + 楼中楼 {st['n_replies']} 条；"
                  f"按前 {st['judge_depth']} 条算，图片评论 {st['with_picture']} 条"
                  f"（{st['picture_share']}%），占样本赞量 {st['picture_like_share']}%"
                  f" ⇒ 判读：{st['picture_verdict']}",
                  file=sys.stderr)
            print(f"[OK] 等级分布（条数）：{st['level_count']}　（赞占比）：{st['level_like_share']}",
                  file=sys.stderr)
            if st["picture_verdict"] != "skip":
                print(f"[HINT] 建议接着跑：python scripts/fetch_comment_media.py {bvid} "
                      f"--comments {args.comments}", file=sys.stderr)

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
            st = result.get("comments_meta") or {}
            if st:
                md += [f"> 顶层 {st['n_top']} 条 + 楼中楼 {st['n_replies']} 条；"
                       f"按前 {st['judge_depth']} 条算，含图 {st['with_picture']} 条"
                       f"（{st['picture_share']}%），占样本赞量 {st['picture_like_share']}%；"
                       f"等级分布 {st['level_count']}；IP 属地 {st['locations']}", ""]
            for c in sorted(result["comments"], key=lambda x: -x["likes"]):
                tag = "（楼中楼）" if c["is_reply"] else ""
                pics = f"[图×{len(c['pictures'])}] " if c.get("pictures") else ""
                if c["content"]:
                    body = c["content"]
                elif c.get("kind") == "picture":
                    body = "（纯图片，无文字）"
                elif c.get("kind") == "emote":
                    body = f"（仅表情：{' '.join(c.get('emotes') or [])}）"
                else:
                    body = "（空）"
                md += [f"- **{c['likes']}赞** {c['author']}{tag}：{pics}{body}"]
        md_path = outdir / f"{bvid}.md"
        md_path.write_text("\n".join(md), encoding="utf-8")
        print(f"[OK] 已写入 {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
