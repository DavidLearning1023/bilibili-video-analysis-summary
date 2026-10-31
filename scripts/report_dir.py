#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告落盘目录：解析 / 设置 / 分任务子目录 / 首次使用引导

为什么要有这个脚本：默认落盘目录是**使用者本机的偏好**，不属于 skill 的公共规则。
写进 `SKILL.md` 会让公开仓库里躺着一个别人机器上不存在的路径，换机也会失效。
所以它落在 gitignore 掉的 `.local/` 里，由本脚本统一解析——Agent 每次**跑一次取路径**，
不靠记忆、不靠猜，也不会把旧路径带进新会话。

三级状态（Agent 必须区分：第一级要问一次，第二级别问，第三级也不用再提"设默认"）：

    状态     触发条件                         Agent 该做什么                 退出码
    ready    --dir / 环境变量 / 本地配置命中   直接用，落盘前回显一次            0
    first    首次使用：没有配置、也没有标记     **必须问一次**，问完 --set        3
    ask      --unset 过：用户选了「每次都问」   按三选项问，别再提"设默认"         4

解析优先级（高 → 低）：

    1. `--dir <路径>`            命令行显式指定（临时覆盖）
    2. 环境变量 `BVA_REPORT_DIR`  适合 CI / 多客户端统一配置
    3. `.local/report-dir.txt`   本地配置（`--set` 写入，入 gitignore，不跟随仓库）
    4. 未配置                    看 `.local/ask-each-time` 标记决定 first / ask

落盘结构（2026-10-31 起）：**一份报告一个同名子目录**，不要再往 reports 根上平铺。

    <默认目录>/
    └── 20261031-《视频标题》分析总结/        <- 目录名 = 报告名
        ├── 20261031-《视频标题》分析总结.html
        └── 20261031-《视频标题》分析总结.pdf

用法：

    python scripts/report_dir.py                                  # 打印默认目录（并创建）
    python scripts/report_dir.py --task "20261031-《标题》分析总结"  # 打印并创建该报告的子目录
    python scripts/report_dir.py --show                           # 各级取值 + 最终来源 + 状态
    python scripts/report_dir.py --set "D:\\某目录\\reports"         # 设默认目录（首次引导用这个）
    python scripts/report_dir.py --unset                          # 回到「每次都问」
    python scripts/report_dir.py --reset                          # 回到「首次未配置」，下次会重新问
    python scripts/report_dir.py --json                           # 结构化输出

输出约定：**stdout 第一行永远是路径**（未就绪时为空行），其余说明走 stderr。
这样 `$(python scripts/report_dir.py)` 拿到的就是干净的路径。

退出码：0 = ready；3 = first（首次，必须问）；4 = ask（每次都问）；2 = 路径非法/无法创建。
"""

import argparse
import json
import os
import pathlib
import sys

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_FILE = SKILL_ROOT / ".local" / "report-dir.txt"
ASK_MARKER = SKILL_ROOT / ".local" / "ask-each-time"
ENV_KEY = "BVA_REPORT_DIR"

EXIT_OK = 0
EXIT_BAD = 2
EXIT_FIRST = 3
EXIT_ASK = 4

# 文件系统非法字符（命名规则的完整版在 references/output-template.md 第一节）
ILLEGAL = '/\\:*?"<>|'


def info(msg):
    """说明性输出一律走 stderr，避免污染 stdout 的路径首行。"""
    print(msg, file=sys.stderr)


def normalize(raw):
    """展开 ~ 与环境变量，返回绝对路径；空值返回 None。"""
    if not raw:
        return None
    text = raw.strip().strip('"').strip("'")
    if not text:
        return None
    text = os.path.expandvars(os.path.expanduser(text))
    return str(pathlib.Path(text).absolute())


def read_config():
    if not CONFIG_FILE.is_file():
        return None
    try:
        # utf-8-sig：兼容用记事本/部分编辑器保存时带的 BOM
        lines = CONFIG_FILE.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        info(f"[WARN] 读取本地配置失败：{exc}")
        return None
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def resolve(cli_dir=None):
    """返回 (路径, 来源)。来源取值：cli / env / config / unset。"""
    if cli_dir:
        return normalize(cli_dir), "cli"
    env_val = os.environ.get(ENV_KEY)
    if env_val and env_val.strip():
        return normalize(env_val), "env"
    cfg = read_config()
    if cfg:
        return normalize(cfg), "config"
    return None, "unset"


def state_of(source):
    """把解析结果映射成三级状态：ready / first / ask。"""
    if source != "unset":
        return "ready"
    return "ask" if ASK_MARKER.is_file() else "first"


def ensure_dir(path):
    try:
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)
        return True, None
    except OSError as exc:
        return False, str(exc)


def check_name(name):
    """分任务目录名只做体检，不改写——改写规则由 Agent 按 output-template.md 先做。"""
    bad = [c for c in ILLEGAL if c in name]
    if bad:
        info("[WARN] 目录名含文件系统非法字符：" + " ".join(bad)
             + "  （先把这些字符换成全角再调用本脚本）")
        return False
    return True


def cmd_set(target):
    path = normalize(target)
    if not path:
        info("[FAIL] --set 需要一个非空路径")
        return EXIT_BAD
    ok, err = ensure_dir(path)
    if not ok:
        info(f"[FAIL] 目录无法创建：{err}")
        return EXIT_BAD
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        "# 报告默认落盘目录（本文件已入 .gitignore，不随仓库分发）\n"
        "# 换机后跑一次：python scripts/report_dir.py --set \"<新目录>\"\n"
        f"{path}\n",
        encoding="utf-8",
    )
    if ASK_MARKER.is_file():
        ASK_MARKER.unlink()
    info(f"[OK]   已写入 {CONFIG_FILE}")
    print(path)
    return EXIT_OK


def cmd_unset():
    """回到「每次都问」，并记下这个选择——别再反复提示用户去设默认目录。"""
    removed = False
    if CONFIG_FILE.is_file():
        CONFIG_FILE.unlink()
        removed = True
    ASK_MARKER.parent.mkdir(parents=True, exist_ok=True)
    ASK_MARKER.write_text("用户选择：每次询问报告落盘位置\n", encoding="utf-8")
    info(f"[OK]   {'已删除' if removed else '本来没有'}本地配置，标记为「每次都问」")
    info(f"       标记文件：{ASK_MARKER}")
    info("       想重新走首次引导：--reset")
    return EXIT_OK


def cmd_reset():
    """清掉配置与标记，回到「首次未配置」——下次会再问一遍。"""
    gone = []
    for f in (CONFIG_FILE, ASK_MARKER):
        if f.is_file():
            f.unlink()
            gone.append(f.name)
    info(f"[OK]   已清除：{'、'.join(gone) if gone else '（本来就没有东西）'}")
    return EXIT_OK


def main():
    ap = argparse.ArgumentParser(
        description="解析/设置 B站视频分析报告 的落盘目录（含分任务子目录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dir", default=None, help="显式指定目录（优先级最高，不落盘配置）")
    ap.add_argument("--task", default=None, metavar="NAME",
                    help="报告名（= 文件名主干），打印并创建 <默认目录>/<NAME> 子目录")
    ap.add_argument("--set", default=None, metavar="PATH", help="设置默认目录")
    ap.add_argument("--unset", action="store_true", help="回到「每次都问」")
    ap.add_argument("--reset", action="store_true", help="回到「首次未配置」")
    ap.add_argument("--show", action="store_true", help="显示各级取值、最终来源与状态")
    ap.add_argument("--json", action="store_true", help="结构化输出")
    ap.add_argument("--no-create", action="store_true", help="只解析，不创建目录")
    args = ap.parse_args()

    if args.set:
        return cmd_set(args.set)
    if args.unset:
        return cmd_unset()
    if args.reset:
        return cmd_reset()

    path, source = resolve(args.dir)
    state = state_of(source)

    if args.show:
        info("解析优先级：--dir > 环境变量 BVA_REPORT_DIR > .local/report-dir.txt")
        info(f"  命令行   : {args.dir or '(未给)'}")
        info(f"  环境变量 : {os.environ.get(ENV_KEY) or '(未设)'}")
        info(f"  本地配置 : {read_config() or '(无)'}  <- {CONFIG_FILE}")
        info(f"  每次都问标记 : {'有' if ASK_MARKER.is_file() else '无'}")
        info(f"  最终来源 / 状态 : {source} / {state}")

    hint = {
        "first": "首次使用：必须问用户一次存哪里，问完用 --set 记住。",
        "ask": "用户选了「每次都问」：按三选项问，别再提“设默认目录”。",
    }.get(state)

    if state != "ready":
        if args.json:
            print(json.dumps({"path": None, "source": source, "state": state,
                              "config_file": str(CONFIG_FILE)}, ensure_ascii=False))
        else:
            print("")
        info(f"[{state.upper()}] {hint}")
        return EXIT_FIRST if state == "first" else EXIT_ASK

    if args.task:
        if not check_name(args.task):
            print("")
            return EXIT_BAD
        path = str(pathlib.Path(path) / args.task)

    created = False
    if not args.no_create:
        ok, err = ensure_dir(path)
        if not ok:
            info(f"[FAIL] 目录无法创建：{err}")
            print("")
            return EXIT_BAD
        created = True

    if args.json:
        print(json.dumps({"path": path, "source": source, "state": state,
                          "created": created, "config_file": str(CONFIG_FILE)},
                         ensure_ascii=False))
    else:
        print(path)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
