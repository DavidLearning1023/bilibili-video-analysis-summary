#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili-mcp @1.14.1 本地补丁工具

背景：@xzxzzx/bilibili-mcp@1.14.1 存在三处影响实际使用的问题（详见 README.md）。
本工具用「整文件替换」的方式打补丁，因此不依赖字符串匹配，不受压缩/格式差异影响。

用法：
    python apply-patch.py            # 打补丁（整文件覆盖）
    python apply-patch.py --check    # 只报告状态
    python apply-patch.py --revert   # 还原为官方原始文件

安全设计：
    * 版本守卫：只在包版本恰好为 1.14.1 时动手，版本不符直接拒绝
    * 状态自检：能识别「已打补丁 / 原始 / 未知」三种状态
    * 双向可逆：pristine/ 存官方原始文件，patched/ 存打好补丁的版本
"""

import hashlib
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent
INDEX = ROOT / "patch-index.json"
PRISTINE = ROOT / "pristine"
PATCHED = ROOT / "patched"


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_index() -> dict:
    if not INDEX.exists():
        print(f"[FAIL] 缺少 {INDEX.name}，请先运行 build-patch-index.py")
        raise SystemExit(2)
    return json.loads(INDEX.read_text(encoding="utf-8"))


def resolve_live_dir(idx: dict):
    """定位已安装的包目录：索引里记录的路径优先，失效则扫描 npx 缓存（跨平台）。

    注意：patch-index.json 里**不存** live_package_dir——每个用户机器的 npx
    缓存路径不同，写死会让别人的补丁流程直接失效。这里一律现查。
    """
    recorded = idx.get("live_package_dir")
    if recorded:
        live = pathlib.Path(recorded)
        if (live / "package.json").exists():
            return live
    for base in (pathlib.Path.home() / "AppData/Local/npm-cache/_npx",   # Windows
                 pathlib.Path.home() / ".npm/_npx"):                     # macOS / Linux
        if not base.exists():
            continue
        for c in base.glob("*/node_modules/@xzxzzx/bilibili-mcp"):
            if (c / "package.json").exists():
                return c
    return None


def check_version(live: pathlib.Path, expected: str) -> str:
    pj = live / "package.json"
    if not pj.exists():
        return "(package.json 缺失)"
    return json.loads(pj.read_text(encoding="utf-8")).get("version", "(未知)")


def main() -> int:
    idx = load_index()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    live = resolve_live_dir(idx)
    expected_ver = idx["version"]

    if not live:
        print(f"[FAIL] 未找到已安装的 {idx['package']}（npx 缓存里没有）。")
        print(f"       请先安装：npx -y {idx['package']}@{expected_ver} setup")
        return 2

    actual_ver = check_version(live, expected_ver)

    # ---- --check ----
    if mode == "--check":
        print(f"包目录：{live}")
        print(f"版本：{actual_ver}（补丁针对 {expected_ver}）")
        print()
        print(f"{'文件':40s} {'状态':10s} 大小")
        print("-" * 70)
        for rel, rec in idx["files"].items():
            cur = live / "dist" / rel
            if not cur.exists():
                print(f"{rel:40s} {'缺失':10s} -")
                continue
            h = sha256(cur)
            if h == rec.get("patched_sha256"):
                state = "已打补丁"
            elif h == rec.get("pristine_sha256"):
                state = "原始"
            else:
                state = "未知!"
            print(f"{rel:40s} {state:10s} {cur.stat().st_size} B")
        return 0

    # ---- 版本守卫 ----
    if actual_ver != expected_ver:
        print(f"[ABORT] 包版本为 {actual_ver}，补丁只针对 {expected_ver}。")
        print("        升级后原问题可能已被上游修复，请先确认再决定是否重新生成补丁。")
        return 3

    # ---- --revert ----
    if mode == "--revert":
        n = 0
        for rel in idx["files"]:
            src = PRISTINE / rel
            dst = live / "dist" / rel
            if src.exists():
                shutil.copy2(src, dst)
                n += 1
        print(f"[OK] 已从 pristine/ 还原 {n} 个文件为官方原始版本")
        print("     注意：还原后 412 问题与评论重复问题会复现")
        return 0

    # ---- 打补丁 ----
    n, skipped = 0, 0
    for rel, rec in idx["files"].items():
        src = PATCHED / rel
        dst = live / "dist" / rel
        if not src.exists():
            print(f"[SKIP] patched/ 中缺少 {rel}")
            skipped += 1
            continue
        if dst.exists() and sha256(dst) == rec.get("patched_sha256"):
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
        print(f"[OK] 已应用 {rel}")

    print()
    print(f"完成：应用 {n} 个，跳过 {skipped} 个（已是补丁状态）")
    if n:
        print("[注意]  需要重启 / 重连客户端，运行中的 MCP 进程才会加载新代码")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
