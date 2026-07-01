---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
---

# 删除视频号相关功能

## 背景
视频号 TikHub API 数据过期，内容不可靠，且无法访问。彻底移除相关代码和预留占位，避免误导。

## 变更范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `collectors/wechat_video.py` | 删除整个文件 | 视频号采集器，依赖 TiHub API |
| `publishers/generate_wall.py` | 移除 wechat_video 条目 | pb_list 平台过滤器第 192 行 |
| `publishers/templates/wall.html` | 移除 wechat_video CSS 规则 | 第 27-35 行的 `data-platform="wechat_video"` 样式 |
| `CLAUDE.md` | 更新路线图阶段4 | "视频号 TikHub API ⏸暂停" → "❌已移除" |

## 不需要变更
- `run_topic.py` — 没有 import wechat_video
- `topics/registry.py` — 没有 wechat_video CollectorDef
- `schedule.yaml` — 没有视频号相关配置
- `.env` — TIKHUB 配置留着不影响功能（删不删随用户）

## 验证
- `python run_topic.py --skip-collect --skip-tag --topic funny` 仍可正常运行
- 无 ImportError、无 NameError
