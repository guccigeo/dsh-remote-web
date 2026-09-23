# 当前状态与待办

> 这份文档回答一个问题：**「现在到哪一步了，接下来从哪继续？」**
> 数字都是实测的，附了复现命令。最后更新：2026-09-23。

## 一、一句话现状

**功能已可用并已上生产路径**：手机上通过 `http://<服务器IP>:17933` 经自家中转服务器控制本机 DSH，能对话、看会话、传文件、跑命令。移动端布局已做过三轮优化（最新一轮把适配面从「主流程」扩到**全组件**：通用原语、工具行、轨迹 tab、右栏全屏、手势），**64 项回归断言全绿（含 WebKit 引擎的 iPhone 冒烟）**。剩下的是几个外观细节和两项环境验证（HTTPS、Mac 真机）。

## 二、验证状态

### 2.1 回归审计（主验证手段）

```sh
cd <仓库目录>     # 默认 %USERPROFILE%\.dsh\remote-web
python tools/mobile-audit.py            # 可加 --webkit 追加真 Safari 引擎冒烟
```

最近一次结果：

```
共 64 项，失败 0 项，已知可接受 0 项
```

覆盖 12 组：DOM 契约 / 加载态 / 选会话后 / 抽屉 / 轨迹 tab / 工具行展开 / 右栏全屏 / 手势 / 设置对话框 / 设置-模型子页 / 桌面 1440px 回归 / WebKit iPhone 冒烟。
**改任何布局前先跑一遍确认是绿的，改完再跑一遍做回归。**

### 2.2 公网链路

```sh
TOK=$(python -c "import json;print(json.load(open('remote.config.json'))['token'])")

curl -s -o NUL -w "%{http_code}\n" http://<服务器IP>:17933/                          # 期望 401
curl -s -o NUL -w "%{http_code}\n" "http://<服务器IP>:17933/?token=$TOK"             # 期望 303
curl -s -o NUL -w "%{http_code} %{size_download}\n" \
     -H "Cookie: dsh-remote=$TOK" http://<服务器IP>:17933/                           # 期望 200 ~31KB
```

最近一次：`401 / 303 / 200 (31318 字节)`。

WebSocket（DSH 流式走 `/api/remote.mux`，不是 SSE）也在本地验证过：无凭证 `401`，带凭证 `101 Switching Protocols`。

### 2.3 服务健康

| 检查 | 命令 | 期望 |
|---|---|---|
| 代理在听 | `Test-NetConnection 127.0.0.1 -Port 19390` | True |
| 隧道在服务器上 | ssh 到服务器 `ss -ltn \| grep 17933` | `0.0.0.0:17933` 由 sshd 监听 |
| 代理日志 | `Get-Content access.log -Tail 20` | 有 `minted DSH session cookie` / `listening` |

## 三、已完成功能

| 功能 | 状态 | 验证方式 |
|---|---|---|
| 手机经公网访问 DSH GUI | ✅ | 公网 curl 200；真机使用 |
| 稳定令牌（29 天 Cookie） | ✅ | 303 换 Cookie 实测；页面重启后仍有效 |
| 自家令牌鉴权（`?token=` / Cookie / Basic） | ✅ | 审计 + curl 401 |
| 把 DSH 进程级 token 换成长期稳定令牌 | ✅ | 读 `.credentials.yaml` 持久密钥自签 cookie，实测 200 |
| HTTP / SSE / **WebSocket** 透传 | ✅ | WS 无凭证 401、带凭证 101 |
| 反向隧道 + 断线自动重连 | ✅ | 杀掉代理子进程后监管器 5 秒重启；隧道 `ServerAliveInterval=25` |
| 登录自启（Windows） | ✅ | `dsh_remote_web.vbs` 已在 `shell:startup` |
| 移动端适配层（25 项修复） | ✅ | 审计 64 项全绿，见 [MOBILE.md](MOBILE.md) |
| 滑动手势（边缘右滑开抽屉 / 左滑关） | ✅ | 审计合成 TouchEvent 断言 |
| WebKit（Safari 引擎）冒烟 | ✅ | `--webkit` 7 项全过；**真机 Safari 仍待**（5.3） |
| macOS / Linux 支持 | ⚠️ **部分** | 脚本语法 + 参数推导已验证；**真机 ssh -R / LaunchAgent 未验** |
| HTTPS | ❌ 未做 | 见 README §七；需要 DNS 子域名 |

## 四、关键实测数值（改动的对照基线）

| 指标 | 原生 | 现在 | 说明 |
|---|---|---|---|
| 手机 420px · 侧栏轨道宽 | 56px | **0 / 1px** | 桌面测量值不变（280px） |
| 手机 420px · 主区域宽 | 364px | **420px** | 轨道归零后拿满 |
| 手机 420px · 正文列宽 | 290px | **386px** | 比原生 **+33%** |
| 设置对话框内容内宽 | 136px | **388px** | 原生会中文逐字竖排 |
| 桌面 1440px · 设置面板 | `800x800` `flex-row` | **完全一致** | 适配层对桌面惰性 |
| 输入区指标胶囊 label | 76px（截断） | 133/153px（完整） | — |
| 输入框字号（防 iOS 缩放） | 14px | **16px** | iOS 对 <16px 输入框自动放大页面 |
| composer 文本最大高度 | 写死 336px | **min(336px, 30dvh)** | 矮屏不再占半屏 |
| 工具行展开区按钮 | 26×18 / 56×22 | **≥32px** | 复制钮 / 查看钮 |
| 标题行角落按钮 | 28px | **36px** | 右栏开关、更多操作等 |
| iPhone 390px（WebKit）正文列 | — | **356px** | 与 Chromium 模拟一致 |

## 五、已知问题

### 5.1 审计里的 WARN（已知可接受）

| 项 | 现象 | 为什么不修 |
|---|---|---|
| 输入区紧凑触发器截断 | 模型名显示 `deepseek-v4.1-fla…`、推理等级显示 `H.`（**条件出现**：取决于当前模型名长短） | 是 App 的紧凑缩写控件；手机横向空间有限，点开就是完整选择器 |

### 5.2 外观细节 / 功能限制

1. **交付物卡片的「打开」按钮压在预览图上** —— `_cardPreview` 是整卡隐形点击层（z-1），`_open` 是叠在上面的 44px 按钮。当前审计会话里没有可见卡片，**等有交付物卡出现时再量再修**（别盲改）。
2. ~~工具行单行摘要截断狠~~ —— **查明是 App 端预截断**（`sw==cw`，"…"是文本自带），CSS 救不回也不该救；点行展开即见全文。**按设计关闭**。
3. ~~会话标题面包屑略挤~~ —— 实测容器无溢出（`sw==cw==129px`），是第一轮主动的让位截断，标题在侧栏/标签页都有。**保持现状**。
4. 底部 `%` 环形指标换行后与胶囊贴得较近（无重叠，只是不够整齐）。
5. **「添加工作区」在手机上不可用**：`directory-picker-auto` 判定 loopback+win32 → 弹**电脑屏幕上**的系统目录对话框（[PITFALLS B19](PITFALLS.md)）。工作区一般电脑前配好；要手机可选目录需钉成 `browse` 交互（未做）。

### 5.3 未验证 / 风险

| 项 | 风险 | 说明 |
|---|---|---|
| **macOS 真机** | 中 | `sh -n` 语法通过、隧道参数推导正确（`root@<服务器IP> 17933 19390`），但 `ssh -R` 与 LaunchAgent 必须在 Mac 上跑一次才算数 |
| **iOS Safari 真机** | 低-中 | **WebKit 引擎冒烟已过**（`--webkit`，7 项：390px 视口、16px 输入框、抽屉收起、正文列 356px、无溢出）；但地址栏/手势条的真实伸缩行为只有真机准 |
| **明文 HTTP** | 中 | 手机↔服务器是明文传令牌。野 WiFi 有嗅探风险；消除需上 HTTPS |
| **DSH 升级** | 低-中 | 适配层靠 DOM 契约。升级后跑一次审计即可发现（契约断言的用途） |
| **Windows 与 Mac 同时启动** | 低 | 会抢远端端口 17933，后启动的一方 `ExitOnForwardFailure` 失败后进重试循环 |

## 六、环境事实

| 项 | 值 |
|---|---|
| 中转服务器 | `<你的服务器IP>`（自建 Linux，SSH key 见 `remote.config.json` 的 `tunnel.identityFile`） |
| 公网端口 | `17933/tcp` |
| 端口放行 | firewalld + **云安全组**（两层都要放行；安全组 SSH 改不了） |
| sshd | 需要 `GatewayPorts clientspecified`（否则 `-R` 只绑到 127.0.0.1） |
| 云安全组实际放行 | 以自己服务器为准（本项目默认用 `17933/tcp`） |
| DSH GUI 端口 | Windows 实测 `127.0.0.1:19387`（**Mac 上可能不同**） |
| 代理监听 | `127.0.0.1:19390` |
| 隧道 | `本机 19390 → 服务器 0.0.0.0:17933` |
| 令牌 | `remote.config.json` → `token`（该文件不入库） |
| 仓库 | GitHub 公开仓库 [guccigeo/dsh-remote-web](https://github.com/guccigeo/dsh-remote-web)（`main` 为脱敏后的单提交历史；本地 `master` 保留完整私有历史，**永不可推送**） |

## 七、关键设计决策

改动这些决策前请先理解理由。

**D1. 用代理，而不是给 DSH 加 `--host 0.0.0.0`。**
DSH 启动时**硬拒绝** `0.0.0.0`（理由：会把 RCE 暴露到网络），`/api` 还有 Host/Origin 栅栏防 DNS rebinding。代理不绕过这些设计，而是把 Host/Origin 规范成 loopback 权威，**对外鉴权由代理承担**。

**D2. 自行签发 DSH cookie，而不是用 `dsh web` 打印的 token。**
那个 token 是**进程级**的，每次重启 DSH 都变，做不了手机书签。DSH 的浏览器会话 cookie 用 `.credentials.yaml` 里**持久化**的签名密钥，代理读它自签 cookie，因此对手机而言令牌长期稳定（29 天）。
👉 代价：格式是复刻 DSH 内部实现（`v1.<b64url(json)>.<b64url(hmac)>`）。若 DSH 改了格式，代理会收到上游 401 —— 已有自动重读密钥 + 明确诊断页兜底。

**D3. 适配层注入代理层，而不是改 DSH 源码。**
见 [MOBILE.md 第一节](MOBILE.md)。

**D4. 手机上把侧栏轨道收到 0 宽 + 浮动汉堡按钮。**
56px 常驻占 13% 屏宽。用「grid 轨归零 + `overflow:hidden`」而不是 `display:none`，因为侧栏里的切换按钮仍在 DOM 里、`HTMLElement.click()` 对不可见元素同样派发事件 —— 汉堡按钮因此能直接调 App 自己的 `_toggle`，不碰 React 状态。

**D5. 停止逻辑靠 PID 文件，不靠命令行匹配。**
`Get-CimInstance` 在受限环境下会「拒绝访问」静默失败；而且按命令行匹配会把自己也匹配进去（[PITFALLS A5](PITFALLS.md)）。

## 八、建议的下一步

按性价比排序：

1. **真机 iPhone Safari 打开一次**（引擎级已由 `--webkit` 覆盖，真机只差地址栏/手势条实感）与**真机安卓各滑一遍手势**。
2. **在 Mac 上跑通一遍**（`./mac/start.sh` + `./mac/autostart.sh install`），把 [5.3](#53-未验证--风险) 里的 macOS 项划掉。
3. **决定是否上 HTTPS**：需要先加一条 DNS A 记录（如 `dsh.example.com` → `<你的服务器IP>`），然后在服务器上建站 + Let's Encrypt + nginx 反代（WebSocket 与 SSE 的 nginx 配置见 README §七）。
4. 外观细节只剩 [5.2](#52-外观细节--功能限制) 的 1（交付物卡）和 4（环形指标）——都要等真实场景出现再量，别盲改。
5. 可选：给仓库加 gitee 远程（目前是纯本地仓库）。

## 九、仓库提交历史

> 下面是**开源前的本地历史**。开源时压缩成单个脱敏初始 commit 推到 GitHub（`main` 分支），
> 这些 hash 只存在于本地 `master`（私有历史，不推送）。

```
a4bf5c3  feat(audit): 组件态全覆盖 + WebKit 冒烟（42 → 64 项断言）
3db3bed  feat(mobile): 滑动手势（边缘右滑开抽屉 / 左滑关）
f7eec94  feat(mobile): 右栏全屏态适配 + 标题行按钮 36px + 触屏隐藏 tooltip
59b707b  feat(mobile): 行内卡片适配（diff 单列 / 审批换行 / 展开区按钮 32px）
3985809  feat(mobile): composer 文本上限随视口 + 审计加轨迹 tab 场景
7c62fba  feat(mobile): 通用原语（16px 输入框 / 菜单 dvh / 模态可滚 / hover 死功能复活）
1772304  fix(audit): 竖排探针排除行内元素（修自身误报）
8ad126b  docs: 完善说明文档 —— 状态看板 + 适配层详解 + 踩坑独立成篇 + 回归审计工具
aad0b41  feat: 跨平台支持（macOS）+ 修 USERPROFILE 路径 bug + 补 Mac 启动器
8859751  fix(mobile): 手机上彻底隐藏侧栏图标轨道，补左上角浮动入口
bfae51f  fix(mobile): 正文列加宽 14%、指标胶囊不再截断、注入资源加版本号
a774c5c  feat: DSH 远程 Web 控制（手机远程控制本机 DSH）
```

**敏感文件按设计不入库**：`remote.config.json`（含明文令牌）、`access.log`、`*.pid`、`tools/out/`。提交前扫一遍暂存区：

```sh
TOK=$(python -c "import json;print(json.load(open('remote.config.json'))['token'])")
git add -A && git grep --cached -n "$TOK" ; echo "exit=$?"   # 期望非 0（无匹配）
```

> 别把令牌（哪怕只是前缀）写进任何被跟踪的文件 —— 包括像上面这种"扫描示例"。
> 写死了前缀会让闸门永远报警，也等于把线索留在历史里。
