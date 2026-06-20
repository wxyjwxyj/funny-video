# 搞笑视频聚合墙 —— 项目方案

## 一、定位

每天自动从 B站/抖音/小红书采集搞笑视频、AI 筛选后聚合成可刷视频墙的**个人自用**工具。

**核心目标:**
1. 一个页面刷到当天搞笑视频，不用切三个 App
2. AI 筛掉标题党，留真搞笑
3. 全自动，每天定时更新

**不做:** 视频号、签名逆向、去水印下载、二次分发、公开发布。

## 二、技术架构（复用 news1 地基）

```
collectors/  bilibili / douyin / xiaohongshu  ┐
   (B站公开API直连，抖音/小红书走CDP登录态)     ├→ video.db (SQLite/WAL)
   → 去重(content_hash) → Claude 打标签筛"搞笑" ┘
   → generate_wall.py → 视频墙 HTML（瀑布流+内嵌/跳转播放）
   → scheduler（schedule.yaml 定时）
```

**从 news1 搬:** `utils/{claude,http,config,log,errors}.py`、`storage/db.py`（WAL+busy_timeout）、CDP 接入方式、build_bundle 的并发打标签链路（ThreadPoolExecutor + claude_call_tool）。

**为什么独立建项目:** 数据模型/前端/采集逻辑完全不同，硬并进 news1 只会让两边都乱。共享的是 utils 底层能力。

## 三、各平台采集

### B站（阶段一，最简单）
- 走公开 web API，**不用 CDP**，直连更稳更快
- 数据源：热门/排行榜（按分区 rid），搞笑相关：鬼畜区、生活区搞笑子区
- 防盗链：请求带 `Referer: bilibili.com`
- 播放：官方 iframe player 内嵌，**不下载、不碰加密流**
- ⚠️ 具体 endpoint 和 WBI 签名要求实现阶段逐个实测，不写死可能过时的细节

### 抖音（阶段二）
- CDP attach 已登录 web tab，拦截推荐/搜索流 XHR 拿列表
- 不逆向 a_bogus，复用登录态成本最低
- 直链有时效：点的时候现拿或按需下载本地

### 小红书（阶段三，按需）
- 同抖音走 CDP，视频笔记少+风控重，看阶段二体验再决定

## 四、"搞笑"筛选

1. 初筛（免费）：分区/标签/关键词（B站直接有鬼畜区）
2. 精筛（Claude）：批量打标签 + funny_score(0-10)，复用 ThreadPoolExecutor 并发 + claude_call_tool
3. 视频墙默认只展示 funny_score >= 阈值

## 五、前端视频墙

`frontend-design` skill 生成。瀑布流 grid：封面+时长角标+标题+作者+平台标识+搞笑分。点击播放（B站内嵌 iframe，抖音/小红书跳转或本地）。筛选条：平台/分区/隐藏已看。交互：喜欢、标记已看（写回 DB）。CSS/JS 抽到 templates/ 不内嵌 f-string。

## 六、路线图

| 阶段 | 内容 | 工作量 | 产出 |
|------|------|--------|------|
| 0 | 骨架+CLAUDE.md+schema+搬 utils | 0.5天 | 项目可运行 |
| 1 | B站全链路 | 3-4天 | **能天天刷的产品** |
| 2 | 抖音 CDP 采集 | 2-3天 | 双平台 |
| 3 | 小红书+定时自动化 | 按需 | 全自动三平台 |

每阶段独立可用，阶段一跑通即有价值。

## 七、风险

| 风险 | 应对 |
|------|------|
| 接口/签名变更 | B站最稳；抖音走 CDP 降低破裂；维护控制在个人够用 |
| CDP 登录态过期 | 复用 news1 错误处理（exit code 模式） |
| 版权 | 个人自用、不分发、内嵌/跳转为主；绝不公开发布或导出二创素材 |
| 打标签成本 | 量可控，初筛后再打标签，复用 429 重试 |
