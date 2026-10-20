"""Assisted browser runner.

Drives the user's own installed Chrome through Playwright with a persistent
profile, so sites see a normal returning user: real user agent, real
cookies, no `navigator.webdriver`, human-paced typing. The tool fills the
form fields it can recognise and then hands the page to you to solve any
CAPTCHA, review the values and click submit yourself.
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
NEVER_FILL = re.compile(r"password|passwd|ssn|social|security.?number|card|cvv|cvc|captcha|search|coupon|promo|user.?name|login|company|file", re.I)

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
    """Fill recognised fields on `page`. Returns [(field, meta)] filled."""
    used = used if used is not None else set()
    filled: list[tuple[str, str]] = []
    fields = page.evaluate(COLLECT_JS)
    for f in fields:
        fld = _match_field(f["meta"])
        if not fld or fld in used:
            continue
        val = values.get(fld, "")
        if fld == "full_name" and not val:
            val = values.get("full_name", "")
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


def open_plain(url: str) -> None:
    webbrowser.open(url)


def run_assisted(unit: Unit, *, fill: bool = True, submit: bool = False, console=print) -> str:
    """Open the registry page in the user's Chrome, autofill, wait for the user.

    Returns the final status the user chose: done | skipped | failed | pending.
    """
    reg = unit.registry
    if not playwright_available():
        console("Playwright not installed (pip install 'optout[browser]' && playwright install chromium). Opening in default browser instead.")
        open_plain(reg.url)
        return _ask_outcome(console)

    from playwright.sync_api import sync_playwright

    values = unit.values()
    with sync_playwright() as pw:
        launch = dict(
            user_data_dir=str(browser_profile_dir()),
            headless=False,
            viewport=None,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            ignore_default_args=["--enable-automation"],
            locale="en-US",
        )
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **launch)
        except Exception:
            console("Installed Chrome not found; falling back to Playwright's Chromium.")
            ctx = pw.chromium.launch_persistent_context(**launch)
        ctx.add_init_script(STEALTH_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(reg.url, wait_until="domcontentloaded")
        time.sleep(random.uniform(1.0, 2.0))

        if fill and reg.automation == "assisted":
            filled = autofill(page, values)
            if filled:
                console("Autofilled: " + ", ".join(f for f, _ in filled))
            else:
                console("No recognisable fields on this page yet (multi-step form?). Type 'f' to retry autofill after navigating.")
        elif reg.automation == "prohibited":
            console("This site requires identity verification / forbids automation; fill it in yourself.")

        console("")
        console("Values for this submission:")
        for k in reg.fields:
            _print_field(k, values, console)
        console("")
        console("Finish in the browser (CAPTCHA, review, submit). Then here: [d]one  [s]kip  [f]ill again  [x] failed  [p]ending")
        used: set[str] = set()
        while True:
            try:
                ans = input("> ").strip().lower()
            except EOFError:
                ans = "p"
            if ans in ("d", "done"):
                status = "done"
                break
            if ans in ("s", "skip"):
                status = "skipped"
                break
            if ans in ("x", "fail", "failed"):
                status = "failed"
                break
            if ans in ("p", "pending", "q", ""):
                status = "pending"
                break
            if ans in ("f", "fill"):
                try:
                    filled = autofill(page, values, used=set())
                    console("Autofilled: " + (", ".join(f for f, _ in filled) or "nothing"))
                except Exception as exc:  # noqa: BLE001
                    console(f"Autofill error: {exc}")
                continue
            console("d / s / f / x / p")
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return status


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
