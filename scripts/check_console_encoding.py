#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描仓库里的 Python 脚本，找出**运行期字符串**里当前 ANSI 代码页编不了的字符。

为什么需要它
------------
Windows 下 Python 的 stdout **被管道接走时用系统 ANSI 代码页（cp936/GBK）**，
不是控制台代码页（本机 `chcp` 是 65001，但 `sys.stdout.encoding` 仍是 `gbk`）。
GBK 编不了 `✅`(U+2705)、`⚠`(U+26A0)、`⇒`(U+21D2) 这类字符——
**一旦 print 出去就抛 `UnicodeEncodeError`，进程以 exit 1 结束**。

这种失败特别阴：它报的是一条**假失败**。实测踩过的四例：

| 位置 | 表面症状 | 真实情况 |
|---|---|---|
| `check_setup.py` 结论行 | 自检全 `[OK]` 却 exit 1 | 检查全过，退出码骗人 |
| `apply-patch.py`「需要重启」 | 补丁打成功了却 exit 1 | **最该看到的一句话打不出来** |
| `apply-patch.py`「未知⚠」 | 诊断时直接崩 | 正是最需要诊断的时刻 |
| `fetch_video.py` 判读行 | 数据取到了却 exit 1 | 主取数脚本挂了 |

而且它**只在输出被管道接走时发作**——人在终端里跑往往完全正常（终端是 UTF-8）。
所以"我本地跑过没问题"不能作为证据。

用法
----
    python scripts/check_console_encoding.py            # 扫全仓库
    python scripts/check_console_encoding.py --all      # 连 docstring 一起报（一般不用）

退出码：0 = 干净；1 = 有运行期字符串编不了（**必须改**）。

**docstring 里的特殊符号不用管**——它们从不打印。本脚本默认排除，
只在 `--all` 模式下才报出来。
"""

import argparse
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENC = "gbk"
EXCLUDE_DIRS = {".git", "__pycache__", "_deleted", ".venv", "node_modules"}


def unencodable(text: str):
    bad = []
    for ch in set(text):
        try:
            ch.encode(ENC)
        except UnicodeEncodeError:
            bad.append(ch)
    return sorted(bad, key=ord)


def docstring_ids(tree) -> set:
    """收集所有 docstring 节点的 id——它们从不参与运行期输出。

    注意：只认 Module / FunctionDef / AsyncFunctionDef / ClassDef。
    `ast.Lambda` 也有 `.body`，但那里装的是**表达式**（不是语句列表），
    写成 `body[0]` 会抛 `TypeError: 'Subscript' object is not subscriptable`。
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.add(id(first.value))
    return out


def scan(show_all: bool):
    runtime_hits = []
    doc_hits = []
    files = 0
    for py in sorted(ROOT.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SKIP] {py.relative_to(ROOT)}: {e}")
            continue
        files += 1
        docs = docstring_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            bad = unencodable(node.value)
            if not bad:
                continue
            names = " ".join(f"U+{ord(c):04X}" for c in bad)
            item = (py.relative_to(ROOT), node.lineno, names, node.value[:60])
            if id(node) in docs:
                doc_hits.append(item)
            else:
                runtime_hits.append(item)

    print("=" * 62)
    print("console-encoding audit")
    print("=" * 62)
    print(f"scanned {files} py files; encoding = {ENC}")

    if runtime_hits:
        print(f"\n[FAIL] {len(runtime_hits)} 处运行期字符串用当前代码页编不了：")
        for path, lineno, names, snippet in runtime_hits:
            print(f"  {path}:{lineno}  {names}")
            print(f"      {snippet!r}")
        print("\n改成代码页能编的写法，例如：")
        # 注意：这行本身也必须是 GBK 可编的——否则本脚本在管道下会先崩给你看。
        print("  U+2705 (check mark)  ->  [OK]")
        print("  U+26A0 (warning)     ->  [注意]")
        print("  U+21D2 (double arrow) ->  ->")
        print("（中文没问题，GBK 有；emoji 和少见符号才有问题）")
        return 1

    print("\n[OK] 没有运行期字符串用当前代码页编不了。")
    if doc_hits and not show_all:
        print(f"     （另有 {len(doc_hits)} 处在 docstring 里——从不打印，不用管；"
              f"加 --all 可看）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="扫描 GBK 编不了的运行期字符串")
    ap.add_argument("--all", action="store_true", help="连 docstring 一起报")
    args = ap.parse_args()
    sys.exit(scan(args.all))
