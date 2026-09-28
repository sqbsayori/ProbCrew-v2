#!/usr/bin/env python3
"""`scripts/manual_check.py` —— **人工项的一次实跑**（ADR-0006 判给人工的那一半）。

用法（**本机手跑，不进 CI**）::

    # ① 起服务（同源托管，`docs/03 §8.4` 第 2 步）
    cd backend && python -m app.main
    # ② 起一个 WebDriver（macOS 用系统自带的 Safari，无需下载任何浏览器）
    safaridriver -p 4466
    # ③ 跑（默认连 127.0.0.1:8000 与 127.0.0.1:4466，可覆盖）
    python3 scripts/manual_check.py --app http://127.0.0.1:8000/ --wd http://127.0.0.1:4466

它测什么（正是 `scripts/verify.sh` 的**覆盖台账**里逐条打印为"留人工"的那些）：

| 项 | 判据 | 为什么门禁做不到 |
|---|---|---|
| 首次打开 `/` 的落地页 | `docs/02 §7.1` 的落地页约定（空哈希 ⇒ `#/home`） | 要真跑一遍才知道落到哪 |
| R-F① 点击目标 ≥ 32px | N8①（`--target-min`） | **渲染尺寸**，静态只能看"令牌有没有被用" |
| R-F② 键盘可达 + 焦点环 + 语义 | N8② · `docs/02 §1.6` | 要真的 `focus()` 与 `:focus-visible` |
| R-G **行为面** | N11（跳转真的换视图 · 返回键 `popstate`） | 要真的触发 `hashchange` / `back` |
| R-I `must_render` **真的上屏** | F3 / N15（契约里 `must_render` 的字段） | 要读**渲染后的 DOM**；本脚本用**打桩的 `/api/me/notice`** 喂前端（与 R-L 的"假 provider"同一手法） |
| N8③ **渲染结果**的对比度 | `docs/01` N8③ | 门禁只对"**声明的色对**"负责 |

★ **与 ADR-0006 的关系**：ADR-0006 拒的是**把浏览器与 npm 带进门禁/CI**（零安装不可破）。
本脚本**零新依赖**（Python 标准库 + 系统自带的 Safari/safaridriver），且**只能手动跑**
—— 它不是第二个门禁入口（`docs/03 §8.2` 写明"门禁入口只有 `scripts/verify.sh`"）。

★ **两个平台事实（实测，别当成应用缺陷）**：
① macOS Safari 的 **Tab 焦点遍历**要系统设置里打开"键盘导航"（默认关）——按键确实送进页面
   （`keydown` 收得到 `Tab`），但浏览器不移动焦点。所以本脚本用**逐个 `focus()`** 测"应用侧可聚焦"，
   而"真 Tab 走查"要在那项系统设置打开后人工复核。
② Safari 的 WebDriver **一次只能配一个会话**：上一次没删干净会报
   `The Safari instance is already paired with another WebDriver session.` —— 按提示先把旧会话
   `DELETE` 掉（提示里会给出命令）。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

WD = "http://127.0.0.1:4466"
APP = "http://127.0.0.1:8000/"


def call(method, path, payload=None, timeout=40):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(WD + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # WebDriver 把 JS 异常放在 body 里
        body = exc.read().decode(errors="replace")
        hint = ""
        if "already paired" in body:
            hint = ("\n  ★ Safari 一次只能配一个会话：把上一次的会话 DELETE 掉再跑，例如\n"
                    "    curl -X DELETE http://127.0.0.1:4466/session/<旧的 sessionId>")
        raise RuntimeError(f"WebDriver {exc.code}: {body[:400]}{hint}") from None


def js(sid, script, args=None):
    return call("POST", f"/session/{sid}/execute/sync", {"script": script, "args": args or []})["value"]


def js_async(sid, script, args=None):
    return call("POST", f"/session/{sid}/execute/async", {"script": script, "args": args or []})["value"]


REPORT = []


def record(item, ok, detail):
    mark = "PASS" if ok is True else ("n/a " if ok is None else "FAIL")
    REPORT.append((item, ok, detail))
    print(f"[{mark}] {item}")
    if detail:
        print(f"        {detail}")


def main():
    sid = call("POST", "/session", {"capabilities": {"alwaysMatch": {"browserName": "safari"}}})["value"]["sessionId"]
    print(f"Safari 会话：{sid}")
    try:
        call("POST", f"/session/{sid}/window/rect", {"width": 1280, "height": 800, "x": 0, "y": 0})
        call("POST", f"/session/{sid}/url", {"url": APP})
        time.sleep(1.2)

        # ── 0 · 首次打开 `/`（空哈希）⇒ 落到首页；顺带看首屏是否真的起来 ──
        js(sid, "localStorage.clear(); return 1;")
        call("POST", f"/session/{sid}/url", {"url": APP})
        time.sleep(1.2)
        boot = js(sid, """
          const m = document.querySelector('#app-main');
          return {hash: location.hash, mount: m ? m.children.length : -1,
                  nav: document.querySelectorAll('.app-nav__link').length,
                  session: (document.querySelector('#app-session')||{}).textContent,
                  h1: [...document.querySelectorAll('#app-main h1')].map(e=>e.textContent),
                  current: (document.querySelector('.app-nav__link[aria-current="page"]')||{}).textContent};
        """)
        record("首次打开 `/`（空哈希）落到首页 `#/home`",
               boot["hash"] == "#/home" and any("首页" in t for t in boot["h1"]),
               f"hash={boot['hash']!r} → h1 {boot['h1']} · 导航高亮 '{boot['current']}'"
               + (" ★ 修复前这里是 '未知页面'（本次实测发现）" if boot["hash"] != "#/home" else ""))
        record("首屏启动（模块图真的跑起来）", boot["mount"] > 0,
               f"#app-main 子节点 {boot['mount']} · 导航 {boot['nav']} 条 · 顶栏 '{boot['session']}'")

        # ── R-F① 真实点击尺寸 ≥ 32px（N8① 的渲染面） ──────────────────────
        sizes = js(sid, """
          const els=[...document.querySelectorAll('a[href],button,[role="button"],input[type="submit"],summary')];
          const rows=els.map(el=>{const r=el.getBoundingClientRect();const cs=getComputedStyle(el);
            return {name:(el.textContent||el.getAttribute('aria-label')||el.getAttribute('href')||el.tagName).trim().slice(0,24),
                    w:Math.round(r.width),h:Math.round(r.height),cls:(el.className||'').toString().slice(0,26),
                    inProse: !!el.closest('p, li, span'),
                    visible:r.width>0&&r.height>0&&cs.visibility!=='hidden'}}).filter(x=>x.visible);
          return {total:rows.length, violations:rows.filter(x=>Math.min(x.w,x.h)<32)};
        """)
        viol = sizes["violations"]
        non_prose = [v for v in viol if not v["inProse"]]
        record("R-F① 点击目标 ≥ 32px（真实渲染尺寸）", len(non_prose) == 0,
               f"可点击元素 {sizes['total']} 个 · 任一不足 32px 的 {len(viol)} 个"
               + (f" · 其中**非正文内联**的 {len(non_prose)} 个：{json.dumps(non_prose, ensure_ascii=False)}"
                  if non_prose else " · （其余为正文内联链接，WCAG 2.5.8 有内联例外）"))

        # ── R-F② 键盘可达（**应用侧**：每个交互元素真的能被聚焦 + 焦点环） ──
        #   ★ 为什么不用真 Tab：本机 Safari 的 Tab 焦点遍历要**系统设置**里打开
        #     "键盘导航"（默认关），而按键确实送进了页面（keydown 收得到）⇒ 平台限制，不是应用缺陷。
        focus = js(sid, """
          const els=[...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[tabindex]:not([tabindex="-1"])')]
            .filter(el=>{const r=el.getBoundingClientRect(); return r.width>0&&r.height>0&&!el.disabled;});
          const bad=[], noRing=[];
          for (const el of els) {
            el.focus();
            const name=(el.textContent||el.getAttribute('aria-label')||el.className||el.tagName).trim().slice(0,18);
            if (document.activeElement !== el) bad.push(name);
            const cs=getComputedStyle(el);
            if (cs.outlineStyle==='none' && cs.boxShadow==='none') noRing.push(name);
          }
          return {total: els.length, unfocusable: bad, noRing, fv: els.filter(e=>e.matches(':focus-visible')).length};
        """)
        record("R-F② 键盘可达（每个交互元素真的能被聚焦）", not focus["unfocusable"],
               f"可交互元素 {focus['total']} 个 · 无法聚焦 {len(focus['unfocusable'])} 个"
               + (f"：{focus['unfocusable']}" if focus["unfocusable"] else "（全部命中 `document.activeElement`）"))
        record("R-F② 焦点环可见（`:focus-visible` / outline）", not focus["noRing"],
               f"无可见焦点的 {len(focus['noRing'])} 个" + (f"：{focus['noRing'][:6]}" if focus["noRing"] else "")
               + f" · 匹配 `:focus-visible` 的 {focus['fv']}/{focus['total']}"
               + " · ★ 说明：程序化 `focus()` 在 Safari 里未必算“键盘交互”，故**真 Tab 走查**须在系统设置打开键盘导航后手动复核"
               if focus["noRing"] else f"（全部有 outline；匹配 `:focus-visible` 的 {focus['fv']}/{focus['total']}）")

        # ── R-F② 语义标签（在**真实页面**上测，不在兜底页上） ─────────────
        sem = js(sid, """
          const links=[...document.querySelectorAll('a[href],button')];
          return {navs:document.querySelectorAll('nav[aria-label]').length,
                  mains:document.querySelectorAll('main').length,
                  h1:document.querySelectorAll('#app-main h1').length,
                  unnamed:links.filter(e=>!(e.textContent||'').trim()&&!e.getAttribute('aria-label')).length,
                  current:(document.querySelector('.app-nav__link[aria-current="page"]')||{}).textContent,
                  lang:document.documentElement.lang};
        """)
        record("R-F② 语义标签（landmark / h1 / 可访问名 / aria-current / lang）",
               sem["navs"] >= 1 and sem["mains"] == 1 and sem["h1"] == 1 and sem["unnamed"] == 0
               and bool(sem["current"]) and bool(sem["lang"]),
               f"nav[aria-label] {sem['navs']} · main {sem['mains']} · h1 {sem['h1']} · 无可访问名 {sem['unnamed']} · "
               f"aria-current='{sem['current']}' · lang='{sem['lang']}'")


        # ── R-G 行为面①：hash 跳转真的换视图 ─────────────────────────────
        js(sid, "location.hash = '#/login'; return location.hash;")
        time.sleep(0.6)
        view = js(sid, """
          return {hash:location.hash, h1:[...document.querySelectorAll('#app-main h1')].map(e=>e.textContent),
                  inputs:document.querySelectorAll('#app-main input').length,
                  current:(document.querySelector('.app-nav__link[aria-current="page"]')||{}).textContent};
        """)
        record("R-G 跳转**真的**换了视图（不是只变 URL）",
               view["inputs"] >= 1 and "登录" in "".join(view["h1"]) and view["current"] == "登录",
               f"hash {view['hash']} → h1 {view['h1']} · 表单控件 {view['inputs']} 个 · 导航高亮 '{view['current']}'")

        # ── R-G 行为面②：返回键（旧仓的坑：只变 URL 不变视图） ──────────
        call("POST", f"/session/{sid}/back")
        time.sleep(0.8)
        back = js(sid, """
          return {hash:location.hash, h1:[...document.querySelectorAll('#app-main h1')].map(e=>e.textContent),
                  current:(document.querySelector('.app-nav__link[aria-current="page"]')||{}).textContent};
        """)
        record("R-G 返回键（`popstate`：URL 与视图一起回退）",
               back["hash"] != "#/login" and "登录" not in "".join(back["h1"]),
               f"back → hash {back['hash']} · h1 {back['h1']} · 导航高亮 '{back['current']}'")

        js(sid, "location.hash = '#/nope-not-a-page'; return 1;")
        time.sleep(0.6)
        fallback = js(sid, "return [...document.querySelectorAll('#app-main h1')].map(e=>e.textContent);")
        record("R-G 未知路由走 fallback", any("未知页面" in t for t in fallback), f"h1 {fallback}")

        # ── R-I 是否**真的上屏**（N15 的 `noticeStatus.message`，后端打桩） ──
        try:
            ri = js_async(sid, """
              const done = arguments[arguments.length - 1];
              (async () => {
                const real = window.fetch, seen = [];
                window.fetch = (url, opts = {}) => {
                  const s = String(url), method = (opts.method || 'GET').toUpperCase();
                  if (s.includes('/api/me/notice')) {
                    seen.push({url: s, method, auth: (opts.headers || {})['Authorization'] || null, body: opts.body || null});
                    const payload = method === 'POST' ? {ack: true}
                                  : {ask: true, message: '【人工验收】数据将发往你配置的端点'};
                    return Promise.resolve(new Response(JSON.stringify(payload),
                      {status: 200, headers: {'Content-Type': 'application/json'}}));
                  }
                  return real(url, opts);
                };
                const { setSession } = await import('/core/auth.js');
                setSession({token: 'selftest-token'});
                await new Promise(r => setTimeout(r, 900));
                const banner = document.querySelector('[data-block="notice-egress"]');
                const text = banner ? banner.textContent : null;
                const button = banner ? banner.querySelector('button') : null;
                if (button) button.click();
                await new Promise(r => setTimeout(r, 600));
                done({rendered: !!banner, text, removed: !document.querySelector('[data-block="notice-egress"]'), seen});
              })().catch(e => done({error: String(e)}));
            """)
        except RuntimeError as exc:
            ri = {"error": str(exc)}
        if ri.get("error"):
            record("R-I `must_render` 真的上屏（N15 `noticeStatus.message`）", False, ri["error"])
        else:
            calls = ri["seen"]
            record("R-I `must_render` 真的上屏（N15 `noticeStatus.message`）",
                   ri["rendered"] and "人工验收" in (ri["text"] or ""),
                   f"banner 出现={ri['rendered']} · DOM 文案={json.dumps(ri['text'], ensure_ascii=False)[:100]}")
            record("R-I 确认动作打对了端点（`POST {ack:true}`）",
                   len(calls) >= 2 and calls[-1]["method"] == "POST" and calls[-1]["body"] == '{"ack":true}' and ri["removed"],
                   f"调用序列 {json.dumps(calls, ensure_ascii=False)[:240]}")
        record("R-I 另一半（F3 的 `verification.level` 标签）", None,
               "**对象未产出**：没有任何页面渲染它（`#/practice` 属 W2/W4）⇒ 与门禁台账同一结论")

        # ── N8③ 渲染结果的对比度（门禁只对"声明的色对"负责） ────────────────
        contrast = js(sid, """
          function lum(c){const m=c.match(/[\\d.]+/g).map(Number);const [r,g,b]=m.slice(0,3).map(v=>v/255)
            .map(v=>v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4));return 0.2126*r+0.7152*g+0.0722*b;}
          function bgOf(el){let n=el;while(n&&n!==document.documentElement){const c=getComputedStyle(n).backgroundColor;
            if(c&&c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c;n=n.parentElement;}return 'rgb(255, 255, 255)';}
          const out=[];
          for(const s of ['#app-main h1','.page__subtitle','.app-nav__link','#app-session','.app-nav__link[aria-current="page"]']){
            const el=document.querySelector(s); if(!el) continue;
            const cs=getComputedStyle(el), fg=cs.color, bg=bgOf(el);
            const hi=Math.max(lum(fg),lum(bg)), lo=Math.min(lum(fg),lum(bg));
            out.push({sel:s, fg, bg, ratio:+(((hi+0.05)/(lo+0.05)).toFixed(2)), size:cs.fontSize});
          }
          return out;
        """)
        worst = min((row["ratio"] for row in contrast), default=None)
        record("N8③ 渲染结果的对比度 ≥ 4.5:1（人工项；此前只测过「声明的色对」）", worst is None or worst >= 4.5,
               json.dumps(contrast, ensure_ascii=False))
    finally:
        try:
            call("DELETE", f"/session/{sid}")
        except Exception as exc:  # noqa: BLE001
            print(f"（会话删除失败：{exc}）", file=sys.stderr)
    fails = [item for item, ok, _ in REPORT if ok is False]
    print(f"\n== 汇总：{sum(1 for _, ok, _ in REPORT if ok is True)} 通过 / {len(fails)} 失败 / "
          f"{sum(1 for _, ok, _ in REPORT if ok is None)} 无法测（对象未产出）==")
    return 1 if fails else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="人工项的实跑（ADR-0006 判给人工的那一半）")
    parser.add_argument("--app", default=APP, help=f"本地服务地址（缺省 {APP}）")
    parser.add_argument("--wd", default=WD, help=f"WebDriver 地址（缺省 {WD}）")
    args = parser.parse_args()
    APP, WD = args.app, args.wd
    sys.exit(main())
