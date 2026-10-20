# 维护流程（Agent 专用）

> 本 skill 由 Agent 维护，用户不直接改文件。这份文档规定"改什么、怎么改、改完怎么验证"。

---

## 〇、你是哪种身份？这决定你改哪里（**先读这段**）

同一个 skill 目录，在两种人手里是**两回事**：

| 身份 | 你的 skill 目录 | 改动落在哪 | 要跑同步吗 |
|---|---|---|---|
| **使用者**（从 GitHub 装来用，偶尔让 AI 改个措辞） | **就是编辑目标** | 直接改这个目录 | **不需要**。`sync_skill.py` 与你无关 |
| **上游维护者**（开发这份 skill，一个项目仓库接多个 Agent 客户端） | 是**部署副本** | 改**项目仓库**（真源），再部署过去 | 需要，见下 |

> ⚠️ **大多数维护任务属于第一行。** 用户说"把 X 改成 Y"时，
> **直接改他的 skill 目录**就行——那对他就是真源。别急着搞同步那套。
>
> 只有当你**自己**把项目仓库和客户端安装位置分开维护时（上游维护者），才有下面这层。

### 上游维护者：真源 → 部署副本

```
真源      项目仓库目录（Agent 的工作区，git 仓库）      ← 改这里
             │  scripts/sync_skill.py --deploy
             ↓
部署副本   ~/.minimax/skills/<name>/     MiniMax Code 读这里
           ~/.workbuddy/skills/<name>/    WorkBuddy 读这里
           ~/.claude/skills/<name>/       Claude Code 读这里
```

| | 是什么 | 改不改 |
|---|---|---|
| **真源** | 项目仓库目录，git 仓库，有 GitHub 远端 | **唯一的编辑目标**。所有改动先落这里 |
| **部署副本** | 各客户端 skills 目录下的同名文件夹 | **只读**，由 `sync_skill.py --deploy` 覆盖 |

**为什么不再从 GitHub 拉**：Agent 直接接管项目文件夹，**本地状态才是最新的**（含还没提交的改动）。
从 GitHub 同步会把本地已做但未推送的改动**悄悄回滚掉**。所以方向永远是**本地 → 副本**。

**固定动作**：

```bash
python scripts/sync_skill.py --check     # 动手前：看有没有漂移
python scripts/sync_skill.py --deploy    # 收尾后：把改动送到各客户端
```

| 情况 | 怎么办 |
|---|---|
| `--check` 退出码 1 | 有漂移 → 跑 `--deploy`，然后**重读 SKILL.md**（旧规则会做出相反动作） |
| `--check` 说「本机只找到这一份」 | 你是使用者，这一步跳过 |
| `--check` 说「目标就是源目录本身」 | 目标是指向真源的 **junction/符号链接**，结构上不可能漂移，不用管 |
| `--check` 说「你是在已安装副本里运行」 | 同步方向反了 → `cd` 回项目目录，或加 `--source <真源路径>` |
| `--deploy` 报「上游已删」的文件 | 加 `--prune`：移到 `<客户端根>/_deleted_skill-sync_<日期>/` 并写 `_MANIFEST.md`，**由用户把整个文件夹放进回收站**（不做永久删除） |
| 部署副本自带 `.git` | 脚本不动它。它的 git 状态会显示 modified，属正常——**真源才是权威** |

**两种接法，都支持：**

| 接法 | 怎么来的 | 特点 |
|---|---|---|
| **junction / 符号链接** | `New-Item -ItemType Junction -Path <skills>\<name> -Target <真源>` | 客户端读到的**就是真源本身**，**结构上不可能漂移**，改动即时生效。Windows 限定（macOS/Linux 用 `ln -s`） |
| **普通拷贝** | `sync_skill.py --deploy` 复制 | 跨平台通用，但要靠 `--check` 保证不落后 |

> 同一个 skill 在不同客户端上可以混用两种接法——脚本会逐个识别并给出对应结论。

**同步的粒度是文件内容（SHA256），不是 git 提交。** 所以真源改了但还没提交，照样能同步过去。
源文件清单 = `git ls-files --cached --others --exclude-standard`（受管文件 + 未被忽略的未跟踪文件），
自动排除 `.git/` `__pycache__/` `*.pyc`。**不是 git 仓库也能跑**（zip 安装无妨）。

---

## 一、日常自检（最先做的一件事）

```bash
python scripts/check_setup.py
```

输出解读：

| 输出 | 含义 | 动作 |
|---|---|---|
| 三个补丁全 `[OK]`、凭据未过期、API 通 | 一切正常 | 直接干活 |
| 某个补丁 `[FAIL]` | 补丁丢了 | `cd assets/mcp-patch && python apply-patch.py`，再让用户重启 |
| 凭据 `[WARN] 过期` | 登录态失效 | 让用户重跑 `setup` 扫码 |
| 找不到包 | MCP 没装或被移走 | 让用户重装，或改用通道 B（`fetch_video.py`） |

**判断优先级**：先看补丁，再看凭据，最后看网络。三个补丁都在而工具仍失败 → 去 `troubleshooting.md` 查症状。

---

## 二、按需求改文件（速查）

| 需求 | 改哪个文件 | 注意 |
|---|---|---|
| 改生成文档的结构/章节/要求 | `references/output-template.md` | 纯 markdown，改完即生效，**不用重启** |
| 改输出命名 / 落盘位置 / 文档大块划分 | `references/output-template.md` 第一、二节 + `SKILL.md` 第 2 步提示、第 5 步 ①②③、硬性规则 6/7 | 多处必须同时改，否则会打架 |
| 改 skill 名称 | 目录名 + `SKILL.md` 的 `name:` | **两者必须完全一致**，否则加载失败 |
| 改触发时机 | `SKILL.md` 的 `description` + 「触发边界」一节 | 同时改，保持一致 |
| 改取数逻辑 | `scripts/fetch_video.py` | 红线见下 |
| 修 MCP 故障 | `references/troubleshooting.md` → `assets/mcp-patch/` | 打完补丁要重启 |
| 升级 MCP 版本 | 见第四节 | 需重新适配补丁 |

---

## 三、改名完整步骤（容易漏）

**约束**：`name` 字段只能用**小写字母、数字、连字符**（校验正则 `^[a-z0-9-]+$`），不能以连字符开头/结尾，不能有连续连字符。**中文名不可用**，必须用英文 slug。

步骤：

1. **改目录名**：`<你的 skills 目录>/<旧名>/` → `<新名>/`（各客户端 skills 目录位置见仓库根目录 `INSTALL.md` 对照表）
2. **改 `SKILL.md` frontmatter 的 `name:`** —— 必须与目录名逐字符一致
3. **全局搜索旧名**，替换所有出现的路径引用：
   ```bash
   # 在 skill 目录内搜（注意排除 CHANGELOG 里的历史记录，那是历史事实不用改）
   grep -rn "<旧名>" . --include=*.md --include=*.py
   ```
4. **重新打包** zip（如果已分发给别人）
5. **验证**：重跑 `check_setup.py` + 打包校验

⚠️ **不要改 `CHANGELOG.md` 里的历史记录**——那记录的是"当时叫什么"，改了会失真。

---

## 四、MCP 版本升级流程

```python
# 第一步：查有没有新版（在对话里调用）
mcp__bilibili-mcp__check_mcp_update
```

### 情况 A：新版已修好问题一/问题二 → 最理想

1. 改客户端 MCP 配置里该包的版本号（配置文件位置见仓库根目录 `INSTALL.md` 对照表，如 `~/.workbuddy/mcp.json`、`~/.claude.json` 等）
2. **不要**再跑 `apply-patch.py`（上游已修，补丁多余且会冲突）
3. 让用户重启客户端
4. 跑 `check_setup.py` 确认
5. 在 `CHANGELOG.md` 记录：「升级到 X.Y.Z，上游已修 N 个问题，补丁退役」

### 情况 B：新版没修 → 需要重新适配补丁

**必须重新读新版源码，不能直接套用旧补丁文件。**

1. 刷新 pristine：`python assets/mcp-patch/build-patch-index.py fetch <新版本号>` → 自动从 npm tarball 取全部目标文件
2. **逐个对比新版源码**，定位对应函数，把修复重新写进 `patched/` 下对应文件：
   - 问题一：`getVideoInfo()`（在 `dist/bilibili/video-api.js`）
   - 问题二：`getComments()` 的分页循环（在 `dist/bilibili/comments.js`）+ 参数构建（`comments-api.js`）
   - 问题三：`limit` 上限（`dist/utils/validation.js` + `dist/server/tool-schemas.js`）
3. 重写索引：`python assets/mcp-patch/build-patch-index.py finalize <新版本号>`（会校验两份快照齐全且确有差异，并重算 sha256）
4. 重跑验证（下节清单）
5. 在 `CHANGELOG.md` 记录

> **`apply-patch.py` 有版本守卫**：包版本不等于 `patch-index.json` 里的版本时会直接拒绝动手。这是刻意设计——避免旧补丁覆盖已修好的新版。

---

## 五、改动后的验证清单（必做）

```bash
S="<本 skill 所在目录>"   # 按实际路径替换；WorkBuddy 默认在 ~/.workbuddy/skills/，Claude Code 默认在 ~/.claude/skills/

# 1) 补丁状态
python $S/assets/mcp-patch/apply-patch.py --check

# 2) 语法校验（改了 JS 补丁才需要）
node --check <被改的 .js 文件>

# 3) 前置自检
python $S/scripts/check_setup.py

# 4) 取数脚本冒烟测试（改了 fetch_video.py 才需要）
python $S/scripts/fetch_video.py BV1r2ey6EEh5 --comments 20 --out /tmp/skilltest

# 5) **GBK 崩溃扫描 + 管道下验证 exit code**（改了任何 print 之后必做）
#    Windows 下 stdout 被管道接走时用系统 ANSI 代码页 GBK（不是 chcp 的 65001），
#    编不了 ✅ ⚠ ⇒ 等字符 → 抛 UnicodeEncodeError → 脚本"假失败"（exit 1 但活儿早干完了）
python $S/scripts/check_console_encoding.py
# 上面的命令应输出 [OK]。它只查**运行期字符串**（docstring 里的特殊符号不用管）。
# 再**不设 PYTHONIOENCODING** 直接跑一次，看 exit code 是不是 0：
#   不设 PYTHONIOENCODING=utf-8 python $S/scripts/check_setup.py ; echo $?
# 人 terminals 里跑通不算数——终端是 UTF-8，只有管道会触发。

# 6) 打包校验
python <skill-creator>/scripts/package_skill.py $S <输出目录>
```

**凡改动涉及 MCP 包或 `mcp.json`，都必须提醒用户重启客户端**——运行中的进程不会热加载。

---

## 六、CHANGELOG 记录规范

每次改动追加一条，格式固定：

```markdown
## YYYY-MM-DD · <一句话说明>

**类型**：修复 / 新增 / 重构 / 名称变更 / 升级适配

**改了什么**：<文件 + 具体改动>

**为什么**：<触发原因，比如"B站调整了接口"、"用户要求加一节">

**怎么验证的**：<实测命令 + 结果，必须是实测而非"应该没问题">

**遗留**：<已知未解决的问题；没有就写"无">
```

**要求**：验证方式必须写**实测证据**（命令 + 实际输出），不要写"理论可行"。这是这个知识库最有价值的部分——未来排查时能判断"这个结论还成不成立"。

---

## 七、架构决策记录（为什么这样设计）

| 决策 | 理由 |
|---|---|
| **补丁而不是 fork** | 改动仅 2.3%，fork 会失去上游更新；补丁可一键还原 |
| **整文件替换而非字符串替换** | 字符串替换依赖精确匹配，包一升级就失效；整文件替换 + 版本守卫更稳 |
| **保留 pristine 原始文件** | 能一键还原成官方版本，回滚零风险 |
| **自带 `fetch_video.py` 作取数主力** | ① MCP 全挂了仍能完成分析；② 突破 MCP 的评论条数上限；③ **MCP 拿不到评论图片/等级/属地**，而"要不要读图"的判据依赖它们（2026-10-19 起评论默认走 B 通道） |
| **知识库写进 skill 而不是对话** | 对话会丢，skill 文件会留。未来维护者（包括未来的我）读文件即可恢复全部上下文 |
