#!/usr/bin/env python3
"""
browser_smoke.py — render the live dashboard in a REAL browser and assert it.

The other suites each stop one step short of a browser:

  * `smoke_test.py`      — does the engine boot?
  * `sync_test.py`       — does the monitor's payload match the engine?
  * `scripts/ui_smoke.mjs` — does the React tree render to static markup?

None of them runs Blink, so none can catch a defect that only exists once the
page is laid out: a bundle that 404s after a deploy, a card inside a
`display:none` container, a value that renders a constant instead of the served
one, a component that throws while the page is live.

This is that check. It launches a headless Chromium and drives it over the Chrome
DevTools Protocol directly — no Playwright/Puppeteer dependency — loads the
running monitor's page, and asserts against the DOM **and** against the same
`/api/status` payload the page itself fetched. Comparing a rendered figure to its
source (not to a constant copied into this file) is the whole point: that is the
check that would fail if a card went back to printing a literal.

It is an INTEGRATION check — it needs a running monitor and a Chromium. When
either is missing it prints SKIP and exits 0, so `npm run verify` stays green on
a machine that has neither, exactly as `check_env_drift.py` skips with no `.env`.
`--strict` turns those skips into failures for a host that is supposed to have
both.

    ./venv/bin/python3 browser_smoke.py
    ./venv/bin/python3 browser_smoke.py --url http://127.0.0.1:3000/
    ./venv/bin/python3 browser_smoke.py --chrome /usr/bin/chromium --strict
    npm run test:browser                      # from the repo root

Chromium is discovered from, in order: `--chrome`, `$CHROME_BIN` (or
`$CHROMIUM_BIN` / `$GOOGLE_CHROME_BIN`), `PATH` (`google-chrome`, `chromium`,
`chrome`, …), the Playwright browser cache (`~/.cache/ms-playwright`), and the
Puppeteer cache (`~/.cache/puppeteer`). No browser is downloaded.

Exit codes: 0 = pass, or SKIP when the browser / monitor / `websockets` client is
absent; 1 = a rendered-DOM assertion failed, or a skip under `--strict`.
"""

import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "http://127.0.0.1:3000/"

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m",
)
# Strip colour when the output is captured (npm, CI, a pipe) or NO_COLOR is set.
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    BOLD = DIM = RED = GREEN = YELLOW = RESET = ""

FAILURES = []
SKIPS = []

# Requests a browser makes on its own and that no deploy is responsible for.
# A 404 here is noted, never failed on — this file must not turn a cosmetic
# omission into a red battery.
IGNORED_FAILED_URLS = ("/favicon.ico",)

# Headings the desk must render. Matched as substrings against the DOM's h1–h3.
EXPECTED_HEADINGS = (
    "Engine Health",
    "Monitored Symbols",
    "Capital Roadmap",
    "Active Positions",
    "Recent Completed Trades",
)

# Strings that should never survive into a rendered DOM — each one means a
# missing guard upstream (`undefined` leaking from an absent payload field,
# arithmetic on a null, a stringified object printed as a value).
CANARIES = ("NaN", "undefined", "Infinity", "[object Object]", "$NaN")


def check(label, ok, detail=""):
    if ok:
        print(f"  [{GREEN}PASS{RESET}] {label}")
    else:
        print(f"  [{RED}FAIL{RESET}] {label}" + (f"\n         {detail}" if detail else ""))
        FAILURES.append(label)


def skip(label, detail=""):
    print(f"  [{YELLOW}SKIP{RESET}] {label}" + (f"\n         {detail}" if detail else ""))
    SKIPS.append(label)


# ── Chromium discovery ───────────────────────────────────────────────────────

def _candidates(explicit=None):
    """>=1.0 yield candidate interpreter paths, most specific first."""
    if explicit:
        yield explicit
    for var in ("CHROME_BIN", "CHROMIUM_BIN", "GOOGLE_CHROME_BIN"):
        val = os.environ.get(var)
        if val:
            yield val
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome", "chrome-headless-shell"):
        found = shutil.which(name)
        if found:
            yield found
    home = os.path.expanduser("~")
    for pattern in (
        f"{home}/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
        f"{home}/.cache/ms-playwright/chromium_headless_shell-*/"
        "chrome-headless-shell-linux*/chrome-headless-shell",
        f"{home}/.cache/puppeteer/chrome/*/chrome-linux*/chrome",
        f"{home}/Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
        "Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        # Newest versioned directory first.
        for path in sorted(glob.glob(pattern), reverse=True):
            yield path


def find_chrome(explicit=None):
    for path in _candidates(explicit):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


# ── Tiny CDP client ──────────────────────────────────────────────────────────

DEVNULL = subprocess.DEVNULL


def _read_debug_port(profile, proc, timeout):
    """Read the port Chromium picked for `--remote-debugging-port=0`."""
    port_file = os.path.join(profile, "DevToolsActivePort")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:          # died (missing libs, bad flag, …)
            return None
        if os.path.exists(port_file):
            try:
                with open(port_file) as fh:
                    first = fh.readline().strip()
                if first.isdigit() and int(first) > 0:
                    return int(first)
            except OSError:
                pass
        time.sleep(0.15)
    return None


def launch_chrome(chrome, workdir, timeout=25):
    """Start headless Chromium; return (proc, port) or (None, None)."""
    for headless in ("--headless=new", "--headless"):
        profile = os.path.join(workdir, "profile")
        proc = subprocess.Popen(
            [chrome, headless, "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--disable-extensions", "--no-first-run",
             "--no-default-browser-check", "--hide-scrollbars",
             "--window-size=1600,1400", "about:blank"],
            # Own process group: Chromium forks zygote/gpu/renderer children that
            # keep writing to the profile after the parent is signalled, which is
            # how a cleanup race turns a passing run into a failed one.
            stdout=DEVNULL, stderr=DEVNULL, start_new_session=True,
        )
        port = _read_debug_port(profile, proc, timeout)
        if port:
            return proc, port
        _kill(proc)
    return None, None


def _kill(proc):
    """Stop the browser *and its children*, then let the profile settle.

    Signalling only the parent leaves Chromium's child processes writing to the
    user-data-dir; the caller then deletes a directory that is still being
    modified. Kill the whole process group instead, and wait for it to go.
    """
    if proc is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:                                        # noqa: BLE001
            try:
                proc.send_signal(sig)
            except Exception:                                    # noqa: BLE001
                pass
        try:
            proc.wait(timeout=10)
            break
        except Exception:                                        # noqa: BLE001
            continue
    time.sleep(0.3)          # let the last profile writes land


def _http_json(port, path, timeout=5):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{port}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CDP:
    """Minimal Chrome DevTools Protocol client: send a command, await its reply.

    Events that arrive while awaiting a reply are buffered, which is how the
    console/exception/network evidence is collected without a second connection.
    """

    def __init__(self, ws):
        self.ws = ws
        self.events = []
        self._id = 0

    async def send(self, method, params=None):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                return msg
            self.events.append(msg)

    async def evaluate(self, expr):
        reply = await self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        result = (reply.get("result") or {})
        if "exceptionDetails" in result:
            return None
        return (result.get("result") or {}).get("value")

    def failures_from_events(self):
        """(exceptions, console_problems, failed_urls) seen while loading."""
        exceptions, console, failed = [], [], {}
        for event in self.events:
            method = event.get("method")
            params = event.get("params") or {}
            if method == "Runtime.exceptionThrown":
                details = params.get("exceptionDetails") or {}
                exceptions.append(
                    (details.get("text") or "")
                    + " " + str((details.get("exception") or {}).get("description", ""))[:300]
                )
            elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "warning"):
                args = " ".join(
                    str(a.get("value", a.get("description", "")))[:160]
                    for a in params.get("args", [])
                )
                console.append(f"{params['type']}: {args}")
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                if entry.get("level") in ("error", "warning"):
                    console.append(f"{entry.get('level')}: {entry.get('text', '')[:200]}")
            elif method == "Network.responseReceived":
                response = params.get("response") or {}
                if response.get("status", 0) >= 400:
                    failed[response.get("url", "?")] = response["status"]
            elif method == "Network.loadingFailed":
                failed[params.get("url") or f"<{params.get('requestId')}>"] = params.get("errorText")
        return exceptions, console, failed


# ── DOM probes (validated against the live dashboard) ────────────────────────

CARD_EXPR = r"""
(() => {
  const h = [...document.querySelectorAll('h3')]
    .find(e => e.textContent.includes('Capital Roadmap'));
  if (!h) return null;
  const card = h.closest('[class*="rounded-xl"]') || h.parentElement.parentElement;
  const r = card.getBoundingClientRect();
  const cs = getComputedStyle(card);
  const chipTexts = [...card.querySelectorAll('span')]
    .map(s => s.textContent.trim())
    .filter(t => /USDT/.test(t) && t.length < 40);
  return {
    text: card.innerText,
    rect: {w: Math.round(r.width), h: Math.round(r.height)},
    display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
    chips: [...new Set(chipTexts)],
  };
})()
"""

PAGE_EXPR = r"""
(() => {
  const body = document.body.innerText;
  const heads = [...document.querySelectorAll('h1,h2,h3')]
    .map(e => e.textContent.trim()).filter(Boolean);
  return {
    title: document.title,
    ready: document.readyState,
    headings: [...new Set(heads)],
    tabs: [...document.querySelectorAll('nav button')]
      .map(e => e.textContent.trim()).filter(Boolean),
    textLen: body.length,
    canaries: CANARIES.filter(t => body.includes(t)),
  };
})()
""".replace("CANARIES", json.dumps(list(CANARIES)))


def fetch_payload(url, timeout=10):
    """The same `/api/status` the page fetched — the source we compare against."""
    req = urllib.request.Request(url.rstrip("/") + "/api/status")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── The run ──────────────────────────────────────────────────────────────────

async def run(url, chrome, workdir, wait_s, payload):
    proc, port = launch_chrome(chrome, workdir)
    if not port:
        skip("chromium launches and exposes the DevTools endpoint",
             "the binary exists but never answered on DevToolsActivePort — "
             "missing shared libraries or a blocked sandbox. Try --chrome PATH.")
        return

    try:
        version = _http_json(port, "/json/version")
        print(f"{DIM}  {version.get('Browser')} · CDP {version.get('Protocol-Version')}{RESET}")
        check("chromium launches and exposes the DevTools endpoint", True)

        targets = _http_json(port, "/json/list")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            check("chromium offers a page target", False, f"targets={[t.get('type') for t in targets]}")
            return
        check("chromium offers a page target", True)

        # Imported late so the absence of the client is a SKIP, not a crash.
        try:
            try:
                from websockets.asyncio.client import connect as ws_connect
            except ImportError:                                 # older websockets
                from websockets import connect as ws_connect
        except ImportError as exc:
            skip("websockets client is importable", f"{exc} — install requirements.txt")
            return

        roadmap = payload.get("roadmap")

        async with ws_connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = CDP(ws)
            for domain in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
                await cdp.send(domain)
            await cdp.send("Page.navigate", {"url": url})

            card = None
            deadline = time.time() + wait_s
            while time.time() < deadline:
                await asyncio.sleep(0.3)
                state = await cdp.evaluate("document.readyState")
                card = await cdp.evaluate(CARD_EXPR)
                if state == "complete" and roadmap is None:
                    break                    # nothing more will appear; no roadmap
                if isinstance(card, dict) and card.get("text"):
                    if not roadmap or "USDT" in card["text"] or "pairs" in card["text"]:
                        break

            page_info = await cdp.evaluate(PAGE_EXPR) or {}
            exceptions, console, failed = cdp.failures_from_events()

            # ── the page itself ──────────────────────────────────────────────
            check("the page reached readyState=complete",
                  page_info.get("ready") == "complete", f"got {page_info.get('ready')!r}")
            check("the dashboard rendered (not a blank shell)",
                  (page_info.get("textLen") or 0) > 2000,
                  f"body text = {page_info.get('textLen')} chars")
            headings = page_info.get("headings") or []
            missing = [h for h in EXPECTED_HEADINGS
                       if not any(h in rendered for rendered in headings)]
            check(f"all {len(EXPECTED_HEADINGS)} desk sections rendered",
                  not missing, f"missing: {missing} — rendered: {headings}")
            check("the 5-tab nav rendered",
                  len(page_info.get("tabs") or []) == 5, f"tabs={page_info.get('tabs')}")
            canaries = page_info.get("canaries") or []
            check("no render-bug canaries in the DOM", not canaries, f"found: {canaries}")
            check("no uncaught JS exception during load", not exceptions,
                  "; ".join(exceptions[:3]))

            ignorable = {u: s for u, s in failed.items()
                         if any(u.endswith(i) for i in IGNORED_FAILED_URLS)}
            real_failures = {u: s for u, s in failed.items() if u not in ignorable}
            detail = f"failed: {real_failures}"
            if ignorable:
                detail += f" (ignored: {list(ignorable)})"
            check("every request the page made succeeded", not real_failures, detail)

            # ── the Capital Roadmap card ─────────────────────────────────────
            # The roadmap is LIVE, and the page is fed by the 1 Hz /ws push, so the
            # payload fetched before navigation can already describe a different
            # universe than the card on screen (the engine's screener rotates the
            # watchlist, which re-partitions ok/blocked pairs). Comparing the two
            # across that rotation read as "the card dropped a chip". Re-read the
            # payload AND the card together until the pair is self-consistent, then
            # run the same strict DOM-vs-payload checks on it.
            if roadmap is not None and isinstance(card, dict):
                for attempt in range(4):
                    gap = sorted(_expected_chips(roadmap)
                                 - set(card.get("chips") or []))
                    if not gap and not _contradicting_chips(card, roadmap):
                        break
                    try:
                        fresh = (fetch_payload(url) or {}).get("roadmap")
                    except Exception:
                        break          # keep the last pair; report its mismatch
                    fresh_card = await cdp.evaluate(CARD_EXPR)
                    if not isinstance(fresh, dict) or not isinstance(fresh_card, dict):
                        break
                    if attempt == 0:
                        print(f"{DIM}  roadmap rotated since the payload read — "
                              f"re-reading the card and the payload together{RESET}")
                    roadmap, card = fresh, fresh_card
                    await asyncio.sleep(1.2)

            if roadmap is None:
                skip("capital roadmap card checks",
                     "the served payload carries no `roadmap` — nothing for the "
                     "card to render (the monitor computes it on its own cadence).")
            elif not isinstance(card, dict) or not card.get("text"):
                check("the capital roadmap card rendered", False,
                      "no <h3>Capital Roadmap</h3> in the DOM")
            else:
                _check_roadmap(card, roadmap)

            if console:
                print(f"{DIM}  console output: {'; '.join(console[:3]) if console else 'clean'}{RESET}")
    finally:
        _kill(proc)


def _check_roadmap(card, roadmap):
    """The card's rendered values vs the payload the page fetched.

    Every comparison below is DOM-vs-payload, never DOM-vs-constant: that is what
    makes this able to catch a chip that went back to printing a literal.
    """
    text = card["text"]
    rect = card.get("rect") or {}
    laid_out = (rect.get("w", 0) > 0 and rect.get("h", 0) > 0
                and card.get("display") != "none"
                and card.get("visibility") == "visible"
                and float(card.get("opacity") or 0) > 0)
    check("the capital roadmap card is laid out and visible", laid_out,
          f"rect={rect} display={card.get('display')} visibility={card.get('visibility')} "
          f"opacity={card.get('opacity')}")

    # Which universe: the label the card prints must match what the server says
    # it used, and the size must match the served pair list.
    source = roadmap.get("pairs_source")
    expected_label = ("fallback pairs" if source == "default"
                      else "engine-watched pairs")
    n_pairs = len(roadmap.get("pairs") or [])
    check("the card names the universe the server reported "
          f"({source or 'unset'}) and its size ({n_pairs})",
          f"{n_pairs} {expected_label}" in text,
          f"expected {n_pairs} {expected_label!r} in card text")

    check("the card states how old its snapshot is",
          "computed" in text or "refreshed" in text,
          "no age label — the numbers are a cached snapshot and must say so")

    # Per-pair thresholds, compared to the payload's own figures.
    blocked = list(roadmap.get("blocked_pairs") or [])
    ok_pairs = list(roadmap.get("ok_pairs") or [])
    chips = set(card.get("chips") or [])

    missing_ok = [s for s in ok_pairs if s not in chips]
    check("every viable pair renders a chip", not missing_ok, f"missing: {missing_ok}")

    if not blocked:
        print(f"{DIM}  (no blocked pairs in the served watchlist — "
              f"threshold chips not exercised today){RESET}")
    else:
        wanted = sorted(_expected_chips(roadmap) - set(ok_pairs))
        missing = [w for w in wanted if w not in chips]
        check("every blocked pair renders a chip carrying the SERVED threshold",
              not missing, f"expected {wanted} among chips={sorted(chips)}")

        # The differential check: no rendered threshold may contradict the
        # payload. A hardcoded chip fails here even when it happens to be right,
        # because the value it prints belongs to a different pair's floor.
        contradictions = _contradicting_chips(card, roadmap)
        check("no rendered threshold contradicts the served value",
              not contradictions, f"{contradictions}")


def _expected_chips(roadmap):
    """The chip labels this payload requires the card to render.

    An OK pair renders its bare symbol (there is no threshold to show); a BLOCKED
    pair renders `SYM @ $N` from the payload's own per-pair requirement.
    """
    required = {p.get("symbol"): p.get("required_equity")
                for p in (roadmap.get("pairs") or [])}
    labels = {s for s in (roadmap.get("ok_pairs") or []) if s}
    for sym in (roadmap.get("blocked_pairs") or []):
        value = required.get(sym)
        labels.add(f"{sym} @ ${round(value):,}" if isinstance(value, (int, float))
                   else sym)
    return labels


def _contradicting_chips(card, roadmap):
    """Rendered thresholds that disagree with the payload's own figures."""
    required = {p.get("symbol"): p.get("required_equity")
                for p in (roadmap.get("pairs") or [])}
    out = []
    for chip in sorted(card.get("chips") or []):
        match = re.match(r"^([A-Z0-9]+)\s+@\s+\$([\d,]+)$", chip)
        if not match:
            continue
        sym, shown = match.group(1), int(match.group(2).replace(",", ""))
        served = required.get(sym)
        if not isinstance(served, (int, float)) or abs(served - shown) > 0.5:
            out.append(f"{chip} (served {served})")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Render the running dashboard in a real browser and assert it "
                    "(read-only: loads the page, reads the DOM, places no orders)."
    )
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"monitor base URL (default: {DEFAULT_URL})")
    parser.add_argument("--chrome", default=None,
                        help="path to a Chromium/Chrome binary (default: auto-discover)")
    parser.add_argument("--timeout", type=float, default=25.0,
                        help="seconds to wait for the page/card to render (default: 25)")
    parser.add_argument("--strict", action="store_true",
                        help="treat a SKIP as a failure (for a host that should have "
                             "both a browser and a running monitor)")
    args = parser.parse_args()

    print(f"{BOLD}browser render check{RESET} — {args.url}")

    # 1. A running monitor. Without one there is no page to render.
    try:
        payload = fetch_payload(args.url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        skip("the web monitor answers /api/status", f"{exc}")
        print(f"\n{YELLOW}SKIPPED{RESET} — start it with `pm2 start bot-web-monitor` "
              f"(or `./venv/bin/python3 status.py --web`) to run this check.")
        return 1 if args.strict else 0
    print(f"{DIM}  monitor reachable · mode={payload.get('mode')} · "
          f"open_positions={payload.get('open_positions')}{RESET}")

    # 2. A browser.
    chrome = find_chrome(args.chrome)
    if not chrome:
        skip("a Chromium/Chrome binary is available",
             "none found in --chrome, $CHROME_BIN, PATH, the Playwright cache or "
             "the Puppeteer cache. `npx playwright install chromium` provides one.")
        print(f"\n{YELLOW}SKIPPED{RESET} — no browser to drive; the DOM is not checked.")
        return 1 if args.strict else 0
    print(f"{DIM}  chrome: {chrome}{RESET}")

    # mkdtemp + an explicit, error-tolerant rmtree rather than TemporaryDirectory:
    # a browser profile is a moving target, and a cleanup race must never turn a
    # passing run into a failed one.
    workdir = tempfile.mkdtemp(prefix="browser-smoke-")
    try:
        asyncio.run(run(args.url, chrome, workdir, args.timeout, payload))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{RED}FAILED: {FAILURES}{RESET}")
        return 1
    if SKIPS and args.strict:
        print(f"{RED}FAILED (--strict): skipped {SKIPS}{RESET}")
        return 1
    if SKIPS:
        print(f"{YELLOW}SKIP_OK{RESET} — {len(SKIPS)} group(s) skipped, nothing failed.")
        return 0
    print(f"{GREEN}BROWSER_OK{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
