#!/usr/bin/env python
"""脱敏闸门：提交前检查待入库内容里有没有凭证 / 个人基础设施信息。

为什么要有它：README 里写规则只能提醒人，拦不住手滑。这个脚本把「不能入库的
东西」变成可执行的检查，并被 .githooks/pre-commit 调用 —— 不通过就提交不了。

检查两类东西：
  1. 通用模式：非 loopback 的 IPv4、邮箱、本机绝对路径（C:/Users/<名字>、
     /Users/<名字>/、/home/<名字>/）、私钥头。
  2. 你本机的真实秘密：从 remote.config.json（不入库）读出
     令牌全文 / 令牌前 8 位 / publicBaseUrl 与 tunnel.host 里的主机名，
     以及可选的 scrubWords 列表（见下），逐字比对。

scrubWords（可选）：在 remote.config.json 里加
    "scrubWords": ["别的项目名", "我的域名"]
用来额外屏蔽只属于你的词。脚本里不硬编码任何个人信息。

用法：
    python tools/check-secrets.py            # 扫全部被 git 跟踪的文件
    python tools/check-secrets.py --staged   # 只扫本次暂存的文件（pre-commit 用）
    python tools/check-secrets.py --all      # 连未跟踪文件一起扫（更严格，发布前用）

退出码：0 = 干净；1 = 有命中（提交应被阻止）；2 = 环境问题（比如不在 git 仓库里）。

输出只打掩码（前 2 位 + 长度），不会把密钥原文打到终端或日志里。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# 允许出现的地址/邮箱：回环、未指定、文档示例段（RFC 5737）、示例域名
ALLOW_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
DOC_IP_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")
ALLOW_EMAIL_DOMAINS = ("example.com", "users.noreply.github.com", "example.org")

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 用户名捕获要停在路径分隔符、空白、引号、括号和 CJK 标点上 ——
# 否则注释里「C:\Users\X、macOS 是 /Users/x。」会把「、macOS」当成用户名（真踩过）。
HOME_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)"
    r"([^\\/\s\"'`()\u3001\u3002\uff0c\uff1b\uff1a\uff09\uff08]+)"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

RULES = {
    "server-ip": "非 loopback 的 IP —— 换成占位符 <你的服务器IP>",
    "email": "邮箱 —— 换成占位符或删除",
    "home-path": "本机绝对路径 —— 换成 %USERPROFILE% / ~ / <你>",
    "private-key": "私钥内容 —— 绝不入库",
    "token": "真实令牌 —— 绝不入库",
    "token-prefix": "令牌片段 —— 连前几位也不要留",
    "scrub-word": "自定义屏蔽词命中",
}


def run_git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def local_secrets() -> dict:
    """从本机 remote.config.json 读真实秘密（该文件不入库）。"""
    out = {"token": "", "token_prefix": "", "hosts": [], "scrub_words": []}
    cfg_path = ROOT / "remote.config.json"
    if not cfg_path.exists():
        return out
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    tok = str(cfg.get("token") or "")
    if tok and not tok.startswith("<"):
        out["token"] = tok
        out["token_prefix"] = tok[:8]
    hosts = []
    base = str(cfg.get("publicBaseUrl") or "")
    m = re.search(r"//([^/:]+)", base)
    if m:
        hosts.append(m.group(1))
    tunnel_host = str((cfg.get("tunnel") or {}).get("host") or "")
    if tunnel_host:
        hosts.append(tunnel_host.split("@")[-1])
    out["hosts"] = [h for h in hosts if h and not h.startswith("<")]
    words = cfg.get("scrubWords") or []
    if isinstance(words, list):
        out["scrub_words"] = [str(w) for w in words if str(w).strip()]
    return out


def mask(value: str) -> str:
    v = value.strip()
    if len(v) <= 2:
        return "*" * len(v)
    return v[:2] + "*" * min(len(v) - 2, 12) + f"(len={len(v)})"


def mask_ip(ip: str) -> str:
    parts = ip.split(".")
    return parts[0] + ".***.***.***" if len(parts) == 4 else mask(ip)


def allowed_ip(ip: str) -> bool:
    return ip in ALLOW_IPS or ip.startswith(DOC_IP_PREFIXES)


def allowed_email(addr: str) -> bool:
    domain = addr.split("@")[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOW_EMAIL_DOMAINS)


def allowed_home_user(name: str) -> bool:
    # 占位符（<你> / <user>）与文档里的示意名（X / x / user）不算泄漏
    if name.startswith("<") or len(name) <= 1:
        return True
    return name.lower() in {"user", "username", "you", "yourname", "someuser"}


def scan_text(text: str, secrets: dict) -> list[tuple[str, str, str]]:
    """返回 [(rule, masked, 说明)]。"""
    hits: list[tuple[str, str, str]] = []

    if secrets["token"] and secrets["token"] in text:
        hits.append(("token", mask(secrets["token"]), RULES["token"]))
    elif secrets["token_prefix"] and len(secrets["token_prefix"]) >= 8 and secrets["token_prefix"] in text:
        hits.append(("token-prefix", mask(secrets["token_prefix"]), RULES["token-prefix"]))

    for host in secrets["hosts"]:
        if host in text:
            hits.append(("server-ip", mask_ip(host) if IPV4.fullmatch(host) else mask(host), RULES["server-ip"]))

    for ip in set(IPV4.findall(text)):
        if not allowed_ip(ip):
            hits.append(("server-ip", mask_ip(ip), RULES["server-ip"]))

    for addr in set(EMAIL.findall(text)):
        if not allowed_email(addr):
            hits.append(("email", mask(addr), RULES["email"]))

    for name in set(HOME_PATH.findall(text)):
        if not allowed_home_user(name):
            hits.append(("home-path", mask(name), RULES["home-path"]))

    if PRIVATE_KEY.search(text):
        hits.append(("private-key", "-----BEGIN ... PRIVATE KEY-----", RULES["private-key"]))

    for word in secrets["scrub_words"]:
        if word and word in text:
            hits.append(("scrub-word", mask(word), RULES["scrub-word"]))

    return hits


def tracked_files(include_untracked: bool) -> list[Path]:
    if include_untracked:
        raw = run_git("ls-files", "--cached", "--others", "--exclude-standard")
    else:
        raw = run_git("ls-files")
    return [ROOT / line for line in raw.splitlines() if line.strip()]


def staged_files() -> list[Path]:
    raw = run_git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [ROOT / line for line in raw.splitlines() if line.strip()]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    argv = sys.argv[1:]
    mode = "tracked"
    if "--staged" in argv:
        mode = "staged"
    if "--all" in argv:
        mode = "all"

    try:
        files = staged_files() if mode == "staged" else tracked_files(mode == "all")
    except RuntimeError as exc:
        print(f"[check-secrets] 无法读取 git 状态：{exc}", file=sys.stderr)
        return 2

    secrets = local_secrets()
    findings: list[str] = []
    scanned = 0

    for path in files:
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue  # 本脚本自身含规则样例，跳过（规则见文件头注释）
        try:
            data = path.read_bytes()
        except Exception:
            continue
        if b"\x00" in data[:4096]:
            continue  # 二进制
        text = data.decode("utf-8", errors="replace")
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, shown, hint in scan_text(line, secrets):
                findings.append(f"  {path.relative_to(ROOT)}:{lineno}  [{rule}] {shown}  -> {hint}")

    if findings:
        print(f"[check-secrets] 发现 {len(findings)} 处需要脱敏（模式：{mode}）：")
        for item in findings:
            print(item)
        print()
        print("处理办法：换成占位符（<你的服务器IP> / <你> / ~ / %USERPROFILE%），")
        print("或把整份文件加进 .gitignore。真实凭证只放 remote.config.json（已 gitignore）。")
        return 1

    print(f"[check-secrets] 干净：已扫 {scanned} 个文件（模式：{mode}）"
          + ("，含本机真实令牌/服务器地址比对" if secrets["token"] or secrets["hosts"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
