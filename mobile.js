/* ===========================================================================
   DSH 移动端适配层（交互部分）—— 由 dsh-remote-web 代理注入。

   只做一件 CSS 做不到的事：侧栏以浮层打开时，补一层背景遮罩，点遮罩收起侧栏。
   收起动作不去碰 React 内部状态，而是**点 App 自己的切换按钮**——
   那个按钮的 local 类名是稳定的 `_toggle`（折叠时 aria-label="打开侧边栏"，
   展开时 ="收起侧边栏"），所以不会随 DSH 升级失效。
   =========================================================================== */
(function () {
  'use strict'

  var MOBILE_MAX = 720
  var BACKDROP_ATTR = 'data-dshm-backdrop'
  var HAMBURGER_ATTR = 'data-dshm-hamburger'
  var MARK = 'data-dshm'

  function frameEl() {
    return document.querySelector('[data-slot="root"] > [class*="_frame"]')
  }
  function sidebarEl() {
    return document.querySelector('[data-slot="root"] > [class*="_frame"] > [class*="_sidebarCol"]')
  }

  /* 侧栏折叠/展开的切换按钮。优先用稳定的 local 类名 _toggle，
     退路是 aria-label 里带「侧边栏 / sidebar」的那个按钮。 */
  function findToggle() {
    var side = sidebarEl()
    if (!side) return null
    var byClass = side.querySelector('button[class*="_toggle"]')
    if (byClass) return byClass
    var buttons = side.querySelectorAll('button')
    for (var i = 0; i < buttons.length; i++) {
      var label = buttons[i].getAttribute('aria-label') || ''
      if (/侧边栏|sidebar/i.test(label)) return buttons[i]
    }
    return null
  }

  function removeBackdrop() {
    var existing = document.querySelectorAll('[' + BACKDROP_ATTR + ']')
    for (var i = 0; i < existing.length; i++) existing[i].remove()
  }

  /* ---------------------------------------------------------------------
     左上角汉堡按钮：手机上图标轨道被 CSS 收成 0 宽，需要一个新的入口来
     唤出会话抽屉。
     为什么能点到被隐藏的切换按钮：CSS 只是把 grid 第一轨设为 0，侧栏里的
     按钮仍在 DOM 里；HTMLElement.click() 对不可见元素同样派发事件，所以
     这里直接调 App 自己的 _toggle，不碰 React 内部状态。
     --------------------------------------------------------------------- */
  function ensureHamburger() {
    var existing = document.querySelector('[' + HAMBURGER_ATTR + ']')
    if (existing) return existing
    var button = document.createElement('button')
    button.setAttribute(HAMBURGER_ATTR, '')
    button.setAttribute('type', 'button')
    button.setAttribute('aria-label', '打开会话列表')
    button.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>'
    button.addEventListener(
      'click',
      function (event) {
        event.preventDefault()
        event.stopPropagation()
        var toggle = findToggle()
        if (toggle) toggle.click()
      },
      true
    )
    document.body.appendChild(button)
    return button
  }

  /**
   * 是否有「真的打开着」的模态框（设置等）。
   *
   * 为什么不能在 CSS 里用 `html:has([class*="_overlay"])` 判断：
   * 模态框的 overlay 元素**一直存在于 DOM 里**、关闭时只是隐藏，
   * 那个选择器因此恒为真，会把汉堡按钮永久藏掉（踩过，审计立刻抓到了）。
   * 这里按「实际尺寸 + visibility」判断才算得准。
   */
  function hasOpenModal() {
    var overlays = document.querySelectorAll('[class*="_overlay"]:not([class*="_overlayLayer"])')
    for (var i = 0; i < overlays.length; i++) {
      var box = overlays[i].getBoundingClientRect()
      if (box.width > 100 && box.height > 100 &&
          window.getComputedStyle(overlays[i]).visibility !== 'hidden') {
        return true
      }
    }
    return false
  }

  /** 只在「窄屏 + 抽屉已关 + 没有模态框」时显示汉堡按钮 */
  function syncHamburger(narrow, collapsed) {
    var button = ensureHamburger()
    if (narrow && collapsed && !hasOpenModal()) button.removeAttribute('hidden')
    else button.setAttribute('hidden', '')
  }

  /**
   * 右栏在窄屏下是全屏浮层。注意：frame 上的 data-rightbar-collapsed 属性
   * 只描述「停靠列」，全屏面板打开时该属性仍在（实测）——所以这里直接量
   * 面板本身：可见 + 宽度盖过大部分视口才算「右栏全屏开着」。
   */
  function rightbarFullscreenOpen() {
    var panel = document.querySelector('[class*="_rightbarCol"] [class*="_panel"]')
    if (!panel) return false
    if (window.getComputedStyle(panel).visibility === 'hidden') return false
    return panel.getBoundingClientRect().width > window.innerWidth * 0.6
  }

  function sync() {
    var frame = frameEl()
    var side = sidebarEl()
    if (!frame || !side || !side.parentElement) {
      removeBackdrop()
      var orphan = document.querySelector('[' + HAMBURGER_ATTR + ']')
      if (orphan) orphan.setAttribute('hidden', '')
      return
    }
    var expanded = !frame.hasAttribute('data-sidebar-collapsed')
    var rightOpen = rightbarFullscreenOpen()
    var narrow = window.innerWidth <= MOBILE_MAX

    // 窄屏 + 抽屉已关 -> 显示汉堡按钮（图标轨道被 CSS 收成 0 宽了）。
    // 右栏全屏打开时汉堡（z-55）会压在面板（z-40）顶栏上，一并藏起来。
    syncHamburger(narrow && !rightOpen, !expanded)

    if (expanded && narrow) {
      document.documentElement.setAttribute(MARK, 'drawer')
      if (!side.parentElement.querySelector('[' + BACKDROP_ATTR + ']')) {
        var backdrop = document.createElement('div')
        backdrop.setAttribute(BACKDROP_ATTR, '')
        backdrop.addEventListener(
          'click',
          function (event) {
            event.preventDefault()
            event.stopPropagation()
            var toggle = findToggle()
            if (toggle) toggle.click()
          },
          true
        )
        side.parentElement.insertBefore(backdrop, side)
      }
    } else {
      document.documentElement.removeAttribute(MARK)
      removeBackdrop()
    }
  }

  /* ---------------------------------------------------------------------
     选中会话后自动收起抽屉。
     手机上抽屉盖住聊天区，选完会话不收起来就没法看内容、没法打字。
     注意区分两类行（local 名稳定）：
       _sessionRow + aria-selected  = 会话 -> 该收起
       _projectRow + aria-expanded  = 工作区文件夹（展开/折叠树）-> 不能收
     行内的操作按钮（"…" 菜单 / 删除）也排除，否则点菜单会顺手把抽屉关了。
     --------------------------------------------------------------------- */
  document.addEventListener(
    'click',
    function (event) {
      var target = event.target
      if (!target || typeof target.closest !== 'function') return
      if (target.closest('[class*="_iconButton"]')) return
      var row = target.closest('[class*="_sessionRow"]')
      if (!row) return
      var side = sidebarEl()
      if (!side || !side.contains(row)) return
      if (window.innerWidth > MOBILE_MAX) return
      var frame = frameEl()
      if (!frame || frame.hasAttribute('data-sidebar-collapsed')) return
      // 等 App 先完成选中（React 的处理挂在 root 容器上），再收起抽屉
      window.setTimeout(function () {
        var f = frameEl()
        if (!f || f.hasAttribute('data-sidebar-collapsed')) return
        var toggle = findToggle()
        if (toggle) toggle.click()
      }, 120)
    },
    true
  )

  /* ---------------------------------------------------------------------
     手势：左缘右滑开抽屉、抽屉上左滑关抽屉。
     - 只认窄屏；开手势起点必须在左缘 24px 内（避免抢表格/代码块的横滚）。
     - 判定成立后对 touchmove preventDefault（非 passive），压制安卓/iOS 的
       边缘滑动返回手势；iOS Safari 的边缘返回是系统级、压不住，所以汉堡
       按钮仍是主入口，手势只是加分项。
     - 开/关都走 App 自己的 _toggle 按钮，不碰 React 状态。
     --------------------------------------------------------------------- */
  var SWIPE_EDGE = 24
  var SWIPE_MIN = 64
  var gesture = null // {x, y, mode: 'open'|'close', decided}

  function onTouchStart(event) {
    if (window.innerWidth > MOBILE_MAX) { gesture = null; return }
    if (!event.touches || event.touches.length !== 1) { gesture = null; return }
    var frame = frameEl()
    if (!frame) { gesture = null; return }
    var expanded = !frame.hasAttribute('data-sidebar-collapsed')
    var t = event.touches[0]
    if (!expanded) {
      if (t.clientX > SWIPE_EDGE) { gesture = null; return }
      if (hasOpenModal() || rightbarFullscreenOpen()) { gesture = null; return }
      gesture = { x: t.clientX, y: t.clientY, mode: 'open', decided: false }
    } else {
      gesture = { x: t.clientX, y: t.clientY, mode: 'close', decided: false }
    }
  }

  function onTouchMove(event) {
    if (!gesture || gesture.decided) return
    if (!event.touches || event.touches.length !== 1) { gesture = null; return }
    var t = event.touches[0]
    var dx = t.clientX - gesture.x
    var dy = t.clientY - gesture.y
    if (Math.abs(dx) < 12 && Math.abs(dy) < 12) return // 还没走出抖动区
    if (Math.abs(dx) <= Math.abs(dy) * 2) { gesture = null; return } // 偏垂直，是滚动
    if (gesture.mode === 'open' && dx < 0) { gesture = null; return }
    if (gesture.mode === 'close' && dx > 0) { gesture = null; return }
    gesture.decided = true
    // 成立：阻止浏览器把这个横滑解释成「返回」
    if (event.cancelable) event.preventDefault()
  }

  function onTouchEnd(event) {
    if (!gesture) return
    var decided = gesture.decided
    var mode = gesture.mode
    var startX = gesture.x
    var startY = gesture.y
    gesture = null
    if (!decided) return
    var t = (event.changedTouches && event.changedTouches[0]) || null
    if (!t) return
    var dx = t.clientX - startX
    if (Math.abs(dx) < SWIPE_MIN) return
    if (Math.abs(t.clientY - startY) > Math.abs(dx) / 2) return
    var toggle = findToggle()
    if (!toggle) return
    var frame = frameEl()
    if (!frame) return
    var expanded = !frame.hasAttribute('data-sidebar-collapsed')
    if (mode === 'open' && !expanded && dx > 0) toggle.click()
    if (mode === 'close' && expanded && dx < 0) toggle.click()
  }

  document.addEventListener('touchstart', onTouchStart, true)
  document.addEventListener('touchmove', onTouchMove, { capture: true, passive: false })
  document.addEventListener('touchend', onTouchEnd, true)

  /* React 会整体重挂载 DOM，所以不能只观察 frame 本身：
     挂在 body 上，属性 + 子节点一起看，用 rAF 去抖。 */
  var scheduled = false
  function schedule() {
    if (scheduled) return
    scheduled = true
    requestAnimationFrame(function () {
      scheduled = false
      sync()
    })
  }

  var observer = new MutationObserver(schedule)
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['data-sidebar-collapsed', 'data-rightbar-collapsed', 'data-slot', 'class']
  })
  window.addEventListener('resize', schedule)
  window.addEventListener('orientationchange', schedule)

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule)
  } else {
    schedule()
  }
})()
