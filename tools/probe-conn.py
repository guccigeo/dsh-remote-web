"""长连接稳定性探针：对比「本机代理直连」与「公网经隧道」的 WebSocket 重连情况。

用法: python tools/probe-conn.py --url local|public --seconds 90
"""
import argparse
import json
import pathlib
import statistics
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "remote.config.json").read_text(encoding="utf-8"))

INDICATOR_JS = r"""
() => {
  // 连接指示器在侧栏底部（ConnectionIndicator）。按可见文本兜住所有状态文案。
  const texts = ['连接中断', '重新连接', '连接异常', '连接成功', '正在重试', '重试中'];
  const hits = [];
  for (const el of document.querySelectorAll('button, span, div')) {
    const t = (el.textContent || '').trim();
    if (t.length > 24 || t.length === 0) continue;
    if (texts.some(k => t.includes(k))) {
      const r = el.getBoundingClientRect();
      if (r.width > 2 && r.height > 2) hits.push({text: t, rect: [Math.round(r.x), Math.round(r.y)]});
    }
  }
  return hits.slice(0, 4);
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="local", choices=["local", "public"])
    ap.add_argument("--seconds", type=int, default=90)
    args = ap.parse_args()

    if args.url == "public":
        base = CFG["publicBaseUrl"].rstrip("/")
    else:
        base = "http://127.0.0.1:%d" % CFG.get("listenPort", 19390)
    url = base + "/?token=" + CFG["token"]

    events = []          # (t, kind, detail)
    frames = []          # (t, direction, size)
    ws_ids = {}
    t0 = time.time()

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 420, "height": 933}, is_mobile=True, has_touch=True, device_scale_factor=2,
                            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                                        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"))
        page = ctx.new_page()

        def on_ws(ws):
            ws_ids[id(ws)] = ws.url
            events.append((time.time() - t0, "ws-open", ws.url[-40:]))
            ws.on("framereceived", lambda payload: frames.append((time.time() - t0, "in", len(payload) if isinstance(payload, (bytes, str)) else 0)))
            ws.on("framesent", lambda payload: frames.append((time.time() - t0, "out", len(payload) if isinstance(payload, (bytes, str)) else 0)))
            ws.on("close", lambda w: events.append((time.time() - t0, "ws-close", w.url[-40:])))
            ws.on("socketerror", lambda err: events.append((time.time() - t0, "ws-error", str(err)[:60])))

        page.on("websocket", on_ws)
        console = []
        page.on("console", lambda m: console.append((round(time.time() - t0, 1), m.type, m.text[:120])))
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        indicator_samples = []
        end = time.time() + args.seconds
        while time.time() < end:
            page.wait_for_timeout(500)
            try:
                hits = page.evaluate(INDICATOR_JS)
            except Exception:
                hits = []
            if hits:
                indicator_samples.append((round(time.time() - t0, 1), hits))
        page.screenshot(path=str(ROOT / "tools" / "out" / f"conn-{args.url}.png"))
        b.close()

    in_frames = [f for f in frames if f[1] == "in"]
    out_frames = [f for f in frames if f[1] == "out"]
    gaps = [round(in_frames[i + 1][0] - in_frames[i][0], 2) for i in range(len(in_frames) - 1)]
    print(f"\n===== {args.url} · {args.seconds}s =====")
    print("WS 连接数:", sum(1 for e in events if e[1] == "ws-open"))
    for e in events:
        print("  事件", e)
    print("收到帧:", len(in_frames), " 发出帧:", len(out_frames))
    if gaps:
        print("帧间隔 中位/最大:", statistics.median(gaps), max(gaps), " 前10大:", sorted(gaps, reverse=True)[:10])
    print("指示器出现次数:", len(indicator_samples), " 样本:", indicator_samples[:6])
    print("console 错误:", [c for c in console if c[1] in ("error", "warning")][:8])
    print("console 总数:", len(console))


if __name__ == "__main__":
    main()
