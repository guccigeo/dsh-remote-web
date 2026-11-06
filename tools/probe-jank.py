"""手机页面流畅度探针：长任务 / rAF 帧间隔 / DOM 规模 / 堆内存。

用法: python tools/probe-jank.py --url public --seconds 20
"""
import argparse
import json
import pathlib
import statistics

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "remote.config.json").read_text(encoding="utf-8"))

SETUP_JS = r"""
() => {
  window.__jank = {longtasks: [], raf: []};
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) window.__jank.longtasks.push(Math.round(e.duration));
    }).observe({entryTypes: ['longtask']});
  } catch (err) {}
  let last = performance.now();
  const tick = (t) => {
    window.__jank.raf.push(Math.round(t - last));
    last = t;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return true;
}
"""

READ_JS = r"""
() => {
  const j = window.__jank || {longtasks: [], raf: []};
  const raf = j.raf.slice(1);
  const sorted = [...raf].sort((a, b) => a - b);
  const p = (q) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))] : 0;
  return {
    longtasks: j.longtasks,
    longtaskTotal: j.longtasks.reduce((a, b) => a + b, 0),
    frames: raf.length,
    rafP50: p(0.5), rafP90: p(0.9), rafP99: p(0.99), rafMax: sorted.length ? sorted[sorted.length - 1] : 0,
    dropped: raf.filter(x => x > 40).length,
    nodes: document.querySelectorAll('*').length,
    heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
    rows: document.querySelectorAll('[class*="_callRow"]').length,
    msgs: document.querySelectorAll('[class*="_message"]').length,
  };
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="public", choices=["local", "public"])
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--no-enhance", action="store_true", help="屏蔽 mobile.css/mobile.js，量原生 DSH 的基线卡顿")
    args = ap.parse_args()
    base = CFG["publicBaseUrl"].rstrip("/") if args.url == "public" else "http://127.0.0.1:%d" % CFG.get("listenPort", 19390)
    url = base + "/?token=" + CFG["token"]

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 420, "height": 933}, is_mobile=True, has_touch=True, device_scale_factor=2,
                            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                                        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"))
        page = ctx.new_page()
        if args.no_enhance:
            page.route("**/__dsh-mobile/**", lambda route: route.abort())
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        # 打开最近一个会话（行数多才有代表性）
        page.evaluate("""() => { const o = document.querySelector('[data-sidebar-opener]'); if (o) o.click(); }""")
        page.wait_for_timeout(1500)
        # 等会话行渲染出来再点（原生模式下没有适配层的布局规则，时序不同）
        page.wait_for_function("() => document.querySelectorAll('[class*=\"_sessionRow\"]').length > 0", timeout=15000)
        clicked = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('[class*="_sessionRow"]')].filter(r => r.getBoundingClientRect().height > 4);
            const t = rows.find(r => r.getAttribute('aria-selected') !== 'true') || rows[0];
            if (!t) return false;
            (t.querySelector('[class*="_title"]') || t).click();
            return true;
        }""")
        print("clicked row:", clicked)
        # 等对话内容真的渲染出来，否则量的是空页面
        page.wait_for_function("() => document.querySelectorAll('[class*=\"_callRow\"], [class*=\"_message\"]').length > 0",
                               timeout=20000)
        page.wait_for_timeout(4000)
        page.evaluate(SETUP_JS)
        page.wait_for_timeout(args.seconds * 1000)
        res = page.evaluate(READ_JS)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        page.screenshot(path=str(ROOT / "tools" / "out" / "jank.png"))
        b.close()


if __name__ == "__main__":
    main()
