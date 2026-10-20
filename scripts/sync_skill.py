#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源目录 → 各客户端副本 的同步工具（check / deploy / prune）

两种身份，两种用法
------------------
**使用者**（从 GitHub 装到 Agent 的 skills 目录，之后没再单独维护过一份）：
    你的 skill 目录**就是唯一一份**，没有别的副本要同步。
    本脚本会直接告诉你"无需同步"，**不需要你做任何操作**。

**维护者**（项目仓库和 skill 安装位置是分开的：自己改仓库，或一个 skill 接多个 Agent）：
    项目仓库是唯一的编辑目标，各客户端 skills 目录是**部署副本**。
    改动真源后用 `--deploy` 送过去；动手前用 `--check` 看有没有漂移。

    为什么不能从 GitHub 反向拉：Agent 直接接管项目文件夹后，
    **本地项目目录才是最新的**（含还没提交的改动）。从 GitHub 同步会把
    本地已做但未推送的改动**悄悄回滚掉**——典型的静默错误。

用法
----
    python scripts/sync_skill.py --check      # 只报告漂移，不写任何文件
    python scripts/sync_skill.py --deploy     # 把源目录同步到各客户端副本
    python scripts/sync_skill.py --deploy --prune   # 额外把"上游已删"的文件移入暂存区
    python scripts/sync_skill.py --list       # 只列出识别到的目标

    --source <目录>   指定真正的源目录（维护者场景可用）
    --target <目录>   指定目标，可重复；可指向尚不存在的目录（首次部署）

退出码
------
    0 = 全部同步 / 无需同步
    1 = 存在漂移（**不是错误**，输出里会写明该跑什么命令）
    2 = 参数或环境错误

同步范围
--------
源文件清单 = `git ls-files --cached --others --exclude-standard`
（受管文件 + 未被 .gitignore 忽略的未跟踪文件），自动排除
`.git/` `__pycache__/` `*.pyc` 等噪声。**不是 git 仓库也能跑**（zip 安装无妨）。

删除纪律
--------
`--prune` **不直接删文件**，而是移到同盘的一个暂存目录
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
    except Exception:
        pass
    if not names:
        # 不是 git 仓库（zip 安装）→ 直接遍历目录
        names = [rel for rel, _ in walk_files(src)]
    return sorted({n.replace("\\", "/") for n in names if not excluded(n.replace("\\", "/"))})


def client_skill_dirs():
    """本机已存在的客户端 skills 目录（绝对路径）。"""
    home = pathlib.Path.home()
    out = []
    for label, rel in CLIENT_SKILL_DIRS:
        t = home / rel / SKILL_NAME
        if t.exists():
            out.append((label, t.absolute()))
    return out


def discover_targets(explicit):
    """返回 [(客户端名, 目标目录)]。"""
    if explicit:
        # 显式指定的目标**允许不存在**——首次部署时目录本来就是空的，
        # 目录会在 --deploy 时被创建。
        return [("指定目标", pathlib.Path(p).expanduser().absolute()) for p in explicit]

    env = os.environ.get("BILI_SKILL_TARGETS", "")
    out = []
    for item in env.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        t = pathlib.Path(item).expanduser()
        if t.exists():
            out.append(("环境变量", t.absolute()))
    out += client_skill_dirs()
    return out


def is_same_dir(a: pathlib.Path, b: pathlib.Path) -> bool:
    """两个路径是不是同一个目录（含 junction / 符号链接指回同一处）。"""
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


def is_installed_copy(src: pathlib.Path) -> bool:
    """源目录是不是「装在某个客户端 skills 目录里的那一份」。"""
    for _, t in client_skill_dirs():
        if is_same_dir(src, t):
            return True
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
    srcset = set(files)
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
        if rel not in srcset:
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
        "- 任务：把源目录同步到部署副本时，清理「上游已删」的文件",
        f"- 源目录：{src}",
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
    """在部署副本留一份状态戳：来自哪个源、什么时候同步的。"""
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
        description="源目录 → 各客户端副本 的同步检查与部署")
    ap.add_argument("--source", help="源目录（默认：本脚本所在的项目目录）")
    ap.add_argument("--target", action="append",
                    help="指定目标目录，可重复；不给则自动发现已装的客户端")
    ap.add_argument("--check", action="store_true", help="只检查，不写任何文件")
    ap.add_argument("--deploy", action="store_true", help="同步差异文件")
    ap.add_argument("--prune", action="store_true",
                    help="配合 --deploy：把「上游已删」的文件移入暂存区（不直接删）")
    ap.add_argument("--list", action="store_true", help="只列出识别到的目标")
    args = ap.parse_args()

    if not (args.check or args.deploy or args.list):
        args.check = True

    src = pathlib.Path(args.source).expanduser().absolute() if args.source else \
        pathlib.Path(__file__).resolve().parent.parent

    if not (src / "SKILL.md").exists():
        print(f"[FAIL] 目录里没有 SKILL.md：{src}", file=sys.stderr)
        return 2

    files = source_files(src)
    targets = discover_targets(args.target)

    print("=" * 62)
    print("skill 同步检查")
    print("=" * 62)
    print(f"源目录：{src}")
    if not (src / ".git").exists():
        print("        （不是 git 仓库——zip 安装无妨，不影响检查）")
    print(f"        （{len(files)} 个文件）")

    if not targets:
        print("\n[OK] 本机只找到这一份 skill，没有别的副本需要同步。")
        print("      使用者看到这里就结束——你的 skill 就是最新的，不用做任何事。")
        return 0

    if args.list:
        for label, tgt in targets:
            print(f"  {label:<14} {tgt}")
        return 0

    # 源目录本身就是"装在客户端 skills 目录里的那一份"
    #   → 对使用者：这就是他的全部家当，没有别的副本要同步
    #   → 对维护者：从副本往别处推是**反方向**，必须挡住
    others = [(lb, t) for lb, t in targets if not is_same_dir(src, t)]
    if others and is_installed_copy(src) and not args.source:
        print("\n[SKIP] 你是在**已安装副本**里运行的这个脚本。")
        print("       同步方向是 项目仓库 → 各客户端副本；从副本往别处推是反的。")
        print("       正确做法：cd 到你的项目仓库目录再执行，或加 --source <项目仓库路径>。")
        return 0

    print()
    drifted = 0
    for label, tgt in targets:
        if is_same_dir(src, tgt):
            kind = "junction" if is_junction(tgt) else "安装位置即源目录"
            print(f"  {label:<14} {tgt}")
            print(f"                 [无需同步]  目标就是源目录本身（{kind}）")
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
            # 安全闸：不能用一份"非仓库拷贝"覆盖一个"真正的开发副本"。
            # 典型踩法：在 zip 下载的副本里跑 --deploy，结果把真源的未提交改动盖掉。
            if not (src / ".git").exists() and (tgt / ".git").exists():
                print(f"                 [拒绝] 源目录不是 git 仓库、目标是 git 仓库——"
                      f"拒绝用拷贝覆盖开发副本")
                print(f"                        （确认要覆盖请手动删除目标，或用 --source 指定真正的源）")
                n = len(missing) + len(changed) + len(extra)
                drifted += 1
                continue
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
        print("结论：所有副本都是最新的。")
        return 0
    print(f"结论：{drifted}/{len(targets)} 个副本需要同步。")
    if not args.deploy:
        print("       → python scripts/sync_skill.py --deploy")
    return 1


if __name__ == "__main__":
    sys.exit(main())
