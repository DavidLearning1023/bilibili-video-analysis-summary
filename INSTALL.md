# 安装与首次使用

> 本文档同时写给**人**和**AI Agent**。把整个仓库目录放进 Agent 的 skills 目录后，AI 会自动读取本文件的内容来完成引导——你也可以自己照着走。
>
> **这是 skill，不是 MCP。** 见下面「这是什么 / 不是什么」。

---

## 〇、这是什么 / 不是什么（先看这个，免得后面看混）

| | 是什么 | 在哪 | 谁维护 |
|---|---|---|---|
| **本项目** | 一个 **Agent Skill**（`SKILL.md` 规范）：给 Agent 读的规则集 + 取数脚本 + 报告模板 | 装在 Agent 的 **skills 目录** | 上游仓库（GitHub） |
| **上游 MCP** | `@xzxzzx/bilibili-mcp@1.14.1`，**第三方**的 MCP 服务器（Apache-2.0） | 跑在 npm 缓存里，用 npx 启动 | 第三方作者 |
| **本仓库的补丁** | 一套**可逆补丁**，修上游 MCP 已知的 3 个 bug（412 风控 / 评论分页 / 条数上限） | `assets/mcp-patch/` | 本项目 |
| **议题级姊妹项目** | `bilibili-info-gathering`：**另一个 skill**，做「一个议题」的广扫 + 交叉验证，产物是结论 + 信源地图（**不是**报告） | 装在 Agent 的 skills 目录 | 同一个作者 |

**一句话**：**skill 是给 Agent 读的规则，MCP 是它可以调用的外部服务，补丁是让这个外部服务少犯错的工具链。**

所以：

- 你装的是 **skill**（一个文件夹），**不是**一个 MCP server——别去 MCP 配置里填这个仓库的路径
- MCP 是**可选增强**（字幕、关键词定位、章节、收藏夹），不装也能用（见第六节「最简路径」）
- 本仓库**不包含** MCP 的代码，只有它的补丁

### 什么时候用哪个（两个 B站 skill 的分工）

| | 本项目 | `bilibili-info-gathering` |
|---|---|---|
| 输入 | **一条**视频（BV/AV/链接） | **一个议题**（「在B站了解 X」） |
| 产物 | 单文件 HTML 报告 + 同源 PDF | 结论 + 信源地图（可选 markdown 简报） |
| 典型说法 | 「帮我分析总结这个视频，链接：xxx」 | 「帮我在B站了解 X」 |

> ⚠️ **别把议题级任务接到本项目上来。** 用户问「B站上关于 X 有什么」时，
> 正确做法是走 `bilibili-info-gathering`——它做广扫、双轴评级、交叉验证，
> 产出的是**结论 + 信源地图**。本项目只负责把**一条**视频榨干并出报告。

---

## 一、前置要求

| 项 | 要求 | 说明 |
|---|---|---|
| Agent 客户端 | 支持 `SKILL.md` 规范即可 | MiniMax Code / WorkBuddy / Claude Code 等（本项目按通用规范编写，不绑定客户端） |
| Python | 3.9+ | 核心脚本**只用标准库**，无需 pip 安装任何依赖 |
| Node.js | ≥ 20 | **仅当**要配 MCP 通道时需要（获取字幕更省事） |
| B站账号 | 有即可 | **仅当**要读**字幕**时需要（手机 App 扫码登录） |
| Playwright | 可选 | 仅当要**自动生成 PDF** 时需要 |
| ffmpeg / pymupdf | 可选 | 仅当要抽帧 / 做 PDF 版式自检时需要 |

---

## 二、使用者：把 skill 装进你的 Agent

> **这一节面向只想用的人。装完即用，不需要理解任何"真源/副本"的概念。**

### 2.1 装本体

**方式一：git clone（推荐，能随时更新）**

```bash
# 把 <skills 目录> 换成你客户端的对应位置
git clone https://github.com/DavidLearning1023/bilibili-video-analysis-summary.git \
  <skills 目录>/bilibili-video-analysis-summary
```

| 客户端 | skills 目录 |
|---|---|
| **MiniMax Code** | `~/.minimax/skills/` |
| **WorkBuddy** | `~/.workbuddy/skills/` |
| **Claude Code** | `~/.claude/skills/` |
| 其他 | 见对应客户端文档（"技能 / Skills"的存放目录） |

> Windows 下 `~` 即 `C:\Users\<你的用户名>`；macOS / Linux 同理。

**方式二：下载 zip**，解压后把整个文件夹放进同样的位置。

### 2.2 装完即用

1. **重启或重连一次客户端**，让它扫描到新 skill
2. 在对话里发一个 B站链接，看是否触发：

```
帮我分析总结这个 B站视频：https://www.bilibili.com/video/BVxxxxxxxxxx
```

3. （可选）配 MCP 通道拿字幕与更多能力 → 第四节

### 2.3 更新

```bash
cd <skills 目录>/bilibili-video-analysis-summary
git pull          # zip 用户：重新下载并覆盖
```

> ⚠️ **如果你的客户端是 MiniMax Code / WorkBuddy / Claude Code，且 skill 目录是通过
> **符号链接（junction / `ln -s`）**指向别处的**——那说明你是「维护者」，更新方式见第三节。

> **使用者不需要跑 `sync_skill.py`。** 你的 skill 目录就是唯一一份，也是最新的。

---

## 三、维护者：在项目目录里开发，再部署到各 Agent

> **这一节面向要改这份 skill 的人**（也就是上游仓库的维护者）。普通使用者跳过。

### 3.1 为什么需要这层

```
真源      项目仓库目录（git 仓库）              ← 唯一的编辑目标
             │  scripts/sync_skill.py --deploy
             ↓
部署副本   ~/.minimax/skills/<name>/     MiniMax Code 读这里
           ~/.workbuddy/skills/<name>/    WorkBuddy 读这里
```

| | 在哪 | 谁写它 |
|---|---|---|
| **真源** | 项目仓库目录（git 仓库，有 GitHub 远端） | **你 / Agent**，所有改动落这里 |
| **部署副本** | 各客户端 skills 目录下的同名文件夹 | **只读**，由脚本覆盖 |

**为什么不能从 GitHub 直接拉副本**：Agent 直接接管项目文件夹后，**本地项目目录才是最新的**
（含还没提交的改动）。从 GitHub 同步会把本地已做但未推送的改动**悄悄回滚掉**——
典型的静默错误。方向永远是 **本地真源 → 部署副本**。

### 3.2 接法一：junction / 符号链接直连（零漂移 · 推荐）

让客户端的 skills 目录**直接指向真源**，客户端读到的就是项目目录本体——
**结构上不可能漂移**，改了立刻生效，连同步都省了：

```powershell
# Windows
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.minimax\skills\bilibili-video-analysis-summary" `
  -Target "$env:USERPROFILE\projects\bilibili-video-analysis-summary"
```

```bash
# macOS / Linux
ln -s ~/projects/bilibili-video-analysis-summary \
      ~/.claude/skills/bilibili-video-analysis-summary
```

每个客户端做一次即可。`sync_skill.py --check` 会识别出这种接法并显示
`[无需同步] 目标就是源目录本身（junction）`。

> ⚠️ junction 的代价：真源目录一旦移动或改名，链接就断（重建即可）。

### 3.3 接法二：拷贝部署 + `sync_skill.py`

不想用链接（或者客户端不支持），就让脚本复制：

```bash
cd <项目仓库目录>
python scripts/sync_skill.py --deploy        # 同步到各已装客户端
python scripts/sync_skill.py --deploy --target <目录>   # 或指定某个目录（可指向尚不存在的）
```

脚本按 **SHA256 逐文件**比对，源清单用 `git ls-files --cached --others --exclude-standard`
（受管文件 + 未被忽略的未跟踪文件）——**真源改了没提交也能同步过去**。

`--prune` 会把「上游已删」的文件移入同盘暂存区并写 `_MANIFEST.md`（**不永久删除**，
由你把整个文件夹放进回收站）。

### 3.4 什么时候跑

| 时机 | 命令 |
|---|---|
| **动手改之前**（确认你读到的是最新版规则） | `python scripts/sync_skill.py --check` |
| **改完之后**（把改动送到各客户端） | `python scripts/sync_skill.py --deploy` |
| 客户端加载的还是旧规则 | 同步后**重启/重连客户端** |

> `--check` 退出码 1 = **发现漂移**，不是脚本出错。

> ⚠️ 脚本要**从项目仓库目录运行**（真源）。在已安装副本里跑会被挡住——
> 那是从副本往别处推，方向是反的。

---

## 四、配置 MCP 通道（可选增强）

> **为什么值得配**：① 扫码登录一次，凭据全通道通用（`fetch_video.py` 也会自动复用它的凭据文件）；
> ② 多出字幕关键词定位、时间段筛选、章节、收藏夹、UP 主内容等功能；
> ③ 配套的 bug 补丁已随本仓库提供，照着打一次即可。
> **不配也能完成核心分析**——见第六节「最简路径」的能力对照表，只是体验会打折扣。

**1. 安装并登录**

```bash
npx.cmd -y @xzxzzx/bilibili-mcp@1.14.1 setup    # Windows PowerShell 用 npx.cmd，CMD / macOS / Linux 用 npx
```

菜单按回车选**扫码登录** → 手机 B站 App 扫码确认 → 问是否安装 ASR 模型时**选否**（默认即可）。

**2. 写入 MCP 配置**——各客户端配置文件位置：

| 客户端 | MCP 配置文件 |
|---|---|
| MiniMax Code | 客户端的 MCP 设置（stdio 服务器，`npx.cmd` + `-y @xzxzzx/bilibili-mcp@1.14.1`） |
| WorkBuddy | `~/.workbuddy/mcp.json` |
| Claude Code | 项目内 `.mcp.json`，或 `claude mcp add` 命令 |
| 其他 | 见对应客户端文档（MCP servers 配置） |

写入内容（JSON 结构在各客户端基本一致）：

```json
{
  "mcpServers": {
    "bilibili-mcp": {
      "command": "npx.cmd",
      "args": ["-y", "@xzxzzx/bilibili-mcp@1.14.1"]
    }
  }
}
```

> - Windows 下 `command` 用 `npx.cmd` 而非 `npx`，避开 PowerShell 的 `.ps1` 执行策略坑。
> - 版本**建议 pin 死**（如上），不用 `@latest`——后者每次启动都要查 npm registry，网络抖动时可能起不来。
> - macOS / Linux 上 `command` 直接用 `npx`。

**3. 启用**

- **WorkBuddy**：连接器管理页 → 右上角「自定义连接器」→ 找到 `bilibili-mcp` → 点「信任」
- **其他客户端**：写好配置后重启客户端即可

**4. 打补丁（关键，否则大概率 412）**

上游包 `@xzxzzx/bilibili-mcp@1.14.1` 有三处影响实际使用的问题，本仓库自带可逆补丁：

```bash
cd <skill 目录>/assets/mcp-patch
python apply-patch.py --check    # 先看状态
python apply-patch.py            # 打补丁（整文件替换 + 版本守卫，可一键还原）
```

**5. 重启 / 重连客户端**（运行中的 MCP 进程不会热加载，这一步最容易漏）

---

## 五、自检

```bash
python <skill 目录>/scripts/check_setup.py
```

输出解读：

| 结果 | 含义 | 处理 |
|---|---|---|
| 全 `[OK]` | 就绪 | 直接开始用 |
| 补丁 `[FAIL]` | 补丁未打/丢失 | 重跑上面的 `apply-patch.py`，再重启客户端 |
| 凭据 `[WARN] 过期` | 登录态失效 | 重跑 `setup` 扫码 |
| 找不到包 | MCP 未装 | 重装，或忽略（走最简路径） |
| API 连通性 `[WARN]` | 网络问题 | 检查网络 / 代理 |

> **注意**：在 Windows 上，如果脚本输出被管道接走（Agent 跑脚本就是这种情况），
> Python 用的是系统 ANSI 代码页而不是控制台代码页。脚本里所有输出字符都刻意避开了
> 那个代码页编不了的符号，所以**不会**出现"检查全过却报 exit 1"的假失败。

---

## 六、最简路径（不装 MCP）

**只用 `scripts/fetch_video.py` 也能完成分析**：

```bash
python <skill 目录>/scripts/fetch_video.py BV1r2ey6EEh5 --comments 150 --no-subtitle   # 元数据 + 评论，无需登录
```

没有字幕时的完整三步（都无需 MCP）：

```bash
python <skill 目录>/scripts/fetch_media.py BV1r2ey6EEh5 --audio --out ./m          # ① 音轨
python <本地 ASR 脚本> m/audio.m4s --model large-v3 --language zh --formats srt     # ② 转写
python <skill 目录>/scripts/fetch_media.py BV1r2ey6EEh5 --video 1080 --out ./m     # ③ 要读画面数字才下
```

> `grab_frames.py` 依赖 **ffmpeg**（可用环境变量 `FFMPEG_BIN` 指定路径）；`check_pdf.py` 依赖 **pymupdf**。两者都是可选的，不做画面/版式核对时不装也能出报告。

| 能力 | 最简路径 | 装了 MCP |
|---|---|---|
| 元数据 + 评论 | ✅ | ✅ |
| 字幕 | 需自备登录凭据（无扫码向导） | ✅ 扫码即可 |
| 无字幕视频（音轨下载 + 本地转写） | ✅ `scripts/fetch_media.py`，无需 MCP | ✅ |
| 画面抽帧（抽帧 + 时间线清单） | ✅ `scripts/grab_frames.py`，需 ffmpeg | ✅ |
| PDF 版式自检 | ✅ `scripts/check_pdf.py`，需 pymupdf | ✅ |
| 关键词定位 / 章节 / 收藏夹 | ❌ | ✅ |
| 评论条数上限 | 不限 | 补丁后 300 条主评论 |

---

## 七、首次使用引导（给 AI 的清单）

> 如果你是 AI Agent，在用户**首次使用**本 skill（或询问"怎么用 / 怎么配置 / 能做什么"）时，按此清单引导，**不要跳过自检直接分析视频**。

0. **⚠️ 先判意图，再决定要不要往下走。** 用户的诉求是**议题**还是**一条视频**？

   | 用户说 | 该走哪 |
   |---|---|
   | 「帮我在B站了解 X」「B站上关于 X 有什么」「B站怎么看待 X」 | **议题级** → 姊妹项目 `bilibili-info-gathering`。**不要**引导用户"给我一个视频链接" |
   | 「帮我分析总结这个视频，链接：xxx」 | **单视频** → 本项目，继续第 1 步 |
   | 只在**背景/补充说明**里贴了 BV 号，但问题本身是议题 | **议题级** → 同上 |

   > 议题级任务走本项目是**做错方向**：本项目是纵向榨干一条视频，
   > 议题级要的是横向广扫 + 交叉验证 + 信源地图。

1. **跑自检**：`python <skill 目录>/scripts/check_setup.py`，把结果读给用户听（是 [OK] 还是需要处理的 [FAIL]）
2. **按结果分档引导**：
   - 全 `[OK]` → 宣告"环境就绪"，然后：
     - 用两三句话说明**能产出什么**（单文件 HTML 报告 + 同源 PDF；七部分结构；评论区聚类与三轴分布；AI 字幕纠偏……详见 `README.md`）
     - 给 1–2 个**可直接复制的使用示例**，例如：
       ```
       帮我分析总结这个 B站视频：https://www.bilibili.com/video/BVxxxxxxxxxx
       ```
     - 询问他想先分析哪个视频
   - 有 `[FAIL]` → 按 `references/troubleshooting.md` 的症状表逐项处理；用户不想装 MCP 时，直接说明"最简路径"（只用 `fetch_video.py`）完全可用
3. **顺手交代维护方式**：告诉用户本 skill 由 AI 维护、用户不需要手动改文件——想改结构/样式/类型指引，直接对 AI 说"把 X 改成 Y"即可（AI 按 `references/maintenance.md` 的速查表执行）
4. **（仅当用户装了两个 B站 skill）** 一句话交代分工：议题级归 `bilibili-info-gathering`，
   单条视频归本项目。**别让用户自己记这张表**——记住"他说的是议题就转走"。

---

## 八、更新 / 卸载

### 使用者

```bash
cd <skills 目录>/bilibili-video-analysis-summary
git pull          # zip 用户：重新下载并覆盖
```

更新后若改了 `scripts/` 或 `assets/`，重跑一次 `scripts/check_setup.py`。

### 维护者

```bash
cd <项目仓库目录>
python scripts/sync_skill.py --check      # 先看漂移
python scripts/sync_skill.py --deploy     # 再同步到各客户端
```

同步后客户端需要重启/重连才会加载新规则。

若上游 MCP 包有升级，见 `references/maintenance.md` 第四节。

### 卸载

删除各客户端 skills 目录下的同名文件夹；补丁可先还原再删
（`python assets/mcp-patch/apply-patch.py --revert`）。

---

## 九、常见安装问题

| 症状 | 原因 | 做法 |
|---|---|---|
| 客户端里看不到这个 skill | 装完没重启/重连 | 重启或重连客户端；确认目录名 = `bilibili-video-analysis-summary` |
| 装了但发 B站链接不触发 | 触发条件不满足 | 见 `SKILL.md`「触发边界」——**必须给链接或 BV 号**，只给文件/文字不会触发 |
| 报 412 / 评论结果重复 | 补丁没打 | `cd <skill 目录>/assets/mcp-patch && python apply-patch.py`，再重启客户端 |
| 补丁提示「版本不符」 | 上游 MCP 升级了，本仓库的补丁是 1.14.1 专用快照 | 见 `references/maintenance.md` 第四节；或先走最简路径（`fetch_video.py` 不依赖补丁） |
| 补丁莫名其妙失效了 | npm 重新下载了包，覆盖了补丁 | 重跑 `apply-patch.py`；`check_setup.py` 会替你发现 |
| `fetch_comment_media.py` 报缺 PIL | 当前 Python 没装 Pillow | 脚本会**自动**找一个带 PIL 的解释器重跑自己；指定用哪个：`set BILI_PYTHON=<解释器路径>` |
| 多个客户端都装了，想一起更新 | 各自是独立副本 | 使用者：在每个目录各 `git pull` 一次；维护者：`sync_skill.py --deploy` 一次送到所有客户端 |
