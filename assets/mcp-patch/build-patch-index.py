#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁工具链 —— 索引生成 / 刷新（上游包升级时用）

背景：apply-patch.py 依赖两样东西：
  1. pristine/   —— 官方原始文件快照（用于一键还原 + 状态识别）
  2. patched/    —— 打好补丁的文件（用于整文件替换）
  以及 patch-index.json 里每个文件的 sha256（用于「已打补丁 / 原始 / 未知」三种状态的判定）。
本脚本负责在"上游包升级"后刷新这两份快照与索引。

典型流程（升级到新版本 X.Y.Z 时，配合 maintenance.md 第四节）：
  1. python build-patch-index.py fetch X.Y.Z
     → 从 npm 下载该版本 tarball，把 5 个目标文件写入 pristine/
  2. [人工] 逐个对比新版源码，把三个问题的修复重新写进 patched/ 下的文件
     （三个问题的定位点见 maintenance.md 第四节「情况 B」）
  3. python build-patch-index.py finalize X.Y.Z
     → 校验两份快照齐全且确有差异，重算 sha256，重写 patch-index.json
  4. 重跑验证清单（maintenance.md 第五节）

用法：
    python build-patch-index.py fetch 1.14.1        # 只刷新 pristine/
    python build-patch-index.py finalize 1.14.1     # 只重写索引（要求两份快照齐全）
    python build-patch-index.py all 1.14.1          # 两步连跑（patched/ 已就绪时）
"""
import argparse
import hashlib
import io
import json
import pathlib
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
PRISTINE = ROOT / "pristine"
PATCHED = ROOT / "patched"
INDEX = ROOT / "patch-index.json"

PACKAGE = "@xzxzzx/bilibili-mcp"

# 需要打补丁的 5 个文件（相对包内 dist/ 的路径）——与 patch-index.json 的键一致
FILES = [
    "bilibili/video-api.js",     # 问题一：getVideoInfo() 改 WBI 优先 + 412 降级
    "bilibili/comments-api.js",  # 问题二：评论请求带 cursor 参数
    "bilibili/comments.js",      # 问题二：分页改用 cursor.next + rpid 去重
    "server/tool-schemas.js",    # 问题三：limit 上限 50 → 300
    "utils/validation.js",       # 问题三：上限校验同步放宽
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tarball_url(version: str) -> str:
    # scope 包：@scope/name → https://registry.npmjs.org/@scope/name/-/name-<ver>.tgz
    return (f"https://registry.npmjs.org/{PACKAGE}/-/"
            f"{PACKAGE.split('/')[-1]}-{version}.tgz")


def fetch(version: str) -> int:
    """下载指定版本，把目标文件写入 pristine/"""
    url = tarball_url(version)
    print(f"[..] 下载 {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as e:
        print(f"[FAIL] 下载失败：{e}")
        return 1
    print(f"[OK] 已下载 {len(data) / 1024:.0f} KB")

    ok = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = tf.getnames()
        for rel in FILES:
            member = f"package/dist/{rel}"
            if member not in names:
                print(f"[FAIL] tarball 中找不到 {member}")
                continue
            out = PRISTINE / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            fileobj = tf.extractfile(member)
            out.write_bytes(fileobj.read())
            print(f"[OK] pristine/{rel}  "
                  f"({out.stat().st_size} B, sha256 {sha256(out)[:12]}…)")
            ok += 1

    print()
    print(f"完成：{ok}/{len(FILES)} 个文件已刷新到 pristine/")
    if ok == len(FILES):
        print("下一步：对比新版源码，把修复重新写进 patched/ 下对应文件，然后执行：")
        print(f"        python build-patch-index.py finalize {version}")
        return 0
    return 1


def finalize(version: str) -> int:
    """校验两份快照并重写 patch-index.json"""
    problems = []
    for rel in FILES:
        pr, pa = PRISTINE / rel, PATCHED / rel
        if not pr.exists():
            problems.append(f"缺少 pristine/{rel}")
            continue
        if not pa.exists():
            problems.append(f"缺少 patched/{rel}")
            continue
        if sha256(pr) == sha256(pa):
            problems.append(f"{rel}：patched 与 pristine 完全相同（补丁没写进去？）")
    if problems:
        print("[FAIL] 校验不通过：")
        for p in problems:
            print(f"       - {p}")
        return 1

    idx = {
        "package": PACKAGE,
        "version": version,
        "tarball": tarball_url(version),
        "files": {
            rel: {
                "pristine_sha256": sha256(PRISTINE / rel),
                "pristine_size": (PRISTINE / rel).stat().st_size,
                "patched_sha256": sha256(PATCHED / rel),
                "patched_size": (PATCHED / rel).stat().st_size,
            }
            for rel in FILES
        },
    }
    # 注意：**不要**写入 live_package_dir —— 每台机器的 npx 缓存路径不同，
    # 写死会让补丁流程在别人机器上失效；apply-patch.py 会在运行时自行定位。
    INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    print(f"[OK] 已重写 {INDEX.name}（version = {version}）")
    print()
    print("下一步（验证清单见 maintenance.md 第五节）：")
    print("  1) python apply-patch.py --check       # 三种状态是否正确识别")
    print("  2) node --check <被改的 .js 文件>       # 语法校验")
    print("  3) 重启客户端后跑 scripts/check_setup.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="补丁索引生成 / 刷新（上游升级时用）")
    ap.add_argument("mode", choices=["fetch", "finalize", "all"],
                    help="fetch=刷新 pristine；finalize=重写索引；all=两步连跑")
    ap.add_argument("version", help="目标版本号，如 1.14.1")
    args = ap.parse_args()

    if args.mode == "fetch":
        return fetch(args.version)
    if args.mode == "finalize":
        return finalize(args.version)
    rc = fetch(args.version)
    if rc != 0:
        return rc
    return finalize(args.version)


if __name__ == "__main__":
    raise SystemExit(main())
