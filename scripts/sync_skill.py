#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源真源 → 部署副本 的同步工具（check / deploy / prune）

为什么需要它
------------
本仓库（项目目录）才是**唯一真源**。各 Agent 客户端的 skills 目录里放的是**部署副本**，
供客户端自动加载。两边会漂移——改了真源却忘了同步，Agent 读到的还是旧版规则。

所以规矩是：**用之前先查本地状态，有漂移先同步再干活。**

    python scripts/sync_skill.py --check      # 只报告漂移，不写任何文件
    python scripts/sync_skill.py --deploy     # 把真源同步到各部署副本
    python scripts/sync_skill.py --deploy --prune   # 额外把"上游已删"的文件移入暂存区

必须**从项目仓库目录运行**（本脚本所在目录的上级是 git 仓库才算真源）。
在部署副本里跑会被拒绝——部署副本不是真源，从它同步只会把旧版再复制一遍。

退出码
------
    0 = 全部同步
    1 = 存在漂移（**不是错误**，输出里会写明该跑什么命令）
    2 = 参数或环境错误

同步范围
--------
源文件清单 = `git ls-files --cached --others --exclude-standard`
（受管文件 + 未被 .gitignore 忽略的未跟踪文件），自动排除
`.git/` `__pycache__/` `*.pyc` 等噪声。

删除纪律
--------
`--prune` **不直接删文件**，而是移到真源同盘的一个暂存目录
（`<客户端根目录>/_deleted_skill-sync_<YYYYMMDD>/`）并写 `_MANIFEST.md`，
由使用者把整个文件夹一次性放进回收站。**不做永久删除。**
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

SKILL_NAME = "bilibili-video-analysis-summary"

EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".idea", ".vscode", ".ipynb_checkpoints"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".swp", ".tmp"}

# 允许存在于部署副本、但不属于源文件清单的文件（不算"多余"）
ALLOWED_EXTRAS = {".sync-state.json"}

# 已知客户端的 skills 目录（相对用户主目录）
CLIENT_SKILL_DIRS = [
    ("MiniMax Code", ".minimax/skills"),
    ("WorkBuddy", ".workbuddy/skills"),
    ("Claude Code", ".claude/skills"),
]


# ---------------------------------------------------------------- 基础工具

def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(d in EXCLUDE_DIRS for d in parts):
        return True
    return os.path.splitext(rel)[1].lower() in EXCLUDE_SUFFIX


def walk_files(root: pathlib.Path):
    """遍历目录下的文件，跳过噪声目录。返回 (相对路径, 绝对路径)。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            p = pathlib.Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if excluded(rel):
                continue
            yield rel, p


# ---------------------------------------------------------------- 源与目标

def source_files(src: pathlib.Path):
    """源文件清单：git 受管 + 未被忽略的未跟踪文件。"""
    names = []
    try:
        r = subprocess.run(
            ["git", "-C", str(src), "-c", "core.quotepath=false",
             "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True)
        names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
    except Exception as e:
        print(f"[WARN] git ls-files 失败（{e}），改用目录遍历", file=sys.stderr)
    if not names:
        names = [rel for rel, _ in walk_files(src)]
    return sorted({n.replace("\\", "/") for n in names if not excluded(n.replace("\\", "/"))})


def discover_targets(explicit):
    """返回 [(客户端名, 目标目录)]，只保留已存在的。"""
    out = []
    if explicit:
        # 显式指定的目标**允许不存在**——首次部署到某个客户端时目录本来就是空的。
        # 目录会在 --deploy 时被创建。
        for p in explicit:
            t = pathlib.Path(p).expanduser().absolute()
            out.append(("指定目标", t))
        return out

    env = os.environ.get("BILI_SKILL_TARGETS", "")
    for item in env.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        t = pathlib.Path(item).expanduser()
        if t.exists():
            out.append(("环境变量", t.absolute()))

    home = pathlib.Path.home()
    for label, rel in CLIENT_SKILL_DIRS:
        t = home / rel / SKILL_NAME
        if t.exists():
            out.append((label, t.absolute()))
    return out


def is_same_dir(a: pathlib.Path, b: pathlib.Path) -> bool:
    """两个路径是不是同一个目录（含 junction / 符号链接指回真源的情况）。"""
    try:
        return os.path.samefile(str(a), str(b))
    except Exception:
        try:
            return os.path.abspath(str(a)) == os.path.abspath(str(b))
        except Exception:
            return False


def is_junction(p: pathlib.Path) -> bool:
    """目标是不是 junction / 符号链接（而不是真拷贝）。"""
    try:
        isj = getattr(os.path, "isjunction", None)   # Python 3.12+
        if isj is not None:
            return bool(isj(str(p)))
    except Exception:
        pass
    try:
        return os.path.islink(str(p))
    except Exception:
        return False


def skill_name_of(root: pathlib.Path):
    """读 SKILL.md frontmatter 的 name:，用于校验目录名是否一致。"""
    p = root / "SKILL.md"
    if not p.exists():
        return None
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 差异计算

def diff_target(src: pathlib.Path, tgt: pathlib.Path, files):
    """返回 (缺失, 更新, 多余) 三个列表。"""
    missing, changed, extra = [], [], []
    for rel in files:
        s = src / rel
        t = tgt / rel
        if not t.exists():
            missing.append(rel)
        elif sha256(s) != sha256(t):
            changed.append(rel)
    for rel, _ in walk_files(tgt):
        if rel in ALLOWED_EXTRAS:
            continue
        if rel not in set(files):
            extra.append(rel)
    return missing, changed, extra


# ---------------------------------------------------------------- 执行

def do_deploy(src, tgt, missing, changed):
    n = 0
    for rel in missing + changed:
        s = src / rel
        t = tgt / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)
        n += 1
    return n


def do_prune(tgt: pathlib.Path, extra, src: pathlib.Path):
    """把"上游已删"的文件移到暂存区（不删），并写 _MANIFEST.md。"""
    if not extra:
        return None, 0
    date = datetime.date.today().strftime("%Y%m%d")
    # 暂存区放在**客户端根目录**（skills 的上级），避免被 skills 扫描器当成 skill
    stage = tgt.parent.parent / f"_deleted_skill-sync_{date}"
    stage.mkdir(parents=True, exist_ok=True)
    rows = []
    for rel in extra:
        s = tgt / rel
        d = stage / SKILL_NAME / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        size = s.stat().st_size
        mtime = datetime.datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        rows.append((s, size, mtime))
        shutil.move(str(s), str(d))
    manifest = stage / "_MANIFEST.md"
    lines = [
        "# 删除清单 — skill 部署同步",
        "",
        f"- 任务：把真源同步到部署副本时，清理「上游已删」的文件",
        f"- 真源：{src}",
        f"- 部署副本：{tgt}",
        f"- 日期：{date}",
        "",
        "| 原始绝对路径 | 大小 | 修改时间 |",
        "|---|---|---|",
    ]
    for p, size, mtime in rows:
        lines.append(f"| `{p}` | {size} B | {mtime} |")
    lines += [
        "",
        "本文件夹整体进回收站，回收站内只保留这一个条目。",
        "恢复方式：按上表「原始绝对路径」放回原位即可。",
    ]
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return stage, len(rows)


def write_state(src: pathlib.Path, tgt: pathlib.Path):
    """在部署副本留一份状态戳：来自哪个真源、什么时候同步的。"""
    import json
    state = {
        "source": str(src),
        "skill": SKILL_NAME,
        "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_files": len(source_files(src)),
    }
    (tgt / ".sync-state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(
        description="源真源 → 部署副本 的同步检查与部署")
    ap.add_argument("--source", help="真源目录（默认：本脚本所在的项目仓库根目录）")
    ap.add_argument("--target", action="append",
                    help="指定部署副本目录，可重复；不给则自动发现已知客户端")
    ap.add_argument("--check", action="store_true", help="只检查，不写任何文件")
    ap.add_argument("--deploy", action="store_true", help="同步差异文件")
    ap.add_argument("--prune", action="store_true",
                    help="配合 --deploy：把「上游已删」的文件移入暂存区（不直接删）")
    ap.add_argument("--list", action="store_true", help="只列出识别到的目标")
    args = ap.parse_args()

    if not (args.check or args.deploy or args.list):
        args.check = True

    src = pathlib.Path(args.source).expanduser().resolve() if args.source else \
        pathlib.Path(__file__).resolve().parent.parent

    if not (src / "SKILL.md").exists():
        print(f"[FAIL] 真源目录里没有 SKILL.md：{src}", file=sys.stderr)
        return 2
    if not (src / ".git").exists() and not args.source:
        print("[FAIL] 本脚本要从**项目仓库目录**运行（真源）。", file=sys.stderr)
        print(f"       当前目录没有 .git：{src}", file=sys.stderr)
        print("       你多半是在部署副本里跑的。部署副本不是真源。", file=sys.stderr)
        print("       正确做法：cd 到项目仓库目录再执行；或用 --source <真源路径>。", file=sys.stderr)
        return 2

    files = source_files(src)
    targets = discover_targets(args.target)

    print("=" * 62)
    print("skill 部署同步检查")
    print("=" * 62)
    print(f"真源：{src}")
    print(f"      （{len(files)} 个受管文件）")

    if not targets:
        print("\n[WARN] 没找到任何部署副本。")
        print("       常见位置：")
        for label, rel in CLIENT_SKILL_DIRS:
            print(f"         {label:<14} ~/{rel}/{SKILL_NAME}")
        print("       可用 --target <目录> 指定，或设环境变量 BILI_SKILL_TARGETS。")
        return 0

    if args.list:
        for label, tgt in targets:
            print(f"  {label:<14} {tgt}")
        return 0

    print()
    drifted = 0
    for label, tgt in targets:
        if is_same_dir(src, tgt):
            kind = "junction" if is_junction(tgt) else "同一目录"
            print(f"  {label:<14} {tgt}")
            print(f"                 [无需同步]  目标就是真源本身（{kind}）——读到的永远是最新版")
            continue
        name = skill_name_of(tgt)
        if name and name != tgt.name:
            print(f"  [WARN] {label} 目录名与 SKILL.md 的 name: 不一致（{tgt.name} vs {name}）")
        missing, changed, extra = diff_target(src, tgt, files)
        n = len(missing) + len(changed) + len(extra)
        exists = tgt.exists()
        print(f"  {label:<14} {tgt}")
        if not exists:
            print(f"                 [目标不存在]  --deploy 时会创建")
        else:
            state = "已同步" if n == 0 else "需要同步"
            print(f"                 [{state}]  缺失 {len(missing)} / 更新 {len(changed)} / 多余 {len(extra)}")
        for rel in missing[:6]:
            print(f"                   + 新增  {rel}")
        if len(missing) > 6:
            print(f"                   + 新增  …另有 {len(missing) - 6} 个")
        for rel in changed[:6]:
            print(f"                   ~ 更新  {rel}")
        if len(changed) > 6:
            print(f"                   ~ 更新  …另有 {len(changed) - 6} 个")
        for rel in extra[:6]:
            print(f"                   - 多余  {rel}")
        if len(extra) > 6:
            print(f"                   - 多余  …另有 {len(extra) - 6} 个")

        if args.deploy and n:
            tgt.mkdir(parents=True, exist_ok=True)
            copied = do_deploy(src, tgt, missing, changed)
            print(f"                 → 已同步 {copied} 个文件")
            if args.prune and extra:
                stage, cnt = do_prune(tgt, extra, src)
                print(f"                 → {cnt} 个「上游已删」文件已移入暂存区：{stage}")
                print(f"                    （不直接删；请把整个文件夹一次性放进回收站）")
            elif extra:
                print(f"                 → 提示：{len(extra)} 个「上游已删」文件仍留在副本里，"
                      f"加 --prune 可移入暂存区")
            write_state(src, tgt)
            # 部署完重新核对：只有真的同步上了，才算这个目标已处理
            m2, c2, e2 = diff_target(src, tgt, files)
            n = len(m2) + len(c2) + len(e2)
            if n:
                print(f"                 [WARN] 同步后仍有差异：缺失 {len(m2)} / 更新 {len(c2)} / 多余 {len(e2)}")

        if n:
            drifted += 1

    print("-" * 62)
    if drifted == 0:
        print("结论：所有部署副本都是最新的。")
        return 0
    print(f"结论：{drifted}/{len(targets)} 个部署副本需要同步。")
    if not args.deploy:
        print("       → python scripts/sync_skill.py --deploy")
    return 1


if __name__ == "__main__":
    sys.exit(main())
