# 安装与首次使用

> 本文档同时写给**人**和**AI Agent**。把整个仓库目录放进 Agent 的 skills 目录后，AI 会自动读取本文件的内容来完成引导——你也可以自己照着走。

---

## 一、前置要求

| 项 | 要求 | 说明 |
|---|---|---|
| Agent 客户端 | 支持 `SKILL.md` 规范即可 | WorkBuddy、Claude Code 等（本项目按通用规范编写，不绑定客户端） |
| Python | 3.9+ | 核心脚本**只用标准库**，无需 pip 安装任何依赖 |
| Node.js | ≥ 20 | 仅当使用 MCP 通道时需要（获取字幕更省事） |
| B站账号 | 有即可 | 仅当要读**字幕**时需要（手机 App 扫码登录） |
| Playwright | 可选 | 仅当要**自动生成 PDF** 时需要 |

## 二、放置 skill（第一步）

> **先搞清两层结构**（这是 2026-10-20 起的模型）：
>
> | | 在哪 | 谁写它 |
> |---|---|---|
> | **真源** | 项目仓库目录（git 仓库） | **你 / Agent**，所有改动落这里 |
> | **部署副本** | 客户端的 skills 目录 | **脚本覆盖**，你不要手改 |
>
> 客户端读的是**部署副本**。两层内容不一致时，客户端读到的是旧规则——所以要用同步工具。

### 方式 A：真源 + 部署（推荐）

```bash
# ① 克隆到你的项目目录
git clone https://github.com/DavidLearning1023/bilibili-video-analysis-summary.git \
  ~/projects/bilibili-video-analysis-summary

# ② 部署到各客户端的 skills 目录（自动发现已装的客户端）
cd ~/projects/bilibili-video-analysis-summary
python scripts/sync_skill.py --deploy
```

`sync_skill.py` 会自动找到本机已存在的客户端 skills 目录并同步过去：

| 客户端 | 部署位置 |
|---|---|
| **MiniMax Code** | `~/.minimax/skills/bilibili-video-analysis-summary` |
| **WorkBuddy** | `~/.workbuddy/skills/bilibili-video-analysis-summary` |
| **Claude Code** | `~/.claude/skills/bilibili-video-analysis-summary` |
| 其他 | 见对应客户端文档（把 `<name>` 换成实际目录名即可） |

手动指定某一个目标：`python scripts/sync_skill.py --deploy --target <目录>`。

> **为什么不用 `git pull` 直接更到客户端目录**：Agent 直接接管项目文件夹，**本地状态才是最新的**
> （含还没提交的改动）。从 GitHub 拉会把本地已做但未推送的改动**悄悄回滚掉**。
> 方向永远是 **本地真源 → 部署副本**。
>
> 同步的粒度是**文件内容**（SHA256），不是 git 提交——真源改了没提交也能同步过去。

### 方式 B：junction 直连（Windows · 零漂移）

让客户端的 skills 目录**直接指向真源**，客户端读到的就是项目目录本体——**结构上不可能漂移**，
改了立刻生效，连同步都省了：

```powershell
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.minimax\skills\bilibili-video-analysis-summary" `
  -Target "$env:USERPROFILE\projects\bilibili-video-analysis-summary"
```

每个客户端做一次即可。`sync_skill.py --check` 会识别出这种接法并显示
`[无需同步] 目标就是真源本身`。

> 两种接法**可以混用**——同一个 skill 在 MiniMax Code 用 junction、在别的客户端用拷贝都行，
> 脚本会逐个识别。junction 是 Windows 限定；macOS / Linux 用 `ln -s`。
>
> ⚠️ junction 的代价：真源目录一旦移动或改名，链接就断（重建即可）。

### 方式 B：只要副本（不想管仓库）

```bash
git clone https://github.com/DavidLearning1023/bilibili-video-analysis-summary.git \
  ~/.minimax/skills/bilibili-video-analysis-summary
# Claude Code 用户改放 ~/.claude/skills/，macOS / Linux 同理
```

这种装法**只能读、不能改**——要改就改真源，再 `sync_skill.py --deploy` 送过去。

放置完成后**重启或重连一次客户端**，让它扫描到新 skill。之后在对话里发一个 B站链接，看看是否触发。

## 二之二、每次用之前：先查漂移

```bash
cd <项目仓库目录>
python scripts/sync_skill.py --check
```

| 输出 | 含义 | 动作 |
|---|---|---|
| `[已同步]` | 副本是最新的 | 继续 |
| `[需要同步]` + 退出码 1 | 真源有改动没送过去 | 跑 `--deploy`，然后重读 SKILL.md |

> 退出码 1 表示**发现漂移**，不是脚本出错。

## 三、配置 MCP 通道（第二步 · 推荐）

> **为什么推荐装**：① 扫码登录一次，凭据全通道通用（`fetch_video.py` 也会自动复用它的凭据文件）；
> ② 多出字幕关键词定位、时间段筛选、章节、收藏夹、UP 主内容等功能；
> ③ 配套的 bug 补丁已随本仓库提供，照着打一次即可。
> 不装也能完成核心分析——见第六节「最简路径」的能力对照表，但体验会打折扣。

**1. 安装并登录**

```bash
npx.cmd -y @xzxzzx/bilibili-mcp@1.14.1 setup    # Windows PowerShell 用 npx.cmd，CMD / macOS / Linux 用 npx
```

菜单按回车选**扫码登录** → 手机 B站 App 扫码确认 → 问是否安装 ASR 模型时**选否**（默认即可）。

**2. 写入 MCP 配置**——各客户端配置文件位置：

| 客户端 | MCP 配置文件 |
|---|---|
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

## 四、自检（第三步）

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

## 五、首次使用引导（给 AI 的清单）

> 如果你是 AI Agent，在用户**首次使用**本 skill（或询问"怎么用 / 怎么配置 / 能做什么"）时，按此清单引导，**不要跳过自检直接分析视频**。

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

## 七、更新 / 卸载

**更新**（真源有改动后）：

```bash
cd <项目仓库目录>
python scripts/sync_skill.py --check      # 先看漂移
python scripts/sync_skill.py --deploy     # 再同步到各客户端
```

改了 `scripts/` 或 `assets/` 的，同步后重跑一次 `check_setup.py`；
若上游 MCP 包有升级，见 `references/maintenance.md` 第四节。

**卸载**：删除各客户端 skills 目录下的同名文件夹；补丁可先还原再删
（`python assets/mcp-patch/apply-patch.py --revert`）。
