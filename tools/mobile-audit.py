#!/usr/bin/env python
"""DSH 远程 Web 控制 —— 移动端适配层回归测试。

为什么要有这个脚本：适配层是一堆针对 DSH 前端 DOM 的覆盖样式，DSH 一升级
就可能失效。之前每次优化都是临时写探针、用完即弃，下次再从零查一遍。
这里把「应该成立的不变量」固化成断言，改完跑一次就知道有没有回归。

用法：
    python tools/mobile-audit.py                 # 打本机代理 127.0.0.1:19390
    python tools/mobile-audit.py --url http://<服务器IP>:17933
    python tools/mobile-audit.py --keep-open     # 保留浏览器（调试用）

依赖：Python 3 + Playwright
    pip install playwright && playwright install chromium

令牌从 remote.config.json 读取（该文件不入库，所以脚本里没有密钥）。

退出码：0 = 全部断言通过；1 = 有失败项（可直接接 CI / 或改完自己看）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "out"

# 审计用的设备参数。420x933 对齐用户手机截图（1260x2800 @ dpr3）。
PHONE = {
    "viewport": {"width": 420, "height": 933},
    "device_scale_factor": 2,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36"
    ),
}
DESKTOP = {"viewport": {"width": 1440, "height": 900}}
# iPhone 13/14 逻辑视口。--webkit 时跑核心组，验证 dvh/safe-area/弹性布局在
# 真 Safari 引擎下的表现（Chromium 模拟不出 WebKit 的差异）。
IPHONE = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    ),
}


# ── 页面内取数 ──────────────────────────────────────────────────────────────
STATE_JS = """() => {
  const q = s => document.querySelector(s);
  const frame = q('[data-slot="root"] > [class*="_frame"]');
  const side = q('[data-slot="root"] > [class*="_frame"] > [class*="_sidebarCol"]');
  const center = q('[data-slot="root"] > [class*="_frame"] > [class*="_centerCol"]');
  const burger = q('[data-dshm-hamburger]');
  const col = q('[class*="_column"]');
  const metricsRow = q('[class*="_composerSeat"] *:has(> [class*="_anchor"])');
  const box = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return { w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y) }; };
  const titleRowEl = q('[data-slot="root"] > [class*="_frame"] > [class*="_centerCol"] [class*="_titleRow"]');
  const input = q('[class*="_composerSeat"] textarea, [class*="_composerSeat"] [contenteditable]:not([contenteditable="false"]), [class*="_composerSeat"] [class*="_input"]');
  return {
    inputFontSize: input ? parseFloat(getComputedStyle(input).fontSize) : null,
    vw: innerWidth, vh: innerHeight,
    grid: frame ? getComputedStyle(frame).gridTemplateColumns : null,
    collapsed: frame ? frame.hasAttribute('data-sidebar-collapsed') : null,
    sidebar: box(side), sidebarPos: side ? getComputedStyle(side).position : null,
    center: box(center),
    messageCol: box(col),
    burgerExists: !!burger,
    burgerShown: burger ? !burger.hasAttribute('hidden') : false,
    burgerBox: box(burger),
    titleRow: box(titleRowEl),
    // 标题的**内容**左边缘 = 盒子左边 + 内边距。汉堡要避让的是内容，不是盒子。
    titleRowContentX: titleRowEl
      ? Math.round(titleRowEl.getBoundingClientRect().left + parseFloat(getComputedStyle(titleRowEl).paddingLeft))
      : null,
    backdrop: !!q('[data-dshm-backdrop]'),
  };
}"""

# DOM 契约检查：适配层赖以为生的锚点还在不在。
# 这些一旦变了，说明 DSH 前端改版，适配层要复查（不是 bug，是提醒）。
# 分两批：BASE 在页面加载后就能查；LIST 要等侧栏/抽屉渲染出来才存在。
CONTRACT_BASE_JS = """() => {
  const need = [
    ['frame',        '[data-slot="root"] > [class*="_frame"]'],
    ['sidebarCol',   '[data-slot="root"] > [class*="_frame"] > [class*="_sidebarCol"]'],
    ['centerCol',    '[data-slot="root"] > [class*="_frame"] > [class*="_centerCol"]'],
    ['rightbarCol',  '[data-slot="root"] > [class*="_frame"] > [class*="_rightbarCol"]'],
    ['sidebarToggle','button[class*="_toggle"]'],
    ['composerSeat', '[class*="_composerSeat"]'],
    ['scrollBody',   '[class*="_scrollBody"]'],
    ['titleRow',     '[class*="_titleRow"]'],
  ];
  const out = {};
  for (const [name, sel] of need) out[name] = !!document.querySelector(sel);
  const frame = document.querySelector('[data-slot="root"] > [class*="_frame"]');
  out._frameAttrs = frame ? [...frame.attributes].map(a => a.name).filter(n => n !== 'class' && n !== 'style') : null;
  return out;
}"""

CONTRACT_LIST_JS = """() => {
  // navList 只在设置对话框里存在，不在这里查（见设置那一段）
  const need = [
    ['sessionRow', '[class*="_sessionRow"]'],
    ['projectRow', '[class*="_projectRow"]'],
  ];
  const out = {};
  for (const [name, sel] of need) out[name] = !!document.querySelector(sel);
  return out;
}"""

# 挤压 / 截断 / 重叠探测（判据见 docs/MOBILE.md）
PROBE_JS = """() => {
  const vw = innerWidth;
  const cls = el => (typeof el.className === 'string' ? el.className : '');
  const desc = el => el.tagName.toLowerCase() + '.' + cls(el).slice(0, 44);
  const issues = { overflow: [], verticalWrap: [], truncated: [], overlap: [] };

  // 被祖先 overflow 裁掉的元素，getBoundingClientRect 仍返回原始布局矩形，
  // 会制造大量假重叠（典型：侧栏轨道收到 0 宽后，里面的按钮"压"到正文上）。
  const clippedOut = el => {
    const er = el.getBoundingClientRect();
    let p = el.parentElement;
    while (p && p !== document.documentElement) {
      const pcs = getComputedStyle(p);
      if (pcs.overflow !== 'visible') {
        const pr = p.getBoundingClientRect();
        if (pr.width < 1 || pr.height < 1) return true;
        if (er.right <= pr.left || er.left >= pr.right) return true;
      }
      p = p.parentElement;
    }
    return false;
  };

  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden') continue;

    // 越过视口右缘，且没有可横向滚动的祖先。
    // 但「被 overflow:hidden 祖先裁掉」不算页面溢出 —— 内容根本不可见，
    // 是设计好的截断（典型：轨迹 tab 的 payload 行，span 排 4000px 宽但
    // 被 .i536ba_resultRequest 裁成 180px 省略号）。fixed 元素不受祖先裁剪。
    if (r.left < vw - 2 && r.right > vw + 1 && cs.position !== 'fixed') {
      let p = el.parentElement, scrollable = false, clippedRight = false;
      while (p && p !== document.documentElement) {
        const pcs = getComputedStyle(p);
        if ((pcs.overflowX === 'auto' || pcs.overflowX === 'scroll') && p.scrollWidth > p.clientWidth + 1) { scrollable = true; break; }
        if ((pcs.overflowX === 'hidden' || pcs.overflowX === 'clip') && r.right > p.getBoundingClientRect().right + 1) { clippedRight = true; break; }
        p = p.parentElement;
      }
      if (!scrollable && !clippedRight) issues.overflow.push({ el: desc(el), right: Math.round(r.right), text: (el.textContent || '').trim().slice(0, 26) });
    }

    // 文字竖排：窄 + 高。
    // 排除表格单元格（td/th 本来就会换行）与代码块（长路径/长命令换行是正常的）。
    // 再排除行内元素：<strong>/<a>/<em> 这类 inline 的联合矩形天然"窄而高"
    // （从行中跨到下一行），是正常排版不是竖排 —— 真踩过：会话正文里一句带
    // <strong> 的话触发了两连 FAIL。竖排 bug（设置内容 136px）发生在块级元素上。
    const isCell = /^(TD|TH|CODE|PRE|KBD|SAMP)$/.test(el.tagName);
    const isInline = cs.display.startsWith('inline');
    const text = (el.textContent || '').trim();
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
    if (!isCell && !isInline && text.length >= 4 && el.children.length <= 2 && r.width < 175 && lh > 0 && r.height > lh * 2.6) {
      issues.verticalWrap.push({ el: desc(el), w: Math.round(r.width), h: Math.round(r.height), text: text.slice(0, 22) });
    }

    // 被 ellipsis 截断。
    // 白名单：折叠摘要 / 会话标题面包屑 / 文件路径与文件链接 / 胶囊标签 —— 本就是单行省略。
    // （_fileLink 是工具行里的路径按钮，420px 下长路径省略是设计行为；实测 w294/sw311。）
    // 另跳过宽度 <=2 的元素：那是 visuallyHidden 之类给读屏用的，本来就该被裁。
    if (el.children.length === 0 && cs.overflow === 'hidden' && el.scrollWidth > el.clientWidth + 2 &&
        text.length > 2 && r.width > 2) {
      const c = cls(el);
      const byDesign = /_summary|_paths$|_crumb|_label$|_count$|_fileLink|_filePath|visuallyHidden/i.test(c);
      if (!byDesign) issues.truncated.push({ el: desc(el), w: Math.round(r.width), scrollW: el.scrollWidth, text: text.slice(0, 26) });
    }
  }

  // 真实重叠：只比较**同一个父元素下**的兄弟节点。
  // 为什么收紧到同父：sticky 表头与滚到它下面的内容在几何上也"重叠"，
  // 但那是正常的层叠（表头盖住内容），不是布局 bug。之前正是这个假阳性。
  const nodes = [...document.querySelectorAll('[class*="_anchor"], [class*="_pill"], [class*="_trigger"], button')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && !clippedOut(e); });
  const byParent = new Map();
  for (const n of nodes) {
    const key = n.parentElement;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(n);
  }
  for (const group of byParent.values()) {
    for (let i = 0; i < group.length; i++) for (let j = i + 1; j < group.length; j++) {
      const a = group[i], b = group[j];
      const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
      const ox = Math.min(ar.right, br.right) - Math.max(ar.left, br.left);
      const oy = Math.min(ar.bottom, br.bottom) - Math.max(ar.top, br.top);
      if (ox > 3 && oy > 3) issues.overlap.push({ a: desc(a), b: desc(b), ox: Math.round(ox), oy: Math.round(oy) });
    }
  }
  return issues;
}"""


# ── 断言框架 ────────────────────────────────────────────────────────────────
class Report:
    """FAIL = 必须修；WARN = 已知可接受（列出来但不影响退出码）。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.failed = 0
        self.warned = 0

    def check(self, group: str, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((group, name, "PASS" if ok else "FAIL", detail))
        if not ok:
            self.failed += 1

    def warn(self, group: str, name: str, detail: str = "") -> None:
        self.rows.append((group, name, "WARN", detail))
        self.warned += 1

    def dump(self) -> None:
        group = None
        for g, name, status, detail in self.rows:
            if g != group:
                print(f"\n-- {g} " + "-" * max(0, 62 - len(g)))
                group = g
            print(f"  {status}  {name:<44} {detail}")
        print(f"\n{'=' * 72}")
        print(f"共 {len(self.rows)} 项，失败 {self.failed} 项，已知可接受 {self.warned} 项")
        print(f"{'=' * 72}")


def open_settings(page) -> dict:
    """打开设置对话框，返回 {openedBy, hittable}。

    分两步，各测各的：

    1) `hittable` —— 用 elementFromPoint 检查设置入口中心点的最上层元素是否就是
       它自己（或其后代）。这才是「用户手指点得着吗」的正确判据。
    2) 打开动作本身用 JS `.click()`。原因：Playwright 的 `locator.click()` 在
       移动端模拟下动作性检查过度保守（按钮无遮挡却一直超时）；改用
       `touchscreen.tap` 按坐标点又会因坐标/动画时序打到背后的正文元素
       （实测误开了作曲家的"会话统计"浮层）。JS click 确定性强，
       而「能否点到」已经由第 1 步单独覆盖。

    另注意：侧栏同时渲染折叠态与展开态两套 DOM，DOM 里更早的同名按钮是隐藏的，
    所以必须先筛出可见的那个。
    """
    probe = page.evaluate(
        """() => {
             const btns = [...document.querySelectorAll('button')].filter(b => {
               const r = b.getBoundingClientRect(); return r.width > 2 && r.height > 2; });
             const set = btns.find(b => (b.getAttribute('aria-label') || '') === '设置'
                                     || (b.textContent || '').trim() === '设置');
             if (!set) return { found: false };
             const r = set.getBoundingClientRect();
             const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
             const top = document.elementFromPoint(cx, cy);
             return { found: true, cx, cy,
                      hittable: !!top && (top === set || set.contains(top)),
                      topCls: top ? (typeof top.className === 'string' ? top.className : '').slice(0, 40) : null };
           }"""
    )
    if not probe.get("found"):
        raise RuntimeError("抽屉里找不到可见的设置按钮")

    page.evaluate(
        """() => {
             const btns = [...document.querySelectorAll('button')].filter(b => {
               const r = b.getBoundingClientRect(); return r.width > 2 && r.height > 2; });
             const set = btns.find(b => (b.getAttribute('aria-label') || '') === '设置'
                                     || (b.textContent || '').trim() === '设置');
             if (set) set.click();
           }"""
    )

    opened = False
    for _ in range(24):
        if page.evaluate(
            "() => !!document.querySelector('[class*=\"_overlay\"]:not([class*=\"_overlayLayer\"]) > [class*=\"_panel\"]')"
        ):
            opened = True
            break
        page.wait_for_timeout(250)
    if not opened:
        raise RuntimeError("JS click 之后设置对话框没出现")

    return {"openedBy": "js-click", "hittable": bool(probe.get("hittable")), "topCls": probe.get("topCls")}


def reset_mobile_ui(page) -> str:
    """把 App 可能恢复出来的全屏浮层收掉，让后续步骤从确定状态开始。

    为什么需要：DSH 会持久化界面状态 —— 实测重启 DSH 后页面加载时**右栏是全屏
    打开的**。此时适配层按设计把汉堡按钮藏起来（汉堡 z-55 会压住面板 z-40 的
    顶栏），审计若直接点汉堡就会超时（踩过）。这里先收起面板再继续。
    """
    state = page.evaluate(
        """() => {
             // 判据同 mobile.js：认「收起右侧边栏」按钮是否真的在视口内。
             // 只看面板容器会误判 —— 新版 DSH 关闭态也把容器留在屏内（内容被推出）。
             const btn = [...document.querySelectorAll('button')]
               .find(b => (b.getAttribute('aria-label') || '').includes('收起右侧边栏'));
             if (!btn) return { panelOpen: false };
             const r = btn.getBoundingClientRect();
             return { panelOpen: r.width > 2 && r.left >= -1 && r.right <= innerWidth + 1 };
           }"""
    )
    if not state.get("panelOpen"):
        return ""
    page.evaluate(
        """() => {
             const btn = [...document.querySelectorAll('button')]
               .find(b => (b.getAttribute('aria-label') || '').includes('收起右侧边栏'));
             if (btn) btn.click();
           }"""
    )
    page.wait_for_timeout(1500)
    still = page.evaluate(
        """() => {
             const btn = [...document.querySelectorAll('button')]
               .find(b => (b.getAttribute('aria-label') || '').includes('收起右侧边栏'));
             if (!btn) return false;
             const r = btn.getBoundingClientRect();
             return r.width > 2 && r.left >= -1 && r.right <= innerWidth + 1;
           }"""
    )
    return "右栏浮层" + ("已收起" if not still else "收起失败（仍在）")


def main() -> int:
    # 控制台可能是 GBK（中文 Windows），遇到 GBK 编不了的符号会直接抛
    # UnicodeEncodeError 把整个审计打断。统一切 UTF-8 + 不可编码就替换。
    # （本文件自身也保持只含 GBK 可编码字符，免得源码在那类终端里打印出错。）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    argv = sys.argv[1:]
    url_override = None
    keep_open = "--keep-open" in argv
    want_webkit = "--webkit" in argv
    if "--url" in argv:
        url_override = argv[argv.index("--url") + 1]

    config_path = ROOT / "remote.config.json"
    if not config_path.exists():
        print(f"找不到 {config_path}；先复制 remote.config.example.json 并填令牌", file=sys.stderr)
        return 2
    config = json.loads(config_path.read_text(encoding="utf-8"))

    token = config["token"]
    base = url_override or "http://127.0.0.1:%d" % (config.get("listenPort", 19390))
    url = f"{base}/?token={token}"

    OUT.mkdir(parents=True, exist_ok=True)
    rep = Report()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not keep_open)

        # ─────────── 手机视口 ───────────
        ctx = browser.new_context(**PHONE)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)

        contract = page.evaluate(CONTRACT_BASE_JS)
        cg = "DOM 契约（失败了说明 DSH 可能改版，适配层要复查）"
        for name, ok in contract.items():
            if name.startswith("_"):
                continue
            rep.check(cg, name, bool(ok), "" if ok else "选择器没命中")
        rep.check(cg, "frame 状态属性", bool(contract.get("_frameAttrs")),
                  f"attrs={contract.get('_frameAttrs')}")

        # 1) 加载态：侧栏轨道应彻底消失
        g = "手机 · 加载态"
        reset_note = reset_mobile_ui(page)
        if reset_note:
            rep.warn(g, "App 恢复了右栏全屏面板", reset_note + "（否则汉堡按钮被藏、点不到）")
        s = page.evaluate(STATE_JS)
        rep.check(g, "grid 第一轨为 0", s["grid"].startswith("0px"), f"grid={s['grid']}")
        rep.check(g, "侧栏列宽 ≤2px", (s["sidebar"] or {}).get("w", 99) <= 2, f"sidebar={s['sidebar']}")
        rep.check(g, "主区域占满视口", (s["center"] or {}).get("w", 0) >= s["vw"] - 2, f"center={s['center']}")
        rep.check(g, "汉堡按钮存在且可见", s["burgerExists"] and s["burgerShown"], f"shown={s['burgerShown']}")
        bb, tb = s["burgerBox"], s["titleRow"]
        if bb and tb and s["titleRowContentX"] is not None:
            rep.check(g, "汉堡不压会话标题", bb["x"] + bb["w"] <= s["titleRowContentX"] + 2,
                      f"汉堡右={bb['x'] + bb['w']} 标题内容左={s['titleRowContentX']}")
        page.screenshot(path=str(OUT / "phone-01-loaded.png"))

        # 2) 打开一个会话，检查正文宽度 / 挤压 / 截断 / 重叠
        rows = page.locator('[data-slot="root"] [role="treeitem"]')
        opened = False
        page.locator('[data-dshm-hamburger]').first.click(timeout=5000)
        page.wait_for_timeout(1500)

        # 侧栏渲染出来了，这时才能查列表相关的契约元素
        for name, ok in page.evaluate(CONTRACT_LIST_JS).items():
            rep.check(cg, name, bool(ok), "" if ok else "选择器没命中（抽屉打开后仍找不到）")

        for i in range(min(rows.count(), 40)):
            try:
                t = rows.nth(i).inner_text(timeout=1000)
            except Exception:
                continue
            if ("分钟" in t or "小时" in t or "天" in t) and "新建会话" not in t:
                # 点标题而不是行中心：新版会话行中间挂着 _iconButton（操作按钮），
                # 点它会命中"排除 iconButton"的分支，抽屉就不会自动收起（实测踩过）。
                title = rows.nth(i).locator('[class*="_title"]')
                (title.first if title.count() else rows.nth(i)).click(timeout=4000)
                opened = True
                break
        page.wait_for_timeout(4000)

        g = "手机 · 选会话后"
        s2 = page.evaluate(STATE_JS)
        rep.check(g, "抽屉已自动收起", s2["collapsed"] is True, f"collapsed={s2['collapsed']}")
        rep.check(g, "遮罩已移除", not s2["backdrop"], f"backdrop={s2['backdrop']}")
        rep.check(g, "汉堡按钮回来了", s2["burgerShown"], "")
        rep.check(g, "找到可打开的会话", opened, "（没有历史会话时跳过）")
        if s2["messageCol"]:
            want = s2["vw"] - 40
            rep.check(g, "正文列宽 ≥ 视口-40", s2["messageCol"]["w"] >= want,
                      f"col={s2['messageCol']['w']} 期望≥{want}")
        # iOS 对 <16px 的可聚焦输入框会自动放大页面。实测原生 14px，适配层补到 16px。
        rep.check(g, "输入框字号 ≥16px（防 iOS 自动缩放）", (s2["inputFontSize"] or 0) >= 16,
                  f"font-size={s2['inputFontSize']}")

        probe = page.evaluate(PROBE_JS)
        for kind, label in [("overflow", "无横向溢出"), ("verticalWrap", "无文字竖排"),
                            ("overlap", "无组件重叠")]:
            items = probe[kind]
            rep.check(g, label, len(items) == 0, f"{len(items)} 处" + (f" 例: {items[0]}" if items else ""))
        # 截断分两类：输入区那些紧凑触发器本来就是缩写控件（已知可接受，走 WARN），
        # 其它位置的截断才算回归。
        known_trunc = [t for t in probe["truncated"] if re.search(r"_trigger(Label|Effort)?$", t["el"])]
        unexpected_trunc = [t for t in probe["truncated"] if t not in known_trunc]
        rep.check(g, "无意外截断", len(unexpected_trunc) == 0,
                  f"{len(unexpected_trunc)} 处" + (f" 例: {unexpected_trunc[0]}" if unexpected_trunc else ""))
        if known_trunc:
            rep.warn(g, "输入区紧凑触发器截断", f"{len(known_trunc)} 处 例: {known_trunc[0]}")
        page.screenshot(path=str(OUT / "phone-02-chat.png"))

        # 3) 汉堡 -> 抽屉
        g = "手机 · 抽屉"
        page.locator('[data-dshm-hamburger]').first.click(timeout=5000)
        page.wait_for_timeout(1500)
        s3 = page.evaluate(STATE_JS)
        rep.check(g, "侧栏变为 absolute 浮层", s3["sidebarPos"] == "absolute", f"pos={s3['sidebarPos']}")
        rep.check(g, "遮罩出现", s3["backdrop"], "")
        rep.check(g, "主区域仍占满视口", (s3["center"] or {}).get("w", 0) >= s3["vw"] - 2, f"center={s3['center']}")
        rep.check(g, "汉堡按钮隐藏", not s3["burgerShown"], "")
        page.screenshot(path=str(OUT / "phone-03-drawer.png"))

        # 3b) 轨迹 tab：payload 行原生就是「nowrap + 祖先 overflow:hidden 裁成省略号」，
        #     不算页面溢出（探针已含裁剪感知）。这里盯的是真正漏出去的元素。
        g = "手机 · 轨迹 tab"
        try:
            bd = page.locator('[data-dshm-backdrop]')
            if bd.count():
                # 遮罩 inset:0 铺满，但抽屉（z-15）压在它左半边 —— 点几何中心会落在
                # 抽屉上。点靠右侧（抽屉宽 min(84vw,320px)，x=400 一定在抽屉外）。
                bd.first.click(position={"x": 400, "y": 500}, timeout=3000)
                page.wait_for_timeout(800)
            page.locator('[class*="_tabs"] [class*="_tab"]', has_text="轨迹").first.click(timeout=4000)
            page.wait_for_timeout(2000)
            tprobe = page.evaluate(PROBE_JS)
            rep.check(g, "无横向溢出", len(tprobe["overflow"]) == 0,
                      f"{len(tprobe['overflow'])} 处" + (f" 例: {tprobe['overflow'][0]}" if tprobe["overflow"] else ""))
            rep.check(g, "无组件重叠", len(tprobe["overlap"]) == 0,
                      f"{len(tprobe['overlap'])} 处" + (f" 例: {tprobe['overlap'][0]}" if tprobe["overlap"] else ""))
            page.screenshot(path=str(OUT / "phone-05-trajectory.png"))
            page.locator('[class*="_tabs"] [class*="_tab"]', has_text="对话").first.click(timeout=4000)
            page.wait_for_timeout(1200)
        except Exception as exc:  # noqa: BLE001
            rep.check(g, "打开轨迹 tab", False, str(exc)[:80])

        # 3c) 组件态 · 展开一个工具行：展开区出现、输出可横滚、小按钮达标。
        #     工具行标签在 DSH 新版里本地化了（Pwsh -> 运行命令 / 读取 / 编辑），
        #     所以按「标签或旧英文名」找；展开区若仍是旧结构才断言，
        #     否则 WARN 跳过（新版 DOM 已变，见 docs/STATE.md 的版本差异一节）。
        g = "手机 · 工具行展开"
        try:
            row = page.locator(
                '[class*="_callRow"]',
                has_text=re.compile(r"^\s*(Pwsh|运行命令|读取|编辑|写入|Read|Edit|Write|Bash)"),
            )
            if row.count() == 0:
                rep.warn(g, "会话里没有可展开的工具行", "跳过展开检查")
            else:
                page.evaluate(
                    """() => {
                         const rows = [...document.querySelectorAll('[class*="_callRow"]')];
                         const hit = rows.find(r => {
                           const t = (r.textContent || '').trim();
                           const b = r.getBoundingClientRect();
                           return b.height > 4 && b.top > 0 && b.top < innerHeight &&
                             /^(Pwsh|运行命令|读取|编辑|写入|Read|Edit|Write|Bash)/.test(t);
                         });
                         if (hit) hit.click();
                       }"""
                )
                page.wait_for_timeout(1200)
                t = page.evaluate(
                    """() => {
                      const q = s => document.querySelector(s);
                      const b = el => { if (!el) return null; const r = el.getBoundingClientRect();
                        return { w: Math.round(r.width), h: Math.round(r.height) }; };
                      const wrap = q('[class*="_bodyWrap"]');
                      const block = wrap ? wrap.querySelector('[class*="_block"]') : null;
                      const output = wrap ? wrap.querySelector('[class*="_output"]') : null;
                      const copy = wrap ? wrap.querySelector('[class*="_copyButton"]') : null;
                      const inspect = q('[class*="_inspectButton"]');
                      // 新版：复制是 aria=复制 的 _action 按钮
                      const newCopy = [...document.querySelectorAll('[class*="_action"]')]
                        .filter(x => (x.getAttribute('aria-label') || '') === '复制')
                        .map(x => b(x))[0] || null;
                      return { legacy: !!wrap, hasBlock: !!block,
                               outputOx: output ? getComputedStyle(output).overflowX : null,
                               outputScrollable: output ? output.scrollWidth > output.clientWidth + 1 : false,
                               copy: b(copy), inspect: b(inspect), newCopy };
                    }"""
                )
                if not t["legacy"]:
                    rep.warn(g, "工具行展开区 DOM 已变（DSH 新版）",
                             "旧选择器 _bodyWrap/_block 不再命中，展开区适配待下一轮；"
                             f"新版复制钮={t['newCopy']}")
                else:
                    rep.check(g, "展开区出现", t["hasBlock"], "")
                    rep.check(g, "长输出可横向滚动",
                              (not t["outputScrollable"]) or t["outputOx"] in ("auto", "scroll"),
                              f"ox={t['outputOx']} scrollable={t['outputScrollable']}")
                    for name, key in [("复制钮", "copy"), ("查看钮", "inspect")]:
                        btn = t[key]
                        if btn:
                            rep.check(g, f"{name}点击目标 ≥32px", btn["h"] >= 32 and btn["w"] >= 32,
                                      f"{name}={btn['w']}x{btn['h']}")
                    cprobe = page.evaluate(PROBE_JS)
                    rep.check(g, "无横向溢出", len(cprobe["overflow"]) == 0,
                              f"{len(cprobe['overflow'])} 处" + (f" 例: {cprobe['overflow'][0]}" if cprobe["overflow"] else ""))
                page.screenshot(path=str(OUT / "phone-06-toolrow.png"))
        except Exception as exc:  # noqa: BLE001
            rep.check(g, "展开工具行", False, str(exc)[:80])

        # 3d) 右侧面板：窄屏下 App 自己会切成全屏浮层（position:fixed + z-40）。
        #     盯：铺满、汉堡让位（z-55 本来会压在面板顶栏上）、收起按钮可命中。
        g = "手机 · 右栏全屏"
        try:
            rtoggle = page.locator('button[aria-label="打开右侧边栏"]')
            if rtoggle.count() == 0:
                rep.warn(g, "没找到右栏开关", "跳过")
            else:
                rtoggle.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                r = page.evaluate(
                    """() => {
                      const q = s => document.querySelector(s);
                      const b = el => { if (!el) return null; const r = el.getBoundingClientRect();
                        return { w: Math.round(r.width), x: Math.round(r.x), y: Math.round(r.y) }; };
                      const panel = q('[class*="_rightbarCol"] [class*="_panel"]');
                      const burger = q('[data-dshm-hamburger]');
                      const close = [...document.querySelectorAll('button')]
                        .find(x => x.getAttribute('aria-label') === '收起右侧边栏');
                      let closeHit = null;
                      if (close) {
                        const r2 = close.getBoundingClientRect();
                        const el = document.elementFromPoint(r2.x + r2.width / 2, r2.y + r2.height / 2);
                        closeHit = el ? (el === close || close.contains(el) || (el.closest('button') === close)) : false;
                      }
                      return { vw: innerWidth, panel: b(panel),
                               panelVis: panel ? getComputedStyle(panel).visibility : null,
                               burgerShown: burger ? !burger.hasAttribute('hidden') : null,
                               closeHit };
                    }"""
                )
                rep.check(g, "面板全屏铺满", r["panel"] and r["panel"]["w"] >= r["vw"] - 2 and r["panel"]["x"] <= 2,
                          f"panel={r['panel']}")
                rep.check(g, "面板可见", r["panelVis"] == "visible", f"vis={r['panelVis']}")
                rep.check(g, "汉堡按钮已让位", r["burgerShown"] is False, f"shown={r['burgerShown']}")
                rep.check(g, "收起按钮可命中", r["closeHit"] is True, f"hit={r['closeHit']}")
                page.screenshot(path=str(OUT / "phone-07-rightbar.png"))
                page.locator('button[aria-label="收起右侧边栏"]').first.click(timeout=3000)
                page.wait_for_timeout(800)
        except Exception as exc:  # noqa: BLE001
            rep.check(g, "打开右栏", False, str(exc)[:80])

        # 3e) 手势：左缘右滑开抽屉、抽屉上左滑关抽屉（合成 TouchEvent 驱动）。
        g = "手机 · 手势"
        SWIPE_JS = """(steps) => {
          const first = steps[0];
          const el = document.elementFromPoint(first[0], first[1]) || document.body;
          for (const [x, y, type] of steps) {
            const t = new Touch({ identifier: 7, target: el, clientX: x, clientY: y });
            const ev = new TouchEvent(type, { cancelable: true, bubbles: true,
              touches: type === 'touchend' ? [] : [t], changedTouches: [t] });
            el.dispatchEvent(ev);
          }
          const frame = document.querySelector('[data-slot="root"] > [class*="_frame"]');
          return frame ? !frame.hasAttribute('data-sidebar-collapsed') : null;
        }"""
        try:
            page.evaluate(SWIPE_JS, [[8, 500, "touchstart"], [40, 500, "touchmove"],
                                     [90, 502, "touchmove"], [100, 502, "touchend"]])
            page.wait_for_timeout(800)
            opened = page.evaluate(
                """() => { const f = document.querySelector('[data-slot="root"] > [class*="_frame"]');
                   return f ? !f.hasAttribute('data-sidebar-collapsed') : null; }"""
            )
            rep.check(g, "左缘右滑开抽屉", opened is True, f"expanded={opened}")
            closed = page.evaluate(SWIPE_JS, [[200, 500, "touchstart"], [150, 500, "touchmove"],
                                              [110, 502, "touchmove"], [90, 502, "touchend"]])
            page.wait_for_timeout(800)
            closed = page.evaluate(
                """() => { const f = document.querySelector('[data-slot="root"] > [class*="_frame"]');
                   return f ? f.hasAttribute('data-sidebar-collapsed') : null; }"""
            )
            rep.check(g, "抽屉上左滑关抽屉", closed is True, f"collapsed={closed}")
        except Exception as exc:  # noqa: BLE001
            rep.warn(g, "合成触摸事件不可用", str(exc)[:80])

        # 4) 设置对话框。
        #    先重载页面拿一个干净状态 —— 上一步的抽屉状态和可能残留的浮层
        #    会污染设置页的测量与截图。
        g = "手机 · 设置对话框"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            reset_mobile_ui(page)  # 重载后 App 可能又恢复出全屏面板
            page.locator('[data-dshm-hamburger]').first.click(timeout=5000)
            page.wait_for_timeout(1800)
            how = open_settings(page)
            rep.check(g, "设置入口可点（elementFromPoint 命中自身）", how["hittable"],
                      f"openedBy={how['openedBy']} 最上层={how['topCls']}")
            page.wait_for_timeout(1200)
            dlg = page.evaluate(
                """() => {
                  const q = s => document.querySelector(s);
                  const ov = q('[class*="_overlay"]:not([class*="_overlayLayer"])');
                  const panel = ov ? ov.querySelector(':scope > [class*="_panel"]') : null;
                  const nav = panel ? panel.querySelector(':scope > [class*="_nav"]') : null;
                  const content = panel ? panel.querySelector(':scope > [class*="_content"]') : null;
                  const b = el => { if (!el) return null; const r = el.getBoundingClientRect();
                    return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x) }; };
                  const cs = el => el ? getComputedStyle(el) : null;
                  return { vw: innerWidth, panel: b(panel), panelDir: cs(panel) ? cs(panel).flexDirection : null,
                           nav: b(nav), navDir: cs(nav) ? cs(nav).flexDirection : null,
                           navListOk: !!q('[class*="_navList"]'),
                           navListOv: q('[class*="_navList"]') ? getComputedStyle(q('[class*="_navList"]')).overflowX : null,
                           content: b(content) };
                }"""
            )
            rep.check(g, "面板铺满宽度", dlg["panel"] and dlg["panel"]["w"] >= dlg["vw"] - 2, f"panel={dlg['panel']}")
            rep.check(g, "导航列表存在（契约）", dlg["navListOk"], "选择器没命中" if not dlg["navListOk"] else "")
            rep.check(g, "面板纵向排列", dlg["panelDir"] == "column", f"dir={dlg['panelDir']}")
            rep.check(g, "导航转横向", dlg["navDir"] == "row", f"dir={dlg['navDir']}")
            rep.check(g, "导航可横向滚动", dlg["navListOv"] == "auto", f"overflow-x={dlg['navListOv']}")
            rep.check(g, "内容区铺满", dlg["content"] and dlg["content"]["w"] >= dlg["vw"] - 2, f"content={dlg['content']}")
            dprobe = page.evaluate(PROBE_JS)
            rep.check(g, "无文字竖排", len(dprobe["verticalWrap"]) == 0,
                      f"{len(dprobe['verticalWrap'])} 处" + (f" 例: {dprobe['verticalWrap'][0]}" if dprobe["verticalWrap"] else ""))
            page.screenshot(path=str(OUT / "phone-04-settings.png"))

            # 4b) 设置子页 · 模型：表单行/卡片在 420px 下不溢出
            g2 = "手机 · 设置-模型子页"
            try:
                nav = page.locator('[class*="_navList"] button, [class*="_navList"] a', has_text="模型")
                if nav.count() == 0:
                    rep.warn(g2, "没找到模型子页导航", "跳过")
                else:
                    nav.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    mprobe = page.evaluate(PROBE_JS)
                    rep.check(g2, "无横向溢出", len(mprobe["overflow"]) == 0,
                              f"{len(mprobe['overflow'])} 处" + (f" 例: {mprobe['overflow'][0]}" if mprobe["overflow"] else ""))
                    rep.check(g2, "无文字竖排", len(mprobe["verticalWrap"]) == 0,
                              f"{len(mprobe['verticalWrap'])} 处" + (f" 例: {mprobe['verticalWrap'][0]}" if mprobe["verticalWrap"] else ""))
                    page.screenshot(path=str(OUT / "phone-08-settings-models.png"))
            except Exception as exc:  # noqa: BLE001
                rep.check(g2, "打开模型子页", False, str(exc)[:80])
        except Exception as exc:  # noqa: BLE001
            rep.check(g, "打开设置", False, str(exc)[:80])
        ctx.close()

        # ─────────── 桌面视口：适配层必须完全惰性 ───────────
        ctx2 = browser.new_context(**DESKTOP)
        page2 = ctx2.new_page()
        page2.goto(url, wait_until="domcontentloaded", timeout=60000)
        page2.wait_for_timeout(6000)
        g = "桌面 1440px · 回归"
        d = page2.evaluate(STATE_JS)
        rep.check(g, "侧栏正常占位（非 0）", not d["grid"].startswith("0px"), f"grid={d['grid']}")
        rep.check(g, "侧栏非浮层", d["sidebarPos"] == "static", f"pos={d['sidebarPos']}")
        rep.check(g, "无汉堡按钮", not d["burgerShown"], f"exists={d['burgerExists']} shown={d['burgerShown']}")
        try:
            open_settings(page2)
            page2.wait_for_timeout(1200)
            dd = page2.evaluate(
                """() => { const ov = document.querySelector('[class*="_overlay"]:not([class*="_overlayLayer"])');
                     const panel = ov ? ov.querySelector(':scope > [class*="_panel"]') : null;
                     const nav = panel ? panel.querySelector(':scope > [class*="_nav"]') : null;
                     const b = el => { if (!el) return null; const r = el.getBoundingClientRect();
                       return { w: Math.round(r.width), h: Math.round(r.height) }; };
                     return { panel: b(panel), panelDir: panel ? getComputedStyle(panel).flexDirection : null,
                              navDir: nav ? getComputedStyle(nav).flexDirection : null }; }"""
            )
            rep.check(g, "设置面板仍是桌面双栏", dd["panelDir"] == "row" and dd["navDir"] == "column",
                      f"panelDir={dd['panelDir']} navDir={dd['navDir']} panel={dd['panel']}")
        except Exception as exc:  # noqa: BLE001
            rep.check(g, "打开设置", False, str(exc)[:80])
        page2.screenshot(path=str(OUT / "desktop-01-settings.png"))
        ctx2.close()

        # ─────────── 可选：WebKit（真 Safari 引擎）冒烟核心组 ───────────
        # 用法：python tools/mobile-audit.py --webkit（先 playwright install webkit）
        if want_webkit:
            g = "WebKit · iPhone 冒烟"
            try:
                wk = p.webkit.launch(headless=not keep_open)
                wpage = wk.new_context(**IPHONE).new_page()
                wpage.goto(url, wait_until="domcontentloaded", timeout=60000)
                wpage.wait_for_timeout(6000)
                reset_mobile_ui(wpage)  # App 可能恢复出右栏全屏面板
                s = wpage.evaluate(STATE_JS)
                rep.check(g, "grid 第一轨为 0", s["grid"].startswith("0px"), f"grid={s['grid']}")
                rep.check(g, "主区域占满视口", (s["center"] or {}).get("w", 0) >= s["vw"] - 2, f"center={s['center']}")
                rep.check(g, "汉堡按钮可见", s["burgerShown"], "")
                rep.check(g, "输入框字号 ≥16px", (s["inputFontSize"] or 0) >= 16, f"font-size={s['inputFontSize']}")
                # 开一个会话看正文列与溢出
                wpage.locator('[data-dshm-hamburger]').first.click(timeout=5000)
                wpage.wait_for_timeout(1500)
                rows = wpage.locator('[data-slot="root"] [role="treeitem"]')
                for i in range(min(rows.count(), 40)):
                    try:
                        t = rows.nth(i).inner_text(timeout=1000)
                    except Exception:
                        continue
                    if ("分钟" in t or "小时" in t or "天" in t) and "新建会话" not in t:
                        title = rows.nth(i).locator('[class*="_title"]')
                        (title.first if title.count() else rows.nth(i)).click(timeout=4000)
                        break
                wpage.wait_for_timeout(4000)
                s2 = wpage.evaluate(STATE_JS)
                rep.check(g, "选会话后抽屉自动收起", s2["collapsed"] is True, f"collapsed={s2['collapsed']}")
                if s2["messageCol"]:
                    rep.check(g, "正文列宽 ≥ 视口-40", s2["messageCol"]["w"] >= s2["vw"] - 40,
                              f"col={s2['messageCol']['w']}")
                wprobe = wpage.evaluate(PROBE_JS)
                rep.check(g, "无横向溢出", len(wprobe["overflow"]) == 0,
                          f"{len(wprobe['overflow'])} 处" + (f" 例: {wprobe['overflow'][0]}" if wprobe["overflow"] else ""))
                wpage.screenshot(path=str(OUT / "webkit-01-chat.png"))
                wk.close()
            except Exception as exc:  # noqa: BLE001
                rep.warn(g, "WebKit 不可用（未安装或启动失败）", str(exc)[:100])

        if keep_open:
            input("按回车结束…")
        browser.close()

    rep.dump()
    print(f"截图已存到 {OUT}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
