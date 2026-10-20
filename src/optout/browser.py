"""Assisted browser runner.

Drives the user's own installed Chrome through Playwright with a persistent
profile, so sites see a normal returning user: real user agent, real
cookies, no `navigator.webdriver`, human-paced typing. The session fills
every form field it can recognise, and re-fills automatically whenever the
page changes (multi-step forms). CAPTCHAs and the final submit stay with
the user.
"""
from __future__ import annotations

import random
import re
import sys
import time
import webbrowser

from .catalog import Unit
from .identity import US_STATES
from .paths import browser_profile_dir

# field -> regex tested against the input's combined metadata (lower-cased)
FIELD_PATTERNS: list[tuple[str, str]] = [
    ("first_name", r"first.?name|fname|given|\bfirst\b"),
    ("last_name", r"last.?name|lname|surname|family|\blast\b"),
    ("middle_name", r"middle"),
    ("email", r"e-?mail"),
    ("phone", r"phone|\btel\b|telephone|mobile|cell"),
    ("line2", r"address.?2|addr2|line.?2|\bapt\b|suite|unit"),
    ("line1", r"address.?1|addr1|line.?1|street|\baddress\b"),
    ("city", r"\bcity\b|town"),
    ("zip", r"zip|postal"),
    ("state", r"\bstate\b|province|region"),
    ("date_of_birth", r"birth|\bdob\b"),
    ("full_name", r"full.?name|your.?name|\bname\b"),
]
NEVER_FILL = re.compile(
    r"password|passwd|ssn|social|security.?number|card|cvv|cvc|captcha|search|coupon|promo|user.?name|login|company|file",
    re.I,
)

COLLECT_JS = """
() => {
  const out = [];
  const els = Array.from(document.querySelectorAll('input, select, textarea'));
  let i = 0;
  for (const el of els) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset','checkbox','radio','file','password'].includes(type)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    let label = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = l.innerText; }
    if (!label) { const p = el.closest('label'); if (p) label = p.innerText; }
    const meta = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                  el.getAttribute('autocomplete'), label, el.getAttribute('data-testid')]
                  .filter(Boolean).join(' | ').toLowerCase();
    el.setAttribute('data-optout-idx', String(i));
    out.push({idx: i, tag: el.tagName.toLowerCase(), type, meta,
              value: el.value || '',
              options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => [o.value, o.text]) : null});
    i++;
  }
  return out;
}
"""

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
"""


def _match_field(meta: str) -> str | None:
    if NEVER_FILL.search(meta):
        return None
    for fld, pat in FIELD_PATTERNS:
        if re.search(pat, meta):
            return fld
    return None


def _pick_state_option(options, abbrev: str) -> str | None:
    name = US_STATES.get(abbrev.upper(), "").lower()
    for value, text in options:
        if value.upper() == abbrev.upper() or text.strip().upper() == abbrev.upper():
            return value
    for value, text in options:
        if name and text.strip().lower() == name:
            return value
    return None


def _human_type(locator, text: str) -> None:
    locator.click()
    time.sleep(random.uniform(0.15, 0.4))
    locator.fill("")
    locator.press_sequentially(text, delay=random.uniform(45, 110))


def autofill(page, values: dict[str, str], used: set[str] | None = None) -> list[tuple[str, str]]:
    """Fill recognised, still-empty fields on `page`. Returns [(field, meta)]."""
    used = used if used is not None else set()
    filled: list[tuple[str, str]] = []
    fields = page.evaluate(COLLECT_JS)
    for f in fields:
        fld = _match_field(f["meta"])
        if not fld or fld in used:
            continue
        val = values.get(fld, "")
        if not val or f["value"].strip():
            continue
        loc = page.locator(f'[data-optout-idx="{f["idx"]}"]').first
        try:
            if f["tag"] == "select":
                opt = _pick_state_option(f["options"], val) if fld == "state" else None
                if opt is None:
                    continue
                loc.select_option(opt)
            else:
                _human_type(loc, val)
            used.add(fld)
            filled.append((fld, f["meta"][:60]))
            time.sleep(random.uniform(0.2, 0.6))
        except Exception as exc:  # noqa: BLE001 - best effort per field
            filled.append((fld, f"(failed: {exc.__class__.__name__})"))
    return filled


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class BrowserSession:
    """One Chrome window for a whole run; a fresh tab per registry.

    Must be created, used and closed from the same thread (Playwright sync API).
    """

    def __init__(self, log=print):
        self.log = log
        self._pw = None
        self.ctx = None
        self.page = None
        self._last_url = ""
        self._last_sig = ""
        self.values: dict[str, str] = {}

    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch = dict(
            user_data_dir=str(browser_profile_dir()),
            headless=False,
            viewport=None,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            ignore_default_args=["--enable-automation"],
            locale="en-US",
        )
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **launch)
        except Exception:  # noqa: BLE001
            self.log("Installed Chrome not found; using Playwright's Chromium.")
            self.ctx = self._pw.chromium.launch_persistent_context(**launch)
        self.ctx.add_init_script(STEALTH_JS)

    def start(self, unit: Unit) -> list[tuple[str, str]]:
        """Open the registry page in a new tab and autofill."""
        self.values = unit.values()
        for p in list(self.ctx.pages):
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass
        self.page = self.ctx.new_page()
        self.page.goto(unit.registry.url, wait_until="domcontentloaded")
        time.sleep(random.uniform(1.0, 2.0))
        self._last_url = self.page.url
        if unit.registry.automation != "assisted":
            return []
        return self.fill()

    def fill(self) -> list[tuple[str, str]]:
        try:
            filled = autofill(self.page, self.values)
        except Exception as exc:  # noqa: BLE001
            self.log(f"autofill error: {exc.__class__.__name__}")
            return []
        self._last_sig = self._signature()
        return filled

    def _signature(self) -> str:
        try:
            return self.page.evaluate("() => document.querySelectorAll('input,select,textarea').length + '|' + location.href")
        except Exception:  # noqa: BLE001
            return ""

    def poll(self) -> list[tuple[str, str]]:
        """Call periodically; re-fills when the page navigated or the form changed."""
        if self.page is None:
            return []
        try:
            if self.page.is_closed():
                return []
            sig = self._signature()
        except Exception:  # noqa: BLE001
            return []
        if sig and sig != self._last_sig:
            time.sleep(0.8)
            return self.fill()
        return []

    def page_closed(self) -> bool:
        try:
            return self.page is None or self.page.is_closed()
        except Exception:  # noqa: BLE001
            return True

    def close(self) -> None:
        for obj, meth in ((self.ctx, "close"), (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:  # noqa: BLE001
                pass
        self.ctx = self._pw = self.page = None


def open_plain(url: str) -> None:
    webbrowser.open(url)


def run_assisted(unit: Unit, *, fill: bool = True, submit: bool = False, console=print, session: BrowserSession | None = None) -> str:
    """CLI flow: open the page, autofill, wait for the user at the terminal."""
    reg = unit.registry
    if not playwright_available():
        console("Playwright not installed (uv sync --all-extras). Opening in default browser instead.")
        open_plain(reg.url)
        return _ask_outcome(console)

    own = session is None
    if own:
        session = BrowserSession(log=console)
        session.open()
    try:
        filled = session.start(unit) if fill else []
        if reg.automation == "prohibited":
            console("This site requires identity verification / forbids automation; fill it in yourself.")
        elif filled:
            console("Autofilled: " + ", ".join(f for f, _ in filled))
        else:
            console("No recognisable fields yet; the form will be filled automatically as you navigate.")
        console("")
        console("Values for this submission:")
        for k in reg.fields:
            _print_field(k, session.values, console)
        console("")
        console("Finish in the browser (CAPTCHA, review, submit). Then: [d]one  [s]kip  [f]ill again  [x] failed  [p]ending")
        while True:
            try:
                ans = input("> ").strip().lower()
            except EOFError:
                ans = "p"
            if ans in ("d", "done"):
                return "done"
            if ans in ("s", "skip"):
                return "skipped"
            if ans in ("x", "fail", "failed"):
                return "failed"
            if ans in ("p", "pending", "q", ""):
                return "pending"
            if ans in ("f", "fill"):
                filled = session.fill()
                console("Autofilled: " + (", ".join(f for f, _ in filled) or "nothing"))
                continue
            console("d / s / f / x / p")
    finally:
        if own:
            session.close()


def _print_field(k: str, values: dict[str, str], console) -> None:
    if k == "name":
        console(f"  name:     {values.get('full_name','')}")
    elif k == "address":
        console(f"  address:  {values.get('address','')}")
    elif k == "phones":
        console(f"  phone(s): {values.get('phones','')}")
    else:
        console(f"  {k}: {values.get(k,'')}")


def _ask_outcome(console) -> str:
    console("When finished: [d]one  [s]kip  [x] failed  [p]ending")
    try:
        ans = input("> ").strip().lower()
    except EOFError:
        return "pending"
    return {"d": "done", "s": "skipped", "x": "failed"}.get(ans[:1], "pending") if ans else "pending"


def is_tty() -> bool:
    return sys.stdin.isatty()
