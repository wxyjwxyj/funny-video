# 搞笑视频聚合墙

## 项目概述

每天自动从 B站/抖音/小红书采集搞笑视频 → 去重 → AI 打标签筛选 → 聚合成可刷视频墙。**个人自用**,只聚合 + 展示 + 内嵌/跳转播放,不分发、不二创、不去水印。

## 主链路

```
collectors/bilibili.py（公开 API，无需 CDP）   ┐
collectors/douyin.py（CDP 复用登录态）         ├→ video.db
collectors/xiaohongshu.py（CDP，阶段三）       ┘
  → pipeline/dedup.py（content_hash 去重）
  → pipeline/tagging.py（Claude 打标签 + funny_score）
  → publishers/generate_wall.py（视频墙 HTML）
  → scripts/scheduler.py（schedule.yaml 定时）
```

## 目录约定

| 目录 | 用途 | 入 Git？ |
|------|------|---------|
| `collectors/` | 各平台采集脚本（一平台一文件） | 是 |
| `utils/` | 底层能力（DB/HTTP/Claude/config/log/errors） | 是 |
| `pipeline/` | 去重 + 打标签流水线 | 是 |
| `storage/` | DB 操作层 + schema.sql | 是 |
| `publishers/` | 视频墙生成 | 是 |
| `publishers/templates/` | CSS/JS 原文（不内嵌 Python f-string） | 是 |
| `scripts/` | 独立工具（scheduler 等） | 是 |
| `tests/` | 测试 | 是 |
| `.env` | 密钥（API Key 等） | **否** |
| `video.db` | SQLite 数据库 | 否 |

## 数据模型

`videos` 表，冲突键 `content_hash UNIQUE`（**不用 page_url，url 会变**）。完整 schema 见 `storage/schema.sql`。平台特有字段塞 `extra` JSON，不为单平台开列。`is_liked`/`is_watched` 是个人交互态。

## 开发规范

### 约束先行
新目录先定结构约定，新模块先想职责边界。需要调整规范时先改本文件、再改实践。

### 复用 news1，不重造
底层能力从 `../news1/` 搬：`utils/claude.py`（打标签统一入口）、`utils/http.py`（retry_session）、`utils/config.py`、`utils/log.py`、`utils/errors.py`、`storage/db.py`（WAL + busy_timeout）。**不要直接 `import anthropic` 创建 client，统一走 `utils/claude.py`。**

### Claude 调用约定（沿用 news1 踩坑结论）
- prompt 指令用**全英文**（中文/"You are..." 句式会被代理拦截），输出内容可中文
- 结构化输出用 `claude_call_tool()`（tool_use + thinking disabled），**不要**用 "Output ONLY JSON" + 文本解析
- `_build_prompt()` 不需要 JSON 格式示范 —— tool_use 自动约束输出结构
- batch tagging 用 `claude_call_tool`，`_call_batch` 返回 `list[dict | None]`，None=未收到结果（保持 DB NULL 待重试），不预填 fallback 值
- prompt 里不写 `"<placeholder>"` / `"..."` 形似字面串的占位符

### MiMo 配置（和 news1 一样）
.env 三字段直连，不走 cc-switch 多 provider 代理（部分 provider 不支持 tool_use）：
```
ANTHROPIC_API_KEY=tp-xxx
ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic
ANTHROPIC_MODEL=mimo-v2.5-pro
```

### 采集约定
- 一平台一 collector 文件，网络请求统一走 `retry_session()`
- B站直连公开 API（带 `Referer: bilibili.com`），抖音/小红书走 CDP 复用登录态
- **不逆向平台签名**（a_bogus/x-s 等），个人自用复用登录态成本最低

### 代码规范
- 注释用中文，docstring/类型标注按 `~/.claude/docs/` 通用规范
- 大文件（>200行）分段读取
- 密钥、token 不进代码，统一 `.env`

### 开发工作流
1. 需求确认 → 2. 方案设计（跨文件改动先列方案）→ 3. 小步开发 → 4. 收尾（测试→Code Review→提交）
每步完成再进下一步，任何一步失败先停下修复。

## 关键配置

- CDP Proxy：`http://localhost:3456`
- 敏感配置：`.env`（不进 Git）
- Claude 模型/Key/BaseURL：从 `.env` 读（`ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`）

## 边界（明确不做）

- ❌ 视频号采集（封闭 + 维护成本最高）
- ❌ 签名逆向
- ❌ 去水印 / 下载分发 / 二创素材导出
- ❌ 公开发布

## 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 项目骨架 + schema + 搬 utils | ✅ 完成 |
| 1 | B站全链路（采集→去重→打标签→视频墙） | ✅ 完成 |
| 2 | 抖音 CDP 采集 | ✅ 完成（推荐流→搜索接口，3词限速） |
| 3 | 小红书 CDP DOM抓取 + 定时自动化 | ✅ 完成 |
| 4 | 视频号 TikHub API | ❌ 已删除（API数据过期，无法访问，代码已移除） |
| 5 | AI视频墙（B站搜索 CDP + 独立 topic） | ✅ 完成 |
| 6 | GitHub Pages 上线 | ✅ 完成（wxyjwxyj.github.io/funny-video） |

## 当前采集链路

| 采集器 | 方式 | 关键词/来源 | 条数/次 |
|--------|------|------------|--------|
| `collectors/bilibili.py` | 公开热门API + CDP搜索 | B站两大入口统一文件 |

## 运行命令

```bash
# 搞笑视频墙（B站+抖音+小红书）
python run_topic.py --topic funny

# AI视频墙
python run_topic.py --topic ai

# 定时调度（每6小时）
python scripts/scheduler.py --interval 6
```

## 输出文件

| 文件 | 说明 |
|------|------|
| `index.html` | 主页入口 |
| `wall.html` | 今日搞笑视频墙 |
| `ai_wall.html` | 今日AI视频墙 |
| `archive/` | 搞笑视频历史归档 |
| `ai_archive/` | AI视频历史归档 |

详见 `PROJECT_PLAN.md`。
