---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
---

# 前端重设计计划

## 目标
把现有"测试页面"级别的视频墙升级为视觉吸引力强、体验丝滑的产品级界面。

## 问题诊断
1. `index.html`：简单居中布局，缺乏视觉层次和品牌感
2. `wall.html` 模板：过滤器/卡片/播放器都是最低可用水平，无平台区分色、无动效、无空状态
3. archive index：简陋表格，无样式系统

## 改动范围

### U-1: `publishers/templates/wall.html`（最高优先级）
- 粘性 header + backdrop-blur，返回首页链接
- 粘性 filter 栏：评分分组 / 平台分组 / 分区分组，分隔线视觉区隔
- 卡片顶部平台色条（B站粉 / 抖音红 / 小红书红）
- 缩略图放大 hover 动效
- 评分徽章按分段着色（9+ 金色、8+ 绿色、7+ 灰色）
- 平台图标角标（左上角）
- 播放弹窗：backdrop-blur + scale 进场动画 + ESC 关闭 + 标题栏
- 空状态展示

### U-2: `index.html`
- 顶部 hero 区：径向渐变背景 + 呼吸动效绿点"每日自动更新"徽章
- 平台徽章展示
- 分区 section（搞笑内容 / AI 内容）
- 主入口卡片用 primary 样式（渐变 + 微发光边框）
- 底部 footer

### U-3: `publishers/generate_wall.py`
- `_render_card`：新增 platform-icon、score CSS 类
- `_update_archive_index`：重写为与设计系统一致的样式
- `_update_index_time`：更新 sub 文本的正则以适配新 HTML 结构

## 验证
- `python run_topic.py --topic funny` 重新生成，肉眼检查
- `python run_topic.py --topic ai` 同上
