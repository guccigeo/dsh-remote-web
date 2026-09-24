# 移动端适配层详解

> 每次要动手机端布局，先读这一篇。**改之前先跑审计**（见文末），改完再跑一次做回归。

## 一、为什么会有这一层

DSH 的 Web GUI 是**桌面布局**：左侧图标轨道常驻、侧栏靠 grid 轨道占宽、设置是左右双栏模态框。直接塞进手机（420px）会坏：

- 侧栏展开时 grid 轨道变成 `280px 113px 0px` —— 主区域只剩 113px，输入框每行两个汉字
- 设置面板 372px 里塞 nav 188 + content 184，内容再减内边距只剩 **136px** —— 中文逐字竖排
- 图标轨道常驻占 13% 屏宽

**为什么不直接改 DSH 源码**：

1. 你装的是打包好的 Electron App，前端产物在 `app.asar` 里，没有源码 checkout；
2. 代理层注入只影响手机这条路径，**桌面 GUI 一个字节都不动**；
3. DSH 升级后注入依然生效（选择器不用 hash 类名，见第四节）。

## 二、注入机制

```
DSH index.html ──► 代理改写 <head> ──► 手机
                    ├─ <link rel="stylesheet" href="/__dsh-mobile/mobile.css?v=<mtime>">
                    └─ <script defer src="/__dsh-mobile/mobile.js?v=<mtime>"></script>
```

- 两个资源由**代理自己吐**（`serveMobileAsset`），不落到上游 DSH；同样需要令牌鉴权。
- `?v=<文件 mtime>`：手机页面已加载后浏览器跑的是旧 JS，不带版本号会让人误判"修复没生效"。
- 只对**文档导航**生效：`accept` 含 `text/html` 时上游请求强制 `identity` 编码（否则拿到 gzip 没法改写），静态资源继续 gzip。
- 改写后重算 `content-length` 并删掉 `content-encoding`。
- 总开关：`remote.config.json` → `"mobileEnhance": false`（改完重启代理）。改 `mobile.css` / `mobile.js` **不用重启**（按 `no-cache` 每次请求重读）。

## 三、修了什么（含实测数值）

| # | 问题 | 原状（420px） | 修后 | 手法 |
|---|---|---|---|---|
| 1 | 图标轨道常驻占位 | 56px（13% 屏宽），刷新后必现 | **0 宽**，正文拿满 | grid 第一轨无条件归零 + 注入浮动汉堡按钮 |
| 2 | 侧栏展开挤窄主区域 | 轨道 `280px 113px 0px` | 侧栏改 absolute 浮层，轨道 `0px 420px 0px` | `:not([data-sidebar-collapsed])` + `!important` 盖内联样式 |
| 3 | 选中会话后抽屉不收 | 一直盖住聊天区和输入框 | 自动收起 | 委托监听 `_sessionRow` 点击，延迟 120ms 点 App 的 `_toggle` |
| 4 | 抽屉无法关闭 | 只能点侧栏内按钮 | 点背景遮罩即收 | 注入 `[data-dshm-backdrop]` |
| 5 | 设置等模态框桌面双栏 | 内容内宽 **136px**，中文逐字竖排 | 全屏 + 顶部横向标签条，内容内宽 **388px** | 面板 `flex-column`、nav 转 row 且 `overflow-x:auto` |
| 6 | 正文列被内边距吃掉 72px | `GrHszq_scroll` 是 `padding:16px 32px`，正文 **290px** | 正文 **386px（+33%）** | padding 收到 12px |
| 7 | 底部指标胶囊截断 | `_label` 限宽 76px → "6 轮 226 步…" | 两行完整显示 | 容器允许换行 + 胶囊按内容宽度排 |
| 8 | 会话标题行横向重叠 | 动作簇压右侧图标组 **55px** | 重叠归零 | 文案让位成图标（`font-size:0`），标题去截断 |
| 9 | 点击目标过小 | 28–36px | 侧栏内 ≥44px / 输入区 40px / 表头 36px | `min-*` 而非 `width`，避免压扁整行按钮 |
| 10 | iOS 动态视口 / 安全区 | `height:100%`，底部输入框被 Safari 工具栏压住 | `100dvh` + `env(safe-area-inset-*)` | — |
| 11 | 侧栏拖宽手柄吞滚动手势 | 8px `touch-action:none` | 触摸设备隐藏 | `@media (pointer: coarse)` |
| 12 | 会话行 hover 预览卡飞出屏外 | `right=520` / 视口 420 | 触摸设备隐藏 | 无真 hover，安卓点击会留粘滞态 |
| 13 | 输入框字号 14px | iOS 聚焦会**自动放大页面**（实测 `DIV.sgwBQW_input` 14px） | 全部输入框/textarea/contenteditable 补到 **16px** | 只在 `max-width:720px` 内 |
| 14 | 菜单卡高用 `100vh` | iOS 的 100vh 含工具栏后方区域，偏高（实测 909px） | `max-height: calc(100dvh - 24px)` + 宽度钳 `100vw-16px` | 用 `_list_`+`_portal_` 双类名精确定位 Menu 原语（[PITFALLS B18](PITFALLS.md)） |
| 15 | 子菜单固定向右弹 | `left: calc(100% + 10px)`，420px 屏必飞出 | 触屏改**底部浮层**（fixed，z-1101 压过父菜单 1100） | `@media (pointer: coarse)` |
| 16 | 高模态框被裁且不可滚 | dialog 无 max-height、body 无 overflow | `max-height: 100dvh-24px` + body 滚动 + footer 可换行 + root padding 24→12 | 结构选择器 `[class*="_mask"] ~ [class*="_dialog"]` |
| 17 | **宽表格触屏完全不能横滚** | `.md-table-wide` 只在 `:hover`/`:focus-visible` 才放开 `overflow-x:scroll`，触屏永远到不了 | 触屏 `overflow-x:auto` | 真功能 bug（桌面设计的 hover 假设） |
| 18 | JsonTree 复制钮、历史消息动作行触屏不存在 | 都是 hover 才显（`data-actions-reveal="hover"` 时 `opacity:0`） | coarse 下常显 | — |
| 19 | composer 文本上限写死 336px | 矮屏手机（667px 档）占半屏 | `min(336px, 30dvh)` | CSS 变量覆写 |
| 20 | diff 对比双列 | 420px 下每列 ~190px | 单列堆叠（`_columns` 全前端只有 diff 模块在用） | 子列保留各自 overflow-x:auto |
| 21 | 展开区小按钮难按 | 复制钮 **26×18**、查看钮 56×22（实测） | coarse 下 ≥32px | — |
| 22 | 右栏全屏时汉堡压住面板顶栏 | 汉堡 z-55 > 面板 z-40 | JS 判断面板可见性后藏汉堡 | `data-rightbar-collapsed` 不可靠（[PITFALLS B17](PITFALLS.md)） |
| 23 | tooltip 黑气泡点击后粘滞悬挂 | 桌面 hover 产物 | 触屏隐藏（`[class*="_bubble"][data-side]`，不伤聊天气泡） | — |
| 24 | 标题行角落/右栏顶栏按钮 28px | 难按（实测 28–31px） | coarse 下 36px | — |
| 25 | 无手势 | 只能点汉堡/遮罩 | **左缘 24px 右滑开抽屉、抽屉上左滑关**（阈值 64px、横向优势 2 倍，不抢滚动；判定成立 preventDefault 压制浏览器边缘返回） | mobile.js，审计有合成触摸断言 |
| 26 | **触屏点会话被误归档**（严重） | 「侧栏按钮补 44px」把 hover 才显形的行内操作按钮也撑成 44×44 → 容器占满整行右侧 **152/256px（59%）**、行高 32px 还溢出到相邻行，「归档会话」正好压在标题/时间上；手机一按就触发粘滞 hover、按钮在手指底下冒出来 | ①行内操作按钮**排除**放大规则；②触屏**摘掉「归档会话」**（保留置顶/操作）；③操作区起点 x=108 → **x=218**（占 16%） | [PITFALLS B22](PITFALLS.md)；审计新增 2 条断言（hover 后逐点探测命中元素） |

## 四、DOM 契约（最重要）

适配层是**针对 DSH 前端 DOM 的覆盖样式**，所以它依赖下面这些锚点。DSH 升级若改了其中任何一个，对应功能就会静默失效。

**选择器策略：不猜 hash 类名。** CSS-module 的类名是 `hash_local`（如 `ONzo8q_sidebarCol`），hash 每次 DSH 重建都变，**local 名不变**。所以：

1. 优先用 App 自己设的**语义化属性**（跨版本最稳）；
2. 次选用 local 名做子串匹配 `[class*="_sidebarCol"]`。

> 这条策略已实测扛过两次版本变化（Electron 打包版 → npm `0.1.7-alpha.2`：hash 全换、端口变了，**契约断言依然全过**）。两版的差异清单见 [第九节 版本兼容矩阵](#九版本兼容矩阵旧版-electron--新版-npm-017)。

| 锚点 | 选择器 | 用途 | 失效表现 |
|---|---|---|---|
| frame | `[data-slot="root"] > [class*="_frame"]` | 布局网格根 | **所有**布局规则失效 |
| `data-sidebar-collapsed` | frame 上的属性（仅折叠时存在） | 判断侧栏开合 | 抽屉/浮层判断全错 |
| `data-rightbar-collapsed` | frame 上的属性 | 右栏状态 | — |
| sidebarCol | `… > [class*="_sidebarCol"]` | 侧栏列 | 抽屉失效 |
| centerCol | `… > [class*="_centerCol"]` | 主区域 | 标题避让失效 |
| rightbarCol | `… > [class*="_rightbarCol"]` | 右栏列 | 第三轨规则失效 |
| sidebarToggle | `button[class*="_toggle"]` | 展开/收起侧栏 | **汉堡按钮点不开抽屉** |
| sessionRow | `[class*="_sessionRow"]` | 会话行 | 选会话不自动收起 |
| projectRow | `[class*="_projectRow"]` | 工作区行 | 可能误收起抽屉 |
| composerSeat | `[class*="_composerSeat"]` | 输入区 | 指标/点击目标规则失效 |
| scrollBody | `[class*="_scrollBody"]` | 消息滚动区 | — |
| titleRow | `[class*="_titleRow"]` | 标题行 | 汉堡可能压住标题 |
| column / scroll | `[class*="_column"]` / `[class*="_scroll"]:has(> [class*="_column"])` | 正文容器 | 正文宽度回退到 290px |
| navList | `[class*="_navList"]` | 设置左侧导航 | 设置标签条失效 |
| 菜单卡片 | `[class*="_list_"][class*="_portal_"]` | 下拉菜单 | 菜单 dvh/宽度钳制失效 |
| 子菜单 | `[class*="_submenu"]` | 二级菜单 | 触屏底部浮层失效 |
| 模态框 | `[class*="_mask"] ~ [class*="_dialog"]` | Modal 原语 | 高模态可滚动失效 |
| 宽表格 | `[class*="_tableScroll"].md-table-wide` | markdown 宽表格 | 触屏横滚失效 |
| 右栏面板 | `[class*="_rightbarCol"] [class*="_panel"]` | 右侧面板 | 汉堡让位失效 |
| diff 列 | `[class*="_columns"]` | diff 对比视图 | 单列堆叠失效 |
| 审批按钮行 | `[class*="_actionRow"]` | 审批卡 | 换行失效 |
| 工具展开区 | `[class*="_bodyWrap"]` | 工具/技能卡 | 按钮目标规则失效 |
| 轨迹 payload | `[class*="_toolCallPayload"]` | 轨迹 tab | （只读观测，无规则） |

> 这些锚点已固化成审计工具的「DOM 契约」断言。**跑审计看到契约 FAIL，先怀疑 DSH 改版，不要怀疑适配层逻辑。**

## 五、z-index 层级表（改浮层前必看）

| 层 | z-index | 归属 | 备注 |
|---|---|---|---|
| 作曲家 sticky seat | **7**（带触发器菜单 **9**） | App | 踩过：抽屉设 5 会被它盖住 |
| 右栏面板（停靠） | 10 | App | |
| 拖宽手柄 | 11 | App | 触摸设备已隐藏 |
| **背景遮罩** | **12** | 本层 | 必须高于 9 |
| **侧栏抽屉** | **15** | 本层 | 必须高于 9、低于 20 |
| App overlayLayer | **20** | App | 侧栏里弹出的菜单挂这；超过它菜单会被侧栏盖住 |
| 右栏面板 fullscreen | 40 | App | 窄屏下 App 自己切全屏；另一块独立面 |
| **汉堡按钮** | **55** | 本层 | 抽屉/模态框/右栏全屏打开时隐藏 |
| tooltip 气泡 | 100 | App | 触屏下整体隐藏（粘滞 hover 会一直挂着） |
| 菜单卡片 | 1100 | App | portal 模式 fixed 定位 |
| **触屏子菜单（底部浮层）** | **1101** | 本层 | 必须压过父菜单的 1100 |
| 设置对话框 overlay | App 自定 | App | 打开时汉堡按钮一并隐藏 |

## 六、断点

| 条件 | 作用 |
|---|---|
| `@media (max-width: 720px)` | 全部布局改造（轨道归零、抽屉、正文加宽、模态框全屏） |
| `@media (pointer: coarse)` | 点击目标放大、隐藏 hover 预览卡 |
| `@media (pointer: coarse), (max-width: 720px)` | 隐藏拖宽手柄 |

桌面（1440px）实测与原生完全一致，见审计的「桌面 1440px · 回归」段。

## 七、审计工具

```sh
python tools/mobile-audit.py                    # 打本机代理
python tools/mobile-audit.py --url http://<服务器IP>:17933   # 打公网
python tools/mobile-audit.py --webkit           # 追加 WebKit（真 Safari 引擎）iPhone 冒烟组
python tools/mobile-audit.py --keep-open        # 保留浏览器调试
```

- 覆盖 12 组：DOM 契约 / 加载态 / 选会话后 / 抽屉 / **轨迹 tab** / **工具行展开** / **右栏全屏** / **手势（合成 TouchEvent）** / 设置对话框 / **设置-模型子页** / 桌面回归 / **WebKit 冒烟（`--webkit`）**
- 产出：PASS / FAIL / **WARN**（WARN = 已知可接受，不影响退出码）
- 截图：`tools/out/`（phone-01…08 + webkit-01 + desktop-01）
- 退出码：0 全过，1 有失败 —— 可直接接 CI
- 令牌从 `remote.config.json` 读，脚本里没有密钥
- `--webkit` 需要先 `python -m playwright install webkit`；未安装则 WARN 跳过，不影响退出码

**判据说明与已知误报白名单**（判据细节都在脚本注释里）：

| 探测 | 判据 | 白名单 |
|---|---|---|
| 横向溢出 | 越过视口右缘且**无可横向滚动祖先、也未被 overflow:hidden 祖先裁掉**（裁剪感知，轨迹 payload 行就靠这条不误报）；fixed 元素不看祖先裁剪 | — |
| 文字竖排 | **块级元素**宽 <175px 且高 >2.6 行（行内元素跨行折行是正常排版，已排除） | `td`/`th`/`code`/`pre` 等 |
| 意外截断 | `scrollWidth > clientWidth` 且 `overflow:hidden` | 折叠摘要、面包屑、文件路径与文件链接（`_fileLink`/`_filePath`）、胶囊标签；宽度 ≤2 的 `visuallyHidden` |
| 组件重叠 | **同一父元素下**的兄弟节点相交 | 收紧到同父，避免 sticky 表头与滚到其下内容被误判 |

## 八、加一条新规则的流程

1. **先跑审计**，确认当前是绿的（否则分不清是不是你改坏的）。
2. **量，别猜**：写个一次性探针 dump 目标元素的 `getBoundingClientRect` + `computedStyle` + 类名，确认层级关系（[PITFALLS B12](PITFALLS.md) 就是猜错层级白跑一轮）。
3. 写规则，**优先用语义属性**；必须盖内联样式才用 `!important`。
4. 想清楚 **z-index 放哪一层**（对照第五节）。
5. **加一条审计断言**（把"应该成立"固化成不变量），再跑审计。
6. 若失败，先怀疑断言本身（[PITFALLS D2](PITFALLS.md)）。
7. 截图肉眼确认一遍——数字对不代表好看。
8. 提交，提交信息里写清「原状 → 修后」的实测数值。

## 九、版本兼容矩阵（旧版 Electron ↔ 新版 npm 0.1.7）

**结论：一套适配层同时兼容两个版本。** 原理是选择器只认 local 名与语义属性，所以 hash 全换（`ONzo8q` → `EvIC1a` 等）也不受影响；真正需要版本判断的地方都改成了「语义信号」而不是「结构猜测」。

| 功能 | 旧版（Electron 打包） | 新版 `0.1.7-alpha.2`（npm） | 两版兼容性 |
|---|---|---|---|
| 布局壳 frame/sidebarCol/centerCol/rightbarCol | `ONzo8q_*` | `pI_x6G_*`（本机实测；聊天流是 `EvIC1a_*`，右栏面板 `P3OORG_*`） | ✅ 只认 local 名 |
| 侧栏会话行 / 工作区行 | `_sessionRow` / `_projectRow` | 同 local 名（hash 变） | ✅ |
| 侧栏收起按钮 | `button[class*="_toggle"]` | 同 | ✅ |
| 上游端口 | `19387` | `3080` | ✅ 代理**自动发现**（并发探测 + 换端口重签 cookie） |
| 右栏开关状态 | `data-rightbar-collapsed` 语义不可靠；关闭态面板 `translate` 出屏 | 同样属性语义不可靠；**关闭态容器仍留在屏内**（内容被 `translateX(420px)` 推出） | ✅ 统一改认「收起右侧边栏」按钮是否在视口内 —— 两版关闭态该按钮都在 x≈800（屏外） |
| 开关右栏时的 DOM 变化 | 改 class | **只改内联 `style`** | ✅ 监听 `style` + 800ms 兜底巡检（sync 幂等） |
| 会话行内 hover 操作按钮 | hover 显形 | hover 显形（`操作`/`归档会话`/`置顶会话`） | ✅ 统一：不放大 + 触屏摘掉「归档会话」 |
| 菜单 / 模态 / 宽表格 / 复制钮等原语 | 来自 `dsh-client-ui-primitives` | 同源 | ✅ 规则同时生效 |
| 工具行展开区 | `_bodyWrap` / `_block` / `_copyButton` / `_inspectButton` | `O_Ebla_*`（工具卡）、`ztWv_q_*`（行）、`_action[aria-label="复制"]` | ⚠️ **部分**：旧规则保留（新版为空操作）；新版复制钮已补 32px；展开区其余适配**待做**（审计降级为 WARN，不假绿） |
| 移动端布局规则（dvh / safe-area / 抽屉 / z-index / 手势） | 与版本无关 | 同 | ✅ |

**怎么判断当前跑的是哪个版本 / 契约有没有变**：

1. 跑审计：`DOM 契约` 组 FAIL = 版本变了（先怀疑 DSH 改版，别怀疑适配层逻辑）。
2. 审计的「工具行展开」组会打印当前命中哪套选择器（旧版断言 / 新版 WARN）。
3. 端口不用管：代理日志里的 `upstream 可用/切换为 …` 会告诉你它认到了哪个端口。
