# MCP 工具速查（`bilibili-mcp`）

服务名：`bilibili-mcp`　｜　本次验证版本：`1.14.1`　｜　要求 Node ≥ 20

> ⚠️ 该包存在三处影响实际使用的问题，**未打补丁时会出错**。运行 `python scripts/check_setup.py` 自检，处理方式见 `troubleshooting.md`。

## 工具一览

| 工具 | 用途 |
|---|---|
| `check_bilibili_credentials` | 验证凭据是否已配置**且登录有效**（唯一能证明登录态的手段） |
| `get_video_metadata` | 标题 / UP主 / 时长 / 发布日期 / 分P列表 / 统计 |
| `get_video_transcript` | **字幕全文、关键词定位、时间段筛选（核心工具）** |
| `get_video_comments` | 评论与回复（热度或时间排序） |
| `get_video_chapters` | 章节 / 进度条分段 |
| `get_video_info` | 字幕优先的上下文（无字幕时返回简介+标签） |
| `search_bilibili_videos` | 按关键词搜视频（最多 10 条） |
| `search_bilibili_creators` / `get_bilibili_creator_content` | 搜 UP 主 / 读 UP 主内容 |
| `list_bilibili_favorite_videos` | 遍历自己账号的收藏夹 |
| `check_mcp_update` | 检查包是否有新版 |
| `get_credential_setup_instructions` | 返回凭据配置指引（不泄露 Cookie） |

## `get_video_transcript` 关键参数

| 参数 | 说明 |
|---|---|
| `bvid_or_url` | BV 号或完整 URL（必填） |
| `page` | 分P 编号，从 1 开始。**多P 视频必须指定，否则只读默认P** |
| `include_timestamps` | 每行加 `[HH:MM:SS --> HH:MM:SS]` 前缀 |
| `query` + `max_matches`(1–20) + `context_segments`(0–5) | 关键词定位，**返回带 `timestamp_url` 可直达播放时刻** |
| `start_seconds` / `end_seconds` | 时间段过滤 |
| `preferred_lang` | `zh-Hans`(默认) / `zh-CN` / `zh-Hant` / `en` / `ja` / `ko` / `ai-zh` |
| `exclude_ai_subtitles` | 排除 B站 AI 字幕，只要人工字幕 |
| `fallback_to_asr` / `force_asr` | 无字幕时用本地 ASR（**默认关**，需先装模型） |
| `fallback_to_description` | 无字幕时降级为视频简介（与时间戳/区间过滤互斥） |

**返回的关键字段**：`data_source`（`subtitle` / `ai_subtitle` / `asr`）、`language`、`source_url`、每处命中的 `timestamp_url`。

## `get_video_comments` 关键参数

| 参数 | 说明 |
|---|---|
| `sort` | `hot`（默认，按热度）/ `time` |
| `detail_level` | `brief`（10 条）/ `detailed`（20 条 + 回复） |
| `limit` | 主评论数量。**补丁后上限 300**（原版 50） |
| `include_replies` | 是否含高赞回复，默认 `true` |

**语义提醒**：`limit` 是**主评论**数量；`include_replies=true` 时扁平 `comments[]` 还包含楼中楼回复，**所以总条数会超过 `limit`**。实测 `limit=50` 约得到 107 条、`limit=150` 约得到 268 条（含楼中楼）。

## 已知坑（都在补丁里处理过）

1. **`/x/web-interface/view` 不带 WBI 签名会被风控 412**
   实测某境外出口 IP：非 WBI 变体一律 412，WBI 变体 200。该接口是**所有视频类工具的共同上游**，所以一处失效全线崩。已改为 WBI 优先 + 412 降级。

2. **评论接口的 `pn` 页码参数已废弃**
   传 `pn` 会被忽略，每次调用返回同一批数据 → 高 `limit` 时结果成倍重复（实测 `limit=50` 返回 112 条里大量重复，`total_comments` 虚高）。必须用 `cursor.next` 游标翻页。已修复并加 rpid 去重。

3. **`ps` 单页上限是 30**，不是 20。

4. **`doctor --json` 不能证明登录有效**
   它只检查本机配置（文件在不在、能否读出），不实际请求 B站。**只有 `check_bilibili_credentials` 能证明 `logged_in: true`**。

5. **中文 AI 字幕会把"长上下文"转成"常识"**等多处误转，详见 SKILL.md 第 3 步。

## 可调环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `BILIBILI_RATE_LIMIT_MS` | 500 | 请求间隔，风控严时调大 |
| `BILIBILI_REQUEST_TIMEOUT_MS` | 10000 | 请求超时 |
| `BILIBILI_CACHE_SIZE` | 100 | 缓存条数 |
| `BILIBILI_MCP_DEBUG` | — | 设为 `1` 输出调试日志到 stderr |
| `BILIBILI_SESSDATA` / `BILIBILI_BILI_JCT` / `BILIBILI_DEDEUSERID` | — | 用环境变量提供凭据 |

环境变量在 **MCP server 进程启动时读取**，改完需重启客户端。
