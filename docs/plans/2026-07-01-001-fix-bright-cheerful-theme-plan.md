---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-plan-bootstrap
---

# fix: 视频墙改为欢快明亮风格（参考 news1）

**日期：** 2026-07-01
**类型：** fix
**深度：** Standard

---

## Goal Capsule

搞笑视频聚合墙的定位是「让人每天开心一刻」，但现有页面使用近黑底色（`#0a0a0f`）的暗黑配色，与内容调性严重不符。目标是将页面改为明亮欢快的浅色系（参考 news1 的轻盈风格），让用户一打开就感受到轻松氛围。

---

## Problem Frame

- `publishers/templates/wall.html` 和 `index.html` 的 CSS 变量全部基于深色底（`--bg: #0a0a0f`），卡片、过滤器、header 均为暗色系
- news1（`../news1/today.html`）的风格：亮灰底 `#f0f2f5`、白色卡片、紫蓝渐变 header、轻阴影、深色文字——整体明亮清爽
- 两份文件都用 CSS 自定义属性集中管理颜色，修改代价低、不影响 Python 逻辑

---

## Requirements

- R1：页面背景改为浅色（亮灰或暖白），不再使用近黑底
- R2：卡片背景为白色或极浅色，配轻阴影代替深色边框
- R3：Header 使用渐变色彩（参考 news1 的 `#667eea→#764ba2` 或更活泼的暖色系），而非黑色透明
- R4：正文文字为深色（`#1a1a1a`/`#333`），次要文字为中灰
- R5：过滤按钮激活态保持彩色（B站粉/抖音红），但底色改为浅色系
- R6：`index.html` 与 `wall.html` 视觉风格统一
- R7：不修改 Python 逻辑，不修改 HTML 结构，仅修改 CSS 变量和少量样式规则

---

## Key Technical Decisions

**KTD-1：配色方案选择**
选用「暖白 + 活泼渐变 header」方案，而非纯 news1 的紫蓝冷色系：
- 页面底：`#f7f8fc`（略带蓝调的浅灰，避免纯白刺眼）
- 卡片：`#ffffff`，阴影 `rgba(0,0,0,0.06)`
- Header 渐变：`#ff7c7c → #ffa34d`（橙红暖色，呼应B站/搞笑基调）或保守选 news1 同款 `#667eea → #764ba2`
  - 决策：**用 news1 同款蓝紫渐变**，更有设计感、与平台品牌色不冲突，用户描述的参照就是 news1
- 强调色（accent）：保留 B站粉 `#fb7299`，在浅底上更鲜亮
- 边框：`#e8eaed`（极浅灰）
- 文字主色：`#1a1a2e`；次色：`#6b7280`；弱色：`#9ca3af`

**KTD-2：backdrop-filter 在浅色页面的处理**
- Header/过滤栏 sticky 时用 `rgba(255,255,255, 0.9)` + `backdrop-filter: blur` 替代黑色透明底
- 播放弹窗遮罩从 `rgba(0,0,0,.88)` 改为 `rgba(15,15,30,.75)`，保持足够对比度

**KTD-3：卡片 hover 效果调整**
- 暗色主题下 hover 用黑色大阴影（`0 12px 40px rgba(0,0,0,.6)`），浅色主题改为彩色阴影（`0 8px 24px rgba(251,114,153,.18)`）
- 图片 scale 效果保留

---

## Implementation Units

### U1. 重写 `publishers/templates/wall.html` CSS 变量与相关规则

**Goal:** 将 wall 模板从暗色系切换到明亮欢快的浅色系

**Requirements:** R1, R2, R3, R4, R5, R7

**Dependencies:** 无

**Files:**
- `publishers/templates/wall.html`

**Approach:**
1. 替换 `:root` 变量块：
   - `--bg`: `#f7f8fc` → 浅灰底
   - `--surface`: `#ffffff` → 白色卡片
   - `--surface-2`: `#f3f4f8` → 次级面板
   - `--surface-3`: `#ebedf2` → 三级面板
   - `--border`: `#e8eaed`
   - `--text`: `#1a1a2e`
   - `--text-sub`: `#6b7280`
   - `--text-muted`: `#9ca3af`
   - `--accent` / `--bili`：保持 `#fb7299`，浅底下更鲜亮
2. Header：渐变背景 `linear-gradient(135deg, #667eea 0%, #764ba2 100%)`，文字改白色（`color: #fff`），sticky 时换为 `rgba(255,255,255,0.92)` + blur + 文字恢复深色
3. 过滤栏 sticky 背景：`rgba(247,248,252,0.95)` + blur
4. 卡片 box-shadow 替代 border：`0 1px 3px rgba(0,0,0,0.07), 0 2px 8px rgba(0,0,0,0.05)`；hover 阴影：`0 8px 24px rgba(251,114,153,0.18), 0 0 0 1px rgba(251,114,153,0.15)`
5. 平台角标 `background`：从 `rgba(0,0,0,.72)` 改为 `rgba(255,255,255,.9)`，文字用对应平台色
6. 评分徽章：`background` 从黑半透改为 `rgba(255,255,255,.9)`，分值颜色不变
7. 标签（`.tag`）：`background` 改 `#f0f2f5`，`border` 改 `#e8eaed`，文字 `#6b7280`
8. 空状态文字颜色适配浅底
9. 播放弹窗遮罩：`rgba(15,15,30,.78)`（深色保持遮罩效果）

**Patterns to follow:** news1 `../news1/today.html` `:root` 和 `.container` 的颜色规范

**Test scenarios:**
- 场景1（R1）：页面打开背景为浅灰，无近黑底色
- 场景2（R2）：视频卡片背景为白色，有轻阴影，无深色边框
- 场景3（R3）：Header 显示蓝紫渐变，文字清晰可读
- 场景4（R5）：B站按钮激活态为粉色底白字，抖音为红色，底部不再是黑色背景
- 场景5（R7）：Python 文件无修改（`git diff --name-only` 不含 `.py`）

**Verification:** 用浏览器打开 `funny_wall.html`，页面整体明亮，无暗黑感；hover 卡片有彩色光晕

---

### U2. 重写 `index.html` CSS 变量与 Hero 渐变

**Goal:** 首页与 wall 页风格统一，同样改为明亮欢快

**Requirements:** R1, R2, R3, R4, R6, R7

**Dependencies:** U1（配色方案确认后再改 index）

**Files:**
- `index.html`

**Approach:**
1. `:root` 变量替换：与 U1 完全一致
2. Hero 区：径向渐变从暗色 `rgba(251,114,153,.08)` 调整为 `radial-gradient(ellipse 70% 45% at 50% 0%, rgba(102,126,234,.12) 0%, transparent 65%)`（蓝紫淡晕）
3. `.live-badge` 背景改为白色/浅灰，文字深色
4. `.nav-card`：背景白色，轻阴影代替深色 border，hover 效果适配浅色系
5. Footer（如有）颜色适配

**Patterns to follow:** 与 U1 保持变量值完全一致，避免两页面颜色微差

**Test scenarios:**
- 场景1（R6）：`index.html` 和 `funny_wall.html` 并排开，背景色、卡片色视觉一致
- 场景2（R3）：Hero 区有轻蓝紫光晕，不是暗色光晕
- 场景3（R1/R4）：标题 `#1a1a2e`，页面背景 `#f7f8fc`

**Verification:** 用浏览器打开 `index.html`，整体与 wall 页面色调统一，感觉明亮友好

---

### U3. 重新生成 wall HTML 并归档

**Goal:** 用新模板重跑生成，确保 `funny_wall.html`、`ai_wall.html`、`funny_archive/` 用上新风格

**Requirements:** R1-R7 全部端到端验证

**Dependencies:** U1, U2

**Files:**
- `funny_wall.html`（生成产物）
- `ai_wall.html`（生成产物）
- `funny_archive/2026-07-01.html`（归档更新）
- `ai_archive/2026-07-01.html`（归档更新）

**Approach:**
```
python run_topic.py --topic funny
python run_topic.py --topic ai
```
仅重新生成 HTML，不重新采集和打标签（DB 已有今日数据）。

**Test scenarios:**
- 场景1：`funny_wall.html` 打开后页面明亮，无 `#0a0a0f` 字样出现在渲染结果中
- 场景2：卡片正常显示，过滤器可用，点击播放弹窗正常

**Verification:** 浏览器视觉检查；`grep -c '#0a0a0f' funny_wall.html` 返回 0

---

## Scope Boundaries

### In scope
- `publishers/templates/wall.html` CSS 变量和相关规则
- `index.html` CSS 变量和相关规则
- 重新生成 `funny_wall.html`、`ai_wall.html`、归档文件

### Deferred to Follow-Up Work
- `ai_wall.html` 是否需要独立的 AI 主题配色（当前与 funny 共用模板）
- archive index 页面（`funny_archive/index.html`、`ai_archive/index.html`）的同步美化

### Out of scope
- 任何 Python 逻辑修改
- 新增功能或布局结构调整
- 暗色模式（dark mode）支持

---

## Verification Contract

1. `funny_wall.html` 在浏览器中整体呈浅色，无暗黑感
2. `index.html` 与 wall 页色调一致
3. `grep -c '#0a0a0f' funny_wall.html` → 0
4. `grep -c '#0a0a0f' index.html` → 0
5. 过滤器、卡片 hover、播放弹窗交互正常

---

## Definition of Done

- [ ] U1 完成：`wall.html` 模板为浅色系
- [ ] U2 完成：`index.html` 同步浅色系
- [ ] U3 完成：生成产物通过视觉检查
- [ ] Verification Contract 5 条全部通过
