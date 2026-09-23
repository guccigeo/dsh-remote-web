# DSH 远程 Web 控制

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Mobile audit](https://img.shields.io/badge/mobile%20audit-64%20assertions-success)

> 手机 / 外网浏览器经**自己的服务器**中转控制**本机 DSH GUI**：对话、看会话、跑命令、传文件，和坐在电脑前一样。
> 零额外软件 —— 只用系统自带的 `ssh` 做反向隧道，加一个 Node 写的鉴权代理。

**它解决什么**：DSH GUI 只监听 `127.0.0.1`（并且硬拒绝 `--host 0.0.0.0`），出门在外就用不了。本项目用「SSH 反向隧道 + 一层自己写鉴权的代理」把它安全地接到手机上，并额外注入一套移动端适配层（原生 GUI 是桌面布局，手机上会挤成每行两个字）。

**几个特点**：

- **零依赖**：代理是单个 `.mjs`，只用 Node 内置模块；隧道用系统自带的 `ssh`；不用装任何东西
- **令牌长期稳定**：复刻 DSH 的浏览器会话 cookie（读 `~/.dsh/.credentials.yaml` 里的持久签名密钥自签），手机可以存书签，不必每次重启 DSH 后重新拿令牌
- **不动 DSH 源码**：适配层在代理层注入，桌面 GUI 一个字节都不改；DSH 升级后依然生效
- **有回归测试**：64 项断言、12 组场景（含真 Safari 引擎的 WebKit 冒烟）；改布局前后各跑一次
- **不绕过安全设计**：DSH 的 loopback 绑定与 Host/Origin 栅栏都保留，**对外鉴权由代理承担**

> **⚠️ 自备服务器（self-host）**：本仓库只含客户端侧的核心代码（代理 + 隧道 + 移动端适配层），**不含任何服务器资源**。要用起来，你需要一台自己的公网 Linux 服务器（放行一个端口 + sshd 开 `GatewayPorts clientspecified`），并在本地 `remote.config.json` 里填自己的服务器地址和令牌。

> **⚠️ 提交代码前必读**：[脱敏规则](#十一提交前必读脱敏硬性规则) —— 凭证与个人基础设施信息**永不入库**；仓库带 pre-commit 闸门，命中即拒绝提交。

## 文档索引

| 文档 | 什么时候看 |
|---|---|
| 本页 | 第一次上手：怎么用、怎么装、怎么停、**提交前必读的脱敏规则** |
| **[docs/STATE.md](docs/STATE.md)** | **「现在到哪一步了、接下来从哪继续」** —— 状态看板、实测数值、已知问题、待办 |
| [docs/MOBILE.md](docs/MOBILE.md) | 要动手机端布局：修了什么、DOM 契约、选择器清单、z-index 层级、审计用法 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | 改这块代码前扫一眼，避免重踩 |
| [SECURITY.md](SECURITY.md) | 安全边界与漏洞上报 |

---

## 一、怎么用

### 最短路径（三步）

```sh
# 1. 本机准备：复制配置并填自己的服务器地址 + 令牌
cp remote.config.example.json remote.config.json
node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"   # 生成令牌填进 token 字段

# 2. 起代理 + 起隧道（Windows 用 start-all.bat；macOS/Linux 用下面这条）
./mac/start.sh

# 3. 手机浏览器打开（第一次带 ?token= 换长期 Cookie，之后收藏干净地址即可）
#    http://<你的服务器IP>:17933/?token=<你的令牌>
```

前置条件只有一条：**你自己的服务器**已放行该端口，且 sshd 配了 `GatewayPorts clientspecified`（详见第二节与 [docs/PITFALLS.md](docs/PITFALLS.md) 的 A3/A4）。

### 手机上的交互

手机浏览器打开（**建议添加到主屏幕**，DSH 自带 PWA manifest，会全屏运行像 App）：

```
http://<你的服务器IP>:17933/?token=<你的令牌>
```

> 真实令牌在 `remote.config.json` 的 `token` 字段（该文件已 gitignore，不入库）。

- 打开一次后浏览器会拿到长期 Cookie（29 天），之后**收藏干净地址即可**：`http://<你的服务器IP>:17933/`
- **手机上侧栏默认是隐藏的**（图标轨道常驻占 13% 屏宽，太浪费）—— 点**左上角 ☰** 或**从屏幕左缘向右滑**唤出会话抽屉，选完会话自动收起；抽屉上向左滑也可关闭
- 换手机 / 清了 Cookie / 过了 29 天：再用上面带 `?token=` 的地址访问一次即可
- 令牌也可以走浏览器登录框：**用户名随便填，密码填令牌**
- 本机访问 `http://127.0.0.1:19390` 同样要令牌（隧道流量也是从 127.0.0.1 进来的，无法按来源 IP 放行本机）

## 二、架构

```
手机浏览器 ──HTTP + 稳定令牌──▶ 你的服务器 <服务器IP>:17933
      安全组 → firewalld(17933/tcp) → sshd 监听 0.0.0.0:17933
                          └──SSH 加密隧道──▶ 本机 ssh
                                              └─▶ 本机代理 127.0.0.1:19390（令牌鉴权 + 注入移动端适配层）
                                                    └─ 注入 DSH 会话 cookie ─▶ DSH GUI 127.0.0.1:19387
```

和常见的「内网服务 + 反向隧道」面板是同一套路，只是多了一个**代理**。代理存在的两个理由：

1. **DSH 故意不允许 `--host 0.0.0.0`**（启动时硬拒绝，理由是把 RCE 暴露到网络），`/api` 还有 Host/Origin 栅栏防 DNS rebinding。代理不绕过这些设计，而是把 Host/Origin 规范成 loopback 权威，由代理自己承担对外鉴权。
2. **`dsh web` 打印的 `?token=` 是进程级的**，每次重启 DSH 就换，做不了手机书签。DSH 的浏览器会话 cookie 用的是 `~/.dsh/.credentials.yaml` 里**持久化**的签名密钥，代理读该密钥自行签发 cookie，所以对手机而言令牌是**长期稳定**的。

设计决策的完整理由见 [docs/STATE.md 第七节](docs/STATE.md)。

## 三、文件清单

| 文件 | 作用 |
|---|---|
| `dsh-remote-web.mjs` | 鉴权代理（Node 零依赖）：令牌校验、注入 DSH cookie、HTTP/SSE/WebSocket 透传、注入移动端适配层 |
| `remote.config.json` | **本地配置（不入库）**：令牌、监听端口、上游地址、`mobileEnhance` 开关。首次从 `remote.config.example.json` 复制 |
| `mobile.css` / `mobile.js` | 移动端适配层，由代理注入 `index.html`。详见 [docs/MOBILE.md](docs/MOBILE.md) |
| `run-proxy.ps1` / `run-tunnel.ps1` | Windows 监管启动器（退出自动重启；写 PID 文件） |
| `dsh_remote_web.vbs` | Windows **登录自启**（已装进 `shell:startup`） |
| `start-all.bat` / `stop-all.bat` / `stop-all.ps1` | Windows 手动启动与停止 |
| `mac/start.sh` · `mac/stop.sh` | **macOS / Linux** 的启动与停止（见第六节） |
| `mac/resolve-tunnel.mjs` | 从 `remote.config.json` 推导隧道参数（host / 端口 / 密钥） |
| `mac/autostart.sh` | macOS 登录自启（LaunchAgent 安装 / 卸载 / 查看） |
| **`tools/mobile-audit.py`** | **移动端回归审计**（64 项断言，12 组场景；`--webkit` 追加真 Safari 引擎冒烟）。改布局前后都跑它，见 [docs/MOBILE.md](docs/MOBILE.md) |
| **`tools/check-secrets.py`** | **脱敏闸门**：提交前扫凭证与个人基础设施信息（会拿本机真实令牌/服务器地址逐字比对）。见 [§十一](#十一提交前必读脱敏硬性规则) |
| `.githooks/pre-commit` | 提交钩子：自动跑上面的闸门，命中即拒绝提交。启用：`git config core.hooksPath .githooks` |
| `docs/` | 状态看板 / 适配层详解 / 踩坑记录 |
| `access.log` | 访问日志（令牌已自动脱敏，不落原文） |

> 启动器写成 PowerShell 而非 .bat，是因为停止逻辑原先靠 `Get-CimInstance` 按命令行匹配，而该接口在受限环境下会「拒绝访问」导致静默失败；改成 PID 文件后停止操作不再依赖 WMI。

## 四、常用操作

```bat
:: Windows —— 手动启动（排障用，会弹两个窗口看日志）
start-all.bat
:: 停止
stop-all.bat
```

```sh
# macOS / Linux
./mac/start.sh            # 后台跑
./mac/start.sh foreground # 前台跑，直接看日志
./mac/stop.sh
```

```sh
# 回归审计（改过任何布局/注入逻辑后都该跑）
python tools/mobile-audit.py
python tools/mobile-audit.py --url http://<服务器IP>:17933   # 打公网
python tools/mobile-audit.py --webkit                        # 追加 WebKit iPhone 冒烟（先 playwright install webkit）
```

**换令牌**：改 `remote.config.json` 的 `token` → 重启代理（`stop-all.bat` + `start-all.bat`）→ 手机用新地址 `/?token=新令牌` 访问一次。

**彻底卸载**：
1. 停止进程（Windows `stop-all.bat` / macOS `./mac/stop.sh`）
2. Windows：删掉启动文件夹（`Win+R` → `shell:startup`）里的 `dsh_remote_web.vbs`；macOS：`./mac/autostart.sh uninstall`
3. 服务器还原：`firewall-cmd --remove-port=17933/tcp --permanent && firewall-cmd --reload`
4. 云服务商控制台删掉 17933 的安全组规则
5. 删掉本目录

## 五、安全边界（必读）

DSH GUI 等价于**本机 RCE 能力**（能跑任意命令、读任意文件）。所以：

- **手机 ↔ 服务器这一段是明文 HTTP + 令牌**。在不可信 WiFi 下令牌可能被嗅探。**要消除这个风险就上 HTTPS**（见第七节）。
- **PC ↔ 服务器这一段全程 SSH 加密**，复用 `~/.ssh/id_ed25519` 之类的已有密钥。
- 代理只监听 `127.0.0.1:19390`，公网只能经隧道进来。
- 令牌比对用 `timingSafeEqual`；Cookie 是 `HttpOnly; SameSite=Strict`，跨站请求带不上 Cookie，因此 CSRF 打不动。
- 代理自己吐的适配层资源（`/__dsh-mobile/*`）**同样要求令牌**。
- 日志**不记录令牌原文**（自动脱敏成 `<redacted>`）。
- **令牌泄露 = 电脑被控**。不要外传地址，不要贴群里。

## 六、在 macOS 上使用

**结论：核心逻辑通用，进程管理换一套脚本即可。** 仓库里已按平台分好：

| 部分 | Windows | macOS / Linux | 说明 |
|---|---|---|---|
| 代理 `dsh-remote-web.mjs` | ✅ | ✅ | 纯 Node。凭证路径用 `os.homedir()`，两端都对 |
| 适配层 `mobile.css` / `mobile.js` | ✅ | ✅ | 纯浏览器，与系统无关 |
| 反向隧道 | `run-tunnel.ps1` | `mac/start.sh` | 都是 `ssh -R`；macOS 自带 `/usr/bin/ssh` |
| 启动 / 停止 | `start-all.bat` / `stop-all.bat` | `mac/start.sh` / `mac/stop.sh` | |
| 登录自启 | 启动文件夹 `dsh_remote_web.vbs` | `mac/autostart.sh install`（LaunchAgent） | |
| 按命令行杀进程兜底 | `stop-all.ps1`（`Get-CimInstance`） | 不需要 | mac 版只靠 PID 文件，无 WMI 依赖 |

### Mac 上三步

```sh
# 1. 配置（remote.config.json 不入库，要自己带过去）
cp remote.config.example.json remote.config.json
#    令牌可沿用 Windows 那份（同一个 DSH 账号）；也可以新生成一个，
#    但换令牌后手机要用新的 ?token= 地址重新访问一次。
#    若 ssh 密钥名不是默认的 ~/.ssh/id_ed25519，补上：
#      "tunnel": { "identityFile": "~/.ssh/你的密钥" }

# 2. 启动
chmod +x mac/*.sh
./mac/start.sh                # 后台跑，各自带监管自动重启
./mac/start.sh foreground     # 或前台跑，直接看日志

# 3. 登录自启（可选）
./mac/autostart.sh install    # 卸载: ./mac/autostart.sh uninstall
```

停止：`./mac/stop.sh`

### Mac 上要注意的三点

1. **DSH 的端口不一定和 Windows 一样**（Windows 上实测是 19387）。代理读配置的 `upstream`；**留空时**会先看环境变量 `DSH_WEB_URL`（DSH 注入给 shell 的），再退回 `127.0.0.1:19387`。最省事的做法是**从 DSH 里启动代理**（`node dsh-remote-web.mjs`），或先 `echo $DSH_WEB_URL` 看端口再填进配置。
2. **ssh 密钥名**。Windows 上在 `tunnel.identityFile` 里指定（例如 `C:/Users/<你>/.ssh/id_ed25519`）；Mac 若是默认的 `~/.ssh/id_ed25519` 无需配置。
3. **服务器侧零改动**。`GatewayPorts clientspecified`、firewalld、安全组都在服务器上，与你本机是 Windows 还是 Mac 无关。因此 **Windows 和 Mac 不能同时启动** —— 会抢同一个远端端口 17933。换机器前先 `stop-all.bat` / `./mac/stop.sh`。

> ⚠️ **Mac 路径尚未真机验证**：脚本语法（`sh -n`）与隧道参数推导已测通，但 `ssh -R` 与 LaunchAgent 必须在 Mac 上跑一次才算数。见 [docs/STATE.md 5.3](docs/STATE.md)。

## 七、想升级成 HTTPS（可选）

目前是明文。要加密的话（推荐，且手机浏览器不会报证书警告）：

1. DNS 加一条 A 记录，例如 `dsh.example.com` → `<你的服务器IP>`
2. 在服务器上建站（nginx 或面板工具均可），申请 Let's Encrypt 证书
3. nginx 反代到隧道端口，注意三件事（默认配置都不满足）：
   - WebSocket 要 `proxy_http_version 1.1;` + `proxy_set_header Upgrade $http_upgrade;` + `proxy_set_header Connection "upgrade";`（DSH 的 `/api/remote.mux` 走 WS）
   - SSE 要 `proxy_buffering off;` + `proxy_read_timeout 600s;`
   - `proxy_set_header Host $host;` 保持原样即可（代理会把 Host 规范成 loopback）

   然后手机改用 `https://dsh.example.com/?token=...`。

## 八、排障

| 现象 | 原因 / 处理 |
|---|---|
| 手机打不开（超时） | 先看是不是安全组没放行：`curl -o NUL -w "%{http_code}" http://<服务器IP>:17933/` 返回 000 = 不通。本机开 VPN 也会导致打公网地址超时（路由绕行），关掉即通 |
| 手机 401 | 令牌不对，或 Cookie 过期 → 用带 `?token=` 的地址重新访问一次 |
| 页面 502 / 提示连不上上游 | DSH 没在跑。代理只管转发，DSH 关了它也没辙 |
| 页面提示「DSH 拒绝了代理注入的会话 cookie」 | DSH 的 browser-session 密钥被轮换过。代理会自动换新 cookie，刷新重试；仍不行就重启代理 |
| 隧道断了 | 监管器会自动重连（最多 60 秒）。彻底没救时停止 + 重新启动 |
| 想确认隧道活着 | 服务器上 `ss -ltnp \| grep 17933` 应看到 sshd 监听 `0.0.0.0:17933` |
| 移动端适配没生效 | 强刷页面（注入资源已带 `?v=<mtime>` 版本号，普通刷新即可）。仍不行看 `/__dsh-mobile/mobile.css` 能否打开（应 200） |
| 适配层改坏了布局 | 临时把 `remote.config.json` 的 `mobileEnhance` 设 `false` 重启代理，回退到原生布局 |
| **手机端布局出问题** | 跑 `python tools/mobile-audit.py`，看是哪段断言 FAIL；DOM 契约 FAIL 说明 DSH 可能改版，见 [docs/MOBILE.md](docs/MOBILE.md) |
| 手机上点不到某按钮 | 检查 z-index 层级 —— 抽屉/遮罩必须高于作曲家的 9、低于 App 浮层的 20。见 [docs/MOBILE.md 第五节](docs/MOBILE.md) |

## 九、踩过的坑

**已迁到 [docs/PITFALLS.md](docs/PITFALLS.md)**（按「服务器/隧道 · 移动端适配 · 跨平台 · 工具流程」分四组，共 30 余条）。

改代码前建议扫一眼对应分组。几条最容易重踩的：

- `.bat` / `.vbs` 里**只写 ASCII**（GBK 读 UTF-8 会乱码切行）
- 别用 PowerShell 的 `Get-Content`/`Set-Content` 批量改 UTF-8 文件（会把中文双重编码写坏）
- 给 grid item 设 `position:absolute` 后必须显式钉 `grid-column`
- 加浮层先查 z-index 层级表，别猜

## 十、开源许可

[MIT](LICENSE)。本仓库只含客户端侧核心代码；**中继服务器需自备**（见顶部「自备服务器」）。文档中的服务器地址、域名、路径均为占位符，请按自己的环境替换。

## 十一、提交前必读：脱敏（硬性规则）

> **一句话：凭证与个人基础设施信息永不入库。** 这是公开仓库，一次手滑就是永久泄露 —— 历史、缓存和别人的 fork 不会因为你事后删除而消失。

### 绝对不能提交的东西

| 类别 | 例子 | 正确做法 |
|---|---|---|
| 远程访问令牌 | `remote.config.json` 的 `token`（**连前几位都不行**） | 只放 `remote.config.json`（已 gitignore） |
| 服务器地址 | 公网 IP、`root@<host>`、自己的域名 | 占位符 `<你的服务器IP>`、`dsh.example.com` |
| 云厂商 / 面板指纹 | 安全组放行端口清单、服务器内网段 | 泛化描述（如「默认只放行 22/80/443」） |
| 个人路径与身份 | `C:\Users\<真名>`、`/home/<真名>`、邮箱 | `<你>`、`~`、`%USERPROFILE%` |
| 密钥与证书 | `*.pem` / `*.key` / `id_ed25519` 私钥 | 绝不入库（`.gitignore` 已兜底） |
| 其它私有项目 | 项目名、它的端口、它的服务器 | 泛化或删除 |

### 闸门（不靠自觉）

```sh
python tools/check-secrets.py            # 扫全部被跟踪文件
python tools/check-secrets.py --staged   # 只扫暂存区（pre-commit 钩子用这个）
python tools/check-secrets.py --all      # 连未跟踪文件一起扫（发布前用）

git config core.hooksPath .githooks      # 启用 pre-commit 钩子（每个 clone 做一次）
```

- 命中即**拒绝提交**，输出只打**掩码**（前 2 位 + 长度），不会把密钥原文打到终端或日志
- 闸门会从本机 `remote.config.json`（不入库）读出**真实令牌、令牌前 8 位、服务器主机名**逐字比对
- 还能屏蔽只属于你的词：在该文件里加 `"scrubWords": ["别的项目名", "我的域名"]`

### 占位符规范（照这样写就不会命中）

| 场景 | 用 |
|---|---|
| 服务器 | `<你的服务器IP>` |
| 域名 | `dsh.example.com` |
| 本机路径 | `%USERPROFILE%\.dsh\remote-web`、`~/...`、`C:/Users/<你>/.ssh/id_ed25519` |
| 令牌 | `<你的令牌>` |
| 邮箱 | 不写；必须写时用 `noreply@users.noreply.github.com` |

### 万一已经提交了敏感内容

1. **先轮换**：换令牌、换 SSH 密钥（按「已泄露」处理，历史/缓存/fork 都撤不回来）
2. 改文件、再提交（清掉当前版本）
3. 清历史：`git filter-repo` 重写，或像本项目开源时那样**压成单个全新初始提交**
4. 强推前确认远端没有别人基于它拉出的分支

### 本项目的分支约定

| 分支 | 用途 |
|---|---|
| `main` | **公开分支**，只放脱敏后的内容（跟踪 `origin/main`） |
| `master` | **本地私有历史**（含开发期真实地址），**永不可推送**；无 upstream，`git push` 不会碰它 |
