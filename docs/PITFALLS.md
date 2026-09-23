# 踩坑记录

> 按「症状 → 原因 → 正确做法」记。**踩过的地方不要再踩第二次。**
> 编号是稳定引用，插新条目请追加到对应分组末尾，不要重排已有编号。

## A. 服务器 / 隧道

**A1. `.bat` 里不要写中文。**
cmd.exe 用 OEM 代码页（中文 Windows = GBK）读 `.bat`，UTF-8 中文乱码后会产生含 `&` 的垃圾字符，把 `rem` 注释行**切断成两条命令**（报 `'yun' is not recognized`）。
→ 本目录所有 `.bat` / `.vbs` 一律纯 ASCII，中文只出现在 `.md`。

**A2. 不要用 `pkill -f <pattern>` 在 ssh 会话里杀进程。**
承载该命令的 sshd 会话，其命令行里也含这个字符串，会**把自己杀掉**（表现为 ssh 命令无输出且 exit 1）。
→ 按端口杀：`fuser -k <端口>/tcp`。

**A3. `firewall-cmd --list-ports` 里有端口 ≠ 外网可达。**
**安全组是独立一层**，SSH 改不了。常见云服务器的安全组默认只放行 `22 / 80 / 443` 等常用端口；firewalld 里开着某个端口 ≠ 安全组放行了。
→ 加端口要**同时**动 firewalld 和云服务商控制台的安全组。

**A4. 服务器 sshd 需要 `GatewayPorts clientspecified`。**
没有这一行时，`ssh -R 0.0.0.0:PORT:...` 只会绑到 `127.0.0.1`，公网连不上。改 `/etc/ssh/sshd_config` 后重启 sshd 即可，与客户端无关。

**A5. 按命令行匹配杀进程会自杀。**
`stop-all` 的 PowerShell 子进程命令行里就含 `17933:127.0.0.1:19390` 这个匹配串，会把自己也匹配进去。而且 `Get-CimInstance Win32_Process` 在受限环境下会直接「拒绝访问」**静默失败**（看起来像"没有进程在跑"）。
→ 停止逻辑改成读 **PID 文件**；命令行扫描只作兜底且失败不致命。

**A6. 隧道端口和 firewalld 都对了，仍可能不通。**
→ 用**同机同客户端对照法**：拿一个已知可用的端口做参照，通/不通一比就知道是不是安全组。

**A7. DSH 的 Web 端口是动态的，别写死。**
实测重启 DSH 后从 `19387` 变成 `3080` —— 写死配置的话，DSH 每重启一次远程入口就 502 一次，而且现象很像「代理坏了」。
→ 用**特征响应**把它认出来：DSH Web 对未鉴权的 `GET /` 固定回 `401` + 文案 `dsh web authentication required`。扫本机 LISTEN 端口逐个探一下即可；**并发探测**（串行 20 多个候选要 6 秒以上）。
→ 端口变了必须**重签 cookie**：DSH 的 cookie 名与内容都绑定 authority（`dsh-auth-<sha256(authority)>` + body 里的 `authority`）。

**A8. 从 DSH 终端里启动的代理/隧道，会随 DSH 重启被连带杀掉。**
它们是 DSH 的子进程，DSH 一重启整棵进程树就没了。实测踩过：重启 DSH 后 17933 的隧道消失，服务器上 `ss -ltn` 查不到监听（公网表现为**连接被拒绝**，区别于超时=安全组/VPN），而机器上还活着的那个 ssh 是**另一个项目的**隧道（转发 17932）。
→ 用**独立启动器**：Windows 登录自启 VBS（`WScript.Shell.Run`）、macOS launchd、或 `start-all.bat`（`start` 脱离父进程）。
→ 排查时先分清是哪一层死了：`Test-NetConnection 127.0.0.1 -Port 19390`（代理）→ 服务器 `ss -ltn | grep 17933`（隧道）→ 再看上游端口（日志 `upstream …`）。

## B. 移动端适配

**B1. 给 grid item 设 `position:absolute` 会让它退出自动排布，后续兄弟元素整体前移一轨。**
症状：侧栏改浮层后，`centerCol` 跑去占宽 0 的第 1 轨，**主区域反而变成 0 宽**。
→ 必须显式钉 `grid-column: 1 / 2 / 3`。

**B2. App 的内联样式只能用 `!important` 盖。**
`grid-template-columns` 是 React 写在 `style` 属性上的，普通 CSS 规则赢不了。

**B3. 抽屉 / 浮层的 z-index 必须夹在「作曲家」与「App 浮层」之间。**
`WMSvqq_composerSeat` 在 active 阶段是 `position:sticky; z-index:7`（带触发器菜单时 9）。抽屉原先用 5 → **底部被作曲家盖住**，真机点抽屉底部的「设置」会点到作曲家的指标行上（审计工具用 `elementFromPoint` 抓到的）。
→ 最终：遮罩 **12** / 抽屉 **15**（高于 9；低于 App 的 `.ONzo8q_overlayLayer` 的 20，否则侧栏里弹出的菜单会被侧栏自己盖住）。

**B4. 想用 `overflow:hidden` 消掉 flex 重叠，会把文字裁成半个字，比重叠更难看。**
→ 让可牺牲的**文案**变成图标（`font-size:0` 只吃文字、不动有显式尺寸的 svg），让标题去截断。

**B5. `overflow: visible` 消不掉截断，反而制造重叠。**
指标胶囊的 `_label` 被限宽截断，先改成 `overflow:visible` → 文字溢出胶囊、压到相邻胶囊上，**比截断更糟**（用户反馈的"组件视觉重叠交叉"就是这个形态）。
→ 让**容器**按内容宽度排 + 允许换行，label 保持 `overflow: hidden`。

**B6. 别用 `[class*="_frame"]` 这种宽泛子串**：会命中 8 个不同组件（`dTBzLW_frame` / `oefblG_frame` / `Zj3LVa_frame` …）。
→ 用 App 的语义化属性锚定：`[data-slot="root"] > [class*="_frame"]`。

**B7. `[class*="_overlay"]` 会连 App 自己的浮层容器一起命中**（`ONzo8q_overlayLayer` 含 `_overlay` 子串）。
→ `[class*="_overlay"]:not([class*="_overlayLayer"])`。

**B8. 「选中后自动收起」不能在 click 里同步执行。**
React 18 的事件处理挂在 root 容器上，而委托监听在捕获阶段先跑 —— 立刻收起会把行从 DOM 里摘掉，App 的选中反而收不到。
→ `setTimeout(..., 120)` 让 App 先处理完。

**B9. 自动收起要排除行内操作按钮。**
会话行里的「…」菜单 / 删除也是 `_sessionRow` 的后代，不排除的话点菜单会顺手把抽屉关了。
→ `if (e.target.closest('[class*="_iconButton"]')) return`。

**B10. 自动收起还要区分行类型。**
`_sessionRow`（会话，该收）vs `_projectRow`（工作区文件夹，展开/折叠树，**不能收**）—— 两者都有 `role=treeitem`，只看 role 会误判。

**B11. 用 `:has(> X)` 认容器前，先确认 X 是容器的直接子元素。**
我用 `:has(> _sep)` 找指标行，实际 `_sep` 是 `_label` 的子元素 → 匹配到的是 label 本身（宽 76px），规则打偏了。

**B12. 改布局要先量再改，别猜层级。**
`GrHszq_scroll` 是**父**、`GrHszq_column` 是**子** —— 按直觉写成 `_column > _scroll`，选择器完全没命中，白跑一轮验证。

**B13. 「隐藏」优先用「grid 轨归零 + `overflow:hidden`」，不要用 `display:none`。**
前者把侧栏轨道收成 0 宽，但里面的切换按钮仍在 DOM 里 —— `HTMLElement.click()` 对不可见元素同样派发事件，所以注入的汉堡按钮能直接调 App 自己的 `_toggle`，不必碰 React 内部状态。用 `display:none` 就要另找入口。

**B14. 用户说的「侧边栏」可能是连图标轨道一起算的。**
第一轮只把展开的侧栏改成抽屉、折叠态保留 56px 轨道，用户两次反馈「侧栏还在」—— 他要的是**整条轨道消失**。移动端 13% 屏宽常驻占位确实奢侈。
→ 轨道归零 + 补一个浮动入口按钮。

**B15. 注入的资源必须带版本号。**
手机页面已加载后，浏览器里跑的是**旧的 JS**；改完 `mobile.js` 刷新前不生效，会让人误判「修复没起作用」。
→ 用文件 mtime 做 `?v=`，改动即失效。

**B16. CSS 的 `:has()` 判断不了「元素是否可见」。**
模态框（设置等）的 overlay 元素**一直存在于 DOM 里**，关闭时只是隐藏。
想用 `html:has([class*="_overlay"]…) [data-dshm-hamburger] { display:none }` 在模态框打开时藏汉堡按钮 —— 该选择器**恒为真**，结果汉堡按钮被永久藏掉，用户再也打不开侧栏。
→ 这类判断放 JS 里按「实际尺寸 + `visibility`」做（见 `mobile.js` 的 `hasOpenModal()`）。
→ 也说明**每次改完都要跑审计**：这个错误是审计当场抓到的，没上到手机上。

**B17. `data-rightbar-collapsed` 只描述「停靠列」，不代表右栏开关状态。**
打开右栏时该属性**仍然存在**（实测），按属性判断永远得到"已关闭"。
→ 判断浮层类 UI 的可见性，别只看状态属性。但也别只量容器：DSH 0.1.7 在**关闭态**同样把面板容器留在屏内（`x=0`、宽 = 视口宽），只是把内容 `translateX(420px)` 推出屏幕 —— 按容器宽度判断会把"关着"当成"开着"，于是汉堡按钮被误藏、用户打不开会话列表（真踩过）。
→ 认**语义信号**：面板顶栏那个「收起右侧边栏」按钮是否真的落在视口内（关闭态时它在 x≈798 的屏幕外）。见 `mobile.js` 的 `rightbarFullscreenOpen()`。

**B18. 短 local 类名会跨模块撞车，用之前先查类名清单。**
`_bubble` 既是 Tooltip 的气泡又是聊天消息气泡；`_list`/`_footer`/`_dialog`/`_card` 都被多个模块用。
→ 精确定位手法：双类名组合（菜单卡片 = `[class*="_list_"][class*="_portal_"]`）、属性限定（tooltip = `[class*="_bubble"][data-side]`，聊天气泡没有 `data-side`）、结构限定（模态 = `[class*="_mask"] ~ [class*="_dialog"]`）。

**B19. 手机上点「添加工作区」，对话框会弹在电脑屏幕上。**
`directory-picker-auto` 的选型逻辑：loopback 绑定 + win32/darwin → **native** 后端（系统目录对话框开在主机桌面）。远程浏览器点它，手机上毫无反应。
→ 这是已知**功能限制**（工作区一般在电脑前就配好了）。要让手机能选目录，需要把 picker 钉成 `browse` 交互（cordis 组合层，未做）。

**B20. 状态变化不一定产生可观察的 mutation。**
DSH 0.1.7 开关右栏时**只改内联 `style`（transform）**，不改 class —— MutationObserver 用 `attributeFilter` 只盯 class/data-*，回调根本不触发，于是「关了右栏，汉堡按钮不回来」（实测踩过）。
→ `attributeFilter` 要含 `style`；再加一个低频（800ms）兜底巡检，覆盖「改的是内部 state、DOM 完全没动」的情况。
→ 前提是 **sync 必须幂等**：`setAttribute` 即使赋相同的值也会产生 mutation 记录，配兜底巡检会变成每帧自激的循环（所以要先比较再写）。

**B21. 自动化点击要点「语义目标」，别点容器中心。**
审计里 `row.click()` 点的是行**几何中心** —— 新版会话行中间挂着一个 `_iconButton`（操作按钮），于是命中了「排除 iconButton」的分支，表现成「选中后不自动收起」，白排查一轮（真因是审计点错位置，适配层没问题）。
→ 点标题（`[class*="_title"]`）这类语义目标，点不到再退回容器。

**B22. 别给「hover 才显形的行内操作按钮」放大点击目标 —— 它会和主点击目标重叠，破坏性操作被误触。**
「侧栏所有 button 补到 44px」那条规则把会话行里 hover 才出现的操作按钮也撑成了 44×44：实测容器占满整行右侧 **152 / 256px（59%）**、行高只有 32px 还溢出到相邻行，而**「归档会话」正好压在标题/时间上**。手机上一按就触发粘滞 hover、按钮在手指底下冒出来 → **点会话变成归档会话**（用户的会话真被归档了）。
→ 放大规则必须**排除行内 hover 操作按钮**（`[class*="_rowActions"] button`）；破坏性的「一键归档」在触屏上直接摘掉（恢复入口留在别处、走两步操作）。
→ 一般原则：**触屏上，主点击目标的区域内不能出现会改变状态的次生按钮**，尤其是破坏性的。审计已把这条固化成断言（hover 一行后逐点探测命中元素）。

## C. 跨平台

**C1. 跨平台路径用 `os.homedir()`，不要用 `process.env.USERPROFILE`。**
macOS / Linux 没有 `USERPROFILE`，会拼出相对路径 `.dsh`，报「找不到凭证文件」。
（这是个真 bug，曾被写进第一版。）

**C2. shell 里 `set --` 会覆盖 `$1`。**
若角色参数（`all`/`proxy`/`tunnel`）还放在 `$1`，`set -- $(...)` 之后 `case "$1"` 拿到的是主机名。
→ 先 `ROLE="$1"` 存起来。

**C3. `set -- $(cmd) || exit 1` 抓不到 `cmd` 失败。**
命令替换失败时 `set --` 仍返回 0。
→ 先把结果存进变量（`OUT=$(cmd) || exit 1`）再 `set --`。

**C4. `Set-Content -Encoding utf8` 在 Windows PowerShell 会写 BOM。**
Python 文件开头多出 `\ufeff` → `SyntaxError: invalid non-printable character U+FEFF`。
→ 用 Python 自身读写（默认无 BOM），或显式用无 BOM 的 UTF8Encoding。

## D. 工具与流程

**D1. 绝对不要用 PowerShell 的 `Get-Content` / `Set-Content` 批量改 UTF-8 文件。**
编码往返会把中文**双重编码**（`远程` → `杩滅▼`），而且 `-replace` 因为读进来时已经乱码而**静默不匹配** —— 结果是「改动没生效 + 文件坏了」。
→ 用编辑工具的精确替换。真要脚本处理，读取必须显式 `-Encoding utf8`，且注意 BOM（见 C4）。

**D2. 审计断言本身会错，先怀疑断言再怀疑产品。**
某轮 8 个「失败」里 5 个是探针误报：`td` 表格单元格被判竖排、被祖先 `overflow:hidden` 裁掉的元素仍报告布局矩形（0 宽侧栏轨道里的按钮）造成假重叠、摘要白名单太窄、契约元素只在特定上下文存在。
→ **先把探针修准，再看剩下的是什么**。否则既浪费时间去查假问题，又会掩盖真问题。

**D3. Playwright 移动端模拟下 `locator.click()` 的动作性检查会误判。**
设置按钮 `elementFromPoint` 就是它自己、无任何遮挡，`click()` 却一直超时；改用 `touchscreen.tap` 按坐标点，又会因坐标 / 动画时序打到背后的正文元素（误开了作曲家的"会话统计"浮层）。
→ **可达性与动作分开测**：用 `elementFromPoint` 断言"点得着"，用 JS `.click()` 完成打开动作。

**D4. 侧栏同时渲染折叠态与展开态两套 DOM。**
DOM 里更早的同名「设置」按钮是隐藏的，`.locator(...).first` 会选中它然后一直等到超时。
→ 取元素前先按可见尺寸过滤（`r.width > 2 && r.height > 2`）。

**D5. 探针的 DOM 遍历别在尺寸为 0 的节点上 `continue`。**
`display:contents` 的包裹层宽高为 0，跳过它就等于跳过整棵子树（第一版探针只探到 `#root` 一个节点）。

**D6. 探测脚本要过滤 `display: contents` 和零尺寸元素。**
它们宽高为 0，会在「最窄元素」这类排序里夺冠（指标行探针就这么失败过一次）。

**D7. Playwright 点全屏遮罩要点「可见区域」。**
遮罩 `inset:0` 而抽屉盖住其左侧，点元素中心会落在抽屉上（z 更高）而被判定为「被拦截」。
→ 用 `page.mouse.click(x, y)` 指定抽屉右侧的坐标。

**D8. 改完一定要做桌面回归。**
适配层规则都在 `max-width:720px` 内，但"应该是这样"不算验证。实测 1440px 下设置面板仍是 `800x800 / flex-row / radius 32px`，与原生一致。
→ 这一项已固化成审计里的断言。

**D9. 控制台可能是 GBK。**
打印含 GBK 编不了的符号（如 `☰` U+2630）会抛 `UnicodeEncodeError` 把整个脚本打断。
→ 脚本开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，且脚本自身源码也只用 GBK 可编码字符。

**D10. CSS-module 的 hash 是 base64url，可能以 `-` / `_` 开头。**
活 DOM 里有 `-F09VW_root` 这样的类名。用 `[A-Za-z0-9]{6}` 提取 hash 会漏掉它们。
→ 提取正则用 `[A-Za-z0-9_-]`；运行时选择器 `[class*="_local名"]` 不受影响。

**D11. 溢出探针必须「裁剪感知」。**
轨迹 tab 的 payload 行把 span 排到 4000px 宽，但视觉上是干净的省略号 —— 祖先 `overflow:hidden` 把它裁了，**不可见的内容不算页面溢出**。
→ 溢出检查加 clippedRight 分支：祖先 `overflow-x:hidden/clip` 且元素右缘越过祖先右缘 → 跳过（fixed 元素不受祖先裁剪，除外）。

**D12. 审计打开的会话正文不是稳定 fixture。**
会话内容随工作变化：一条含 `<strong>` 的消息就让竖排探针两连 FAIL（行内元素跨行折行的联合矩形天然「窄而高」，是正常排版）。
→ 探针判据按**排版原理**写（竖排检查跳过 `display:inline` 元素），别把具体某条消息当基准。

**D13. 脱敏要「宽口径」扫，不能只扫你最在意的那个秘密。**
开源前第一轮只扫了服务器 IP / 邮箱 / 域名 / 个人路径 → 全 clean，看着可以发了。第二轮把口径放宽（所有非 loopback IPv4、`阿里云`/`宝塔` 之类厂商标识、另一个私有项目的名字与端口、安全组端口清单）→ 又抓出一批：**另一项目的引用与端口指纹、你服务器的安全组放行清单、个人时间线细节**。
→ 顺序应是：先列「所有能定位到我的东西」，再逐个决定泛化还是删除；并且把检查**固化成脚本 + pre-commit 钩子**，别靠记性（[README §十一](../README.md)）。

**D14. 正则捕获类要排除 CJK 标点，否则会把中文注释吞进「路径用户名」。**
`C:\Users\X、macOS 是 /Users/x。` 这种示意路径，用 `[^\\/\s]+` 捕获会得到「X、macOS」—— 误报，而且看着像真泄露。
→ 捕获类里排除 `、。，；：（）` 等 CJK 标点；再把 `X` / `x` / `user` 这类示意名加白名单。**闸门自身的误报也要修**，否则会被 `--no-verify` 绕过而形同虚设。

**D15. 用会自动跟随重定向的 HTTP 客户端测鉴权，会把 303 误读成 401。**
`urllib.request.urlopen` 默认跟随跳转：请求 `/?token=…` 时代理先回 `303` 换 Cookie，客户端**自动跟到 `/` 且不再带 token** → 得到 `401`，看起来像「令牌通道坏了」，白排查一轮。
→ 测状态码要禁跟随（`HTTPRedirectHandler.redirect_request → None`，或 `curl` 不加 `-L`）；要测最终页面才跟随。
→ 另外 `Get-Content` 用 GBK 读 UTF-8 日志会把中文显示成乱码（`涓嶅彲鐢?`），**别据此判断文件坏了** —— 用 `python -c "open(..., encoding='utf-8')"` 或 `-Encoding utf8` 复核（见 [D1](#d-工具与流程)）。
