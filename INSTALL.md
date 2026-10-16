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

把整个仓库目录放进你所用的 Agent 的 skills 目录：

| 客户端 | skills 目录 |
|---|---|
| **WorkBuddy** | `~/.workbuddy/skills/` |
| **Claude Code** | `~/.claude/skills/` |
| 其他 | 见对应客户端文档（即"技能 / Skills"的存放目录） |

> Windows 下 `~` 即 `C:\Users\<你的用户名>`；macOS / Linux 同理。

**方式 A：git clone（推荐，后续一条命令即可更新）**

```bash
git clone https://github.com/DavidLearning1023/bilibili-video-analysis-summary.git \
  ~/.workbuddy/skills/bilibili-video-analysis-summary
```

**方式 B：下载 zip**，解压后把整个文件夹放进上面的目录。

放置完成后**重启或重连一次客户端**，让它扫描到新 skill。之后在对话里发一个 B站链接，看看是否触发。

## 三、配置 MCP 通道（第二步，可选但推荐）

> 跳过本步也能用——见第六节「最简路径」。但配置好后，字幕获取、关键词定位、章节等功能会更顺。

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

| 能力 | 最简路径 | 装了 MCP |
|---|---|---|
| 元数据 + 评论 | ✅ | ✅ |
| 字幕 | 需自备登录凭据（无扫码向导） | ✅ 扫码即可 |
| 关键词定位 / 章节 / 收藏夹 | ❌ | ✅ |
| 评论条数上限 | 不限 | 补丁后 300 条主评论 |

## 七、更新 / 卸载

**更新**（方式 A 安装的）：

```bash
cd <skill 目录> && git pull
```

更新后若改了 `scripts/` 或 `assets/`，重跑一次自检即可；若上游 MCP 包有升级，见 `references/maintenance.md` 第四节。

**卸载**：删除 skill 目录；补丁可先还原再删（`python assets/mcp-patch/apply-patch.py --revert`）。
