#!/usr/bin/env python3
"""Headless browser checks for the Mini App front-end (v6.3 reliability work).

    tests/devctl.sh start owner 8080
    tests/devctl.sh start pending 8081          # optional: locked-view scenario
    python tests/test_miniapp_ui.py 8080 [8081]

Requires `pip install playwright && python -m playwright install chromium`.

Verifies:
  * index.html is served with the __ASSET_V__ placeholder substituted and the
    versioned assets are immutable-cacheable;
  * the app boots to the main view with no JS errors;
  * background polls patch the DOM in place (same nodes, no full re-render);
  * tab switching works and admin tab renders workers/users;
  * when the API is unreachable the skeleton shows a Retry button instead of
    a misleading auth screen, and Retry recovers once the server is back;
  * the locked (pending) scenario renders the locked view.
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
LOCKED_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 0
BASE = f"http://localhost:{PORT}"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("✅" if cond else "❌"), name, ("· " + str(info)) if info else "")
    if not cond:
        fails += 1


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "telegram" not in m.text.lower() else None)

    # ── asset versioning ────────────────────────────────────────────────
    resp = page.request.get(BASE + "/app")
    html = resp.text()
    m = re.search(r'app\.js\?v=([0-9a-f]{6,})', html)
    check("index.html has substituted asset version", bool(m) and "__ASSET_V__" not in html, m.group(1) if m else html[:80])
    check("index.html is no-store", "no-store" in (resp.headers.get("cache-control") or ""), resp.headers.get("cache-control"))
    if m:
        a = page.request.get(f"{BASE}/app/app.js?v={m.group(1)}")
        check("versioned asset immutable", "immutable" in (a.headers.get("cache-control") or ""), a.headers.get("cache-control"))
    a2 = page.request.get(f"{BASE}/app/app.js")
    check("bare asset short TTL", "max-age=300" in (a2.headers.get("cache-control") or ""), a2.headers.get("cache-control"))

    # ── boot ─────────────────────────────────────────────────────────────
    page.goto(BASE + "/app", wait_until="domcontentloaded")
    page.wait_for_selector("#view-main:not(.hidden)", timeout=20000)
    check("boots to main view", True)
    check("no JS errors during boot", not errors, errors[:2])
    check("name rendered", page.text_content("#me-name").strip() == "Dev User", page.text_content("#me-name"))
    check("skeleton removed from active-box", page.locator("#active-box .skel").count() == 0)
    check("active job rendered", page.locator("#active-box .job").count() == 1)

    # ── DOM morphing: poll must keep the same nodes ──────────────────────
    page.evaluate("window.__n1 = document.querySelector('#active-box .job'); window.__q1 = document.querySelector('#queue-list .job')")
    page.wait_for_timeout(3200)     # > POLL_LIVE (2.5 s) → at least one background poll
    same = page.evaluate("window.__n1 === document.querySelector('#active-box .job') && window.__q1 === document.querySelector('#queue-list .job')")
    check("poll patches DOM in place (same nodes)", same)

    # ── tabs ─────────────────────────────────────────────────────────────
    page.click(".nav[data-tab='settings']")
    page.wait_for_selector("#tab-settings:not(.hidden)")
    check("settings tab visible", page.locator("#lang-grid .choice").count() > 0, page.locator("#lang-grid .choice").count())
    page.click(".nav[data-tab='admin']")
    page.wait_for_selector("#tab-admin:not(.hidden)")
    page.wait_for_selector("#admin-users .user", timeout=10000)
    check("admin users rendered", page.locator("#admin-users .user").count() > 0, page.locator("#admin-users .user").count())
    check("worker nodes box rendered", page.locator("#worker-nodes").inner_text().strip() != "")
    page.click(".nav[data-tab='home']")
    page.wait_for_selector("#tab-home:not(.hidden)")

    # ── offline boot → Retry button ─────────────────────────────────────
    page2 = ctx.new_page()
    page2.route("**/api/**", lambda route: route.abort("connectionrefused"))
    page2.goto(BASE + "/app", wait_until="domcontentloaded")
    page2.wait_for_timeout(2500)
    check("offline: stays on skeleton (not auth view)", page2.locator("#view-loading:not(.hidden)").count() == 1
          and page2.locator("#view-auth.hidden").count() == 1)
    status = page2.text_content("#loading-status") or ""
    check("offline: waking-up status shown", "Connecting" in status or "aking" in status, status.strip())
    # let the server "come back" — the boot loop should recover by itself on the next attempt
    page2.unroute("**/api/**")
    page2.wait_for_selector("#view-main:not(.hidden)", timeout=25000)
    check("offline → online: auto-recovers to main view", True)

    # ── hard failure (4xx) → Retry button, then Retry works ─────────────
    page3 = ctx.new_page()
    page3.route("**/api/me", lambda route: route.fulfill(status=400, content_type="application/json", body='{"ok":false,"error":"Bad init data"}'))
    page3.goto(BASE + "/app", wait_until="domcontentloaded")
    page3.wait_for_selector("#btn-retry:not(.hidden)", timeout=10000)
    check("4xx: Retry button shown", True, page3.text_content("#loading-status").strip())
    check("4xx: skeleton marked failed", page3.locator("#view-loading.failed").count() == 1)
    page3.unroute("**/api/me")
    page3.click("#btn-retry")
    page3.wait_for_selector("#view-main:not(.hidden)", timeout=15000)
    check("Retry recovers to main view", True)

    # ── locked scenario ─────────────────────────────────────────────────
    if LOCKED_PORT:
        page4 = ctx.new_page()
        page4.goto(f"http://localhost:{LOCKED_PORT}/app", wait_until="domcontentloaded")
        try:
            page4.wait_for_selector("#view-locked:not(.hidden)", timeout=15000)
            check("locked harness shows locked view", True, (page4.text_content("#view-locked h1") or "").strip())
        except Exception as e:
            check("locked harness shows locked view", False, str(e)[:80])

    check("no JS errors overall", not errors, errors[:3])
    browser.close()

print()
print("ALL GOOD ✅" if not fails else f"{fails} check(s) FAILED ❌")
sys.exit(1 if fails else 0)
