# 安全说明

## 这个项目意味着什么风险

它把**本机 DSH GUI 的完整能力**（执行任意命令、读写任意文件）接到公网上，**任何拿到令牌的人就等于拿到了你的电脑**。请按这个前提来用它。

## 部署时必须做到

| 项 | 要求 |
|---|---|
| 令牌 | 用 32 字节随机值（`node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`）；**不要**用短口令或可猜的词 |
| 传输 | 不可信网络（咖啡厅 WiFi 等）下**必须上 HTTPS**（见 README §七）；明文 HTTP 只适合你自己可控的网络 |
| 端口 | 只放行你实际用的那一个；不需要时关掉云安全组规则与 firewalld |
| 服务器 | 只跑你自己的中转；定期打补丁；SSH 用密钥登录、禁用密码 |
| DSH 侧 | 保持它只监听 `127.0.0.1`；**不要**为了图省事给它加 `--host 0.0.0.0` |
| 轮换 | 怀疑泄露就换令牌 + 换 SSH 密钥；`remote.config.json` 里的令牌一换，旧的立刻失效 |

## 已经做的防护

- 代理只监听 `127.0.0.1`，公网流量必须经隧道进来
- 令牌比对用 `timingSafeEqual`（防时序侧信道）
- Cookie 为 `HttpOnly; SameSite=Strict`，跨站请求带不上，CSRF 打不动
- 代理自己吐的适配层资源（`/__dsh-mobile/*`）同样要求令牌
- 访问日志**不记录令牌原文**（自动脱敏成 `<token>`）
- 仓库内**没有任何凭证**：真实配置走 gitignore，且有 pre-commit 脱敏闸门（见 README §十一）

## 上报问题

- 一般问题：开 Issue（**不要**在 Issue 里贴令牌、服务器地址、日志原文）
- 安全问题：用 GitHub 的 [Private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)（仓库 Security 标签页 → Report a vulnerability）

## 免责

本项目按 MIT 许可「原样」提供，不附带任何担保。把本机控制能力暴露到网络上是你自己的决定与风险。
