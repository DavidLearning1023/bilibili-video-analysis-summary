#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站视频分析 —— 前置条件自检

检查四件事：
  1. `bilibili-mcp` 是否安装在 npx 缓存中，版本是多少
  2. 三处关键补丁是否已打（412 风控 / 评论游标分页 / limit 上限）
  3. 凭据是否已配置（~/.bilibili-mcp/config.json）
  4. 本脚本自身的网络连通性（nav 接口）

输出 [OK] / [WARN] / [FAIL] 三级，FAIL 项需要处理后再用 MCP 通道。

用法：
    python check_setup.py
"""

import json
import pathlib
import re
import sys
import urllib.request

PKG = "@xzxzzx/bilibili-mcp"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

fails = []
warns = []


def ok(msg):
    print(f"[OK]   {msg}")


def warn(msg):
    print(f"[WARN] {msg}")
    warns.append(msg)


def fail(msg):
    print(f"[FAIL] {msg}")
    fails.append(msg)


def find_package():
    roots = [pathlib.Path.home() / "AppData/Local/npm-cache/_npx",
             pathlib.Path.home() / ".npm/_npx"]
    for root in roots:
        if not root.exists():
            continue
        for c in root.glob(f"*/node_modules/{PKG}"):
            if (c / "package.json").exists():
                return c
    return None


def main():
    print("=" * 62)
    print("B站视频分析 —— 前置条件自检")
    print("=" * 62)

    # 1. 找包
    pkg = find_package()
    if not pkg:
        fail(f"未找到已安装的 {PKG}。安装方式：npx -y {PKG}@latest setup")
        print()
        print("提示：即使不装 MCP，也可以直接用本 skill 的 scripts/fetch_video.py 取数。")
        return 1
    ver = json.loads((pkg / "package.json").read_text(encoding="utf-8")).get("version", "?")
    ok(f"已找到 {PKG}@{ver}")
    print(f"       目录：{pkg}")

    # 2. 补丁检查
    dist = pkg / "dist"
    checks = [
        ("412 风控补丁（getVideoInfo 走 WBI）",
         dist / "bilibili/video-api.js",
         r"/x/web-interface/wbi/view"),
        ("评论游标分页补丁（cursor.next 翻页）",
         dist / "bilibili/comments.js",
         r"cursor"),
        ("评论 limit 上限放宽（50 → 300）",
         dist / "utils/validation.js",
         r"300"),
    ]
    for label, path, pattern in checks:
        if not path.exists():
            fail(f"{label}：文件缺失 {path.name}")
            continue
        if re.search(pattern, path.read_text(encoding="utf-8", errors="ignore")):
            ok(f"{label}：已应用")
        else:
            fail(f"{label}：**未应用** —— 见 references/troubleshooting.md")

    # 3. 凭据
    cred = pathlib.Path.home() / ".bilibili-mcp" / "config.json"
    if cred.exists():
        try:
            d = json.loads(cred.read_text(encoding="utf-8"))
            has = bool(d.get("sessdata"))
            if has:
                exp = d.get("expiresAt")
                extra = ""
                if isinstance(exp, (int, float)):
                    import datetime
                    ts = exp / 1000 if exp > 1e11 else exp
                    dt = datetime.datetime.fromtimestamp(ts)
                    extra = f"，有效期至 {dt:%Y-%m-%d}"
                    if dt < datetime.datetime.now():
                        warn(f"凭据已过期（{dt:%Y-%m-%d}），需重跑 setup 重新扫码")
                ok(f"凭据已配置{extra}")
            else:
                fail("凭据文件存在但缺少 sessdata，请重跑 setup")
        except Exception as e:
            fail(f"凭据文件解析失败：{e}")
    else:
        warn("未找到凭据文件 —— 元数据与评论仍可用，但**取字幕会看不到内容**")
        print("       配置方式：npx -y @xzxzzx/bilibili-mcp@<版本> setup")

    # 4. 网络
    try:
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav",
                                     headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                ok("B站 API 连通（nav 接口 200）")
            else:
                warn(f"B站 API 返回 HTTP {r.status}")
    except Exception as e:
        warn(f"B站 API 连通性检查失败：{e}")

    print()
    print("=" * 62)
    if fails:
        print(f"结论：{len(fails)} 项需要处理（标 [FAIL]），处理后再用 MCP 通道。")
        print("      也可以直接用 scripts/fetch_video.py（不依赖 MCP 补丁）。")
        return 1
    if warns:
        print(f"结论：可用，但有 {len(warns)} 项提示（标 [WARN]）。")
        return 0
    print("结论：全部就绪 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
