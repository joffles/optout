"""Local web UI: a tiny stdlib HTTP server plus a background run worker.

`optout` with no arguments starts this and opens http://127.0.0.1:8765.
Everything stays on the machine; the server binds to localhost only.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import __version__, browser
from .catalog import Unit, load_registries, units_for
from .identity import Person, load_people
from .letters import email_body, email_subject, letter_text, write_letter
from .paths import config_dir, identity_path, letters_dir, state_path
from .runners import run_email
from .state import State

# ------------------------------------------------------------------ worker


class Runner(threading.Thread):
    """Works through a queue of units; the UI steers it through `actions`."""

    def __init__(self):
        super().__init__(daemon=True)
        self.units: list[Unit] = []
        self.index = -1
        self.phase = "idle"  # idle | opening | waiting | finishing | finished | error
        self.current: Unit | None = None
        self.filled: list[str] = []
        self.log: list[str] = []
        self.letter_path: str = ""
        self.actions: "queue.Queue[str]" = queue.Queue()
        self.email_mode = "auto"
        self.stop_flag = False
        self.results: list[tuple[str, str]] = []

    def say(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        self.log = self.log[-200:]

    def run(self) -> None:  # noqa: C901 - one long state machine
        state = State()
        session: browser.BrowserSession | None = None
        try:
            for i, unit in enumerate(self.units):
                if self.stop_flag:
                    break
                self.index, self.current, self.filled, self.letter_path = i, unit, [], ""
                reg = unit.registry
                self.phase = "opening"
                self.say(f"{reg.name}: {unit.describe()}")
                try:
                    if reg.method == "web":
                        if browser.playwright_available():
                            if session is None:
                                session = browser.BrowserSession(log=self.say)
                                session.open()
                            filled = session.start(unit)
                            self.filled = [f for f, _ in filled]
                            self.say("autofilled: " + (", ".join(self.filled) or "nothing yet"))
                        else:
                            browser.open_plain(reg.url)
                            self.say("Playwright missing; opened in default browser")
                    elif reg.method == "email":
                        status = run_email(unit, mode=self.email_mode, console=self.say) if self.email_mode == "smtp" else self._email_draft(unit)
                        if status == "done":
                            state.mark(unit.key, "done", reg.renew_days)
                            self.results.append((unit.key, "done"))
                            continue
                    elif reg.method == "mail":
                        self.letter_path = str(write_letter(unit))
                        self.say(f"letter written: {self.letter_path}")
                    elif reg.method == "phone":
                        self.say(f"call {reg.phone} from {unit.describe()}")
                except Exception as exc:  # noqa: BLE001
                    self.say(f"error: {exc}")
                    traceback.print_exc()

                self.phase = "waiting"
                status = self._wait(session if reg.method == "web" else None)
                if status == "stop":
                    break
                state.mark(unit.key, status, reg.renew_days)
                self.results.append((unit.key, status))
                self.say(f"-> {status}")
            self.phase = "finished"
        except Exception as exc:  # noqa: BLE001
            self.phase = "error"
            self.say(f"fatal: {exc}")
            traceback.print_exc()
        finally:
            if session is not None:
                session.close()
            self.current = None

    def _email_draft(self, unit: Unit) -> str:
        import urllib.parse

        q = urllib.parse.urlencode({"subject": email_subject(unit), "body": email_body(unit)}, quote_via=urllib.parse.quote)
        webbrowser.open(f"mailto:{unit.registry.email_to}?{q}")
        self.say(f"opened a draft to {unit.registry.email_to} in your mail client")
        return "pending"

    def _wait(self, session) -> str:
        while True:
            if self.stop_flag:
                return "stop"
            try:
                act = self.actions.get(timeout=1.0)
            except queue.Empty:
                if session is not None:
                    refilled = session.poll()
                    if refilled:
                        self.filled += [f for f, _ in refilled]
                        self.say("page changed; autofilled: " + ", ".join(f for f, _ in refilled))
                continue
            if act == "fill" and session is not None:
                refilled = session.fill()
                self.say("autofilled: " + (", ".join(f for f, _ in refilled) or "nothing"))
                continue
            if act in ("done", "skipped", "failed", "pending"):
                return act
            if act == "stop":
                return "stop"

    def snapshot(self) -> dict:
        cur = self.current
        return {
            "phase": self.phase,
            "index": self.index,
            "total": len(self.units),
            "current": _unit_json(cur) if cur else None,
            "filled": self.filled,
            "letter": self.letter_path,
            "log": self.log[-40:],
            "results": self.results[-50:],
        }


# ------------------------------------------------------------------ app


class App:
    def __init__(self):
        self.runner: Runner | None = None
        self.lock = threading.Lock()

    # ---- data
    def people(self) -> list[Person]:
        try:
            return load_people()
        except (FileNotFoundError, ValueError):
            return []

    def overview(self) -> dict:
        regs = load_registries()
        people = self.people()
        state = State()
        rows = []
        for r in regs:
            units = units_for(r, people)
            urows = []
            for u in units:
                rec = state.get(u.key)
                urows.append({**_unit_json(u), "record": asdict(rec) if rec else None, "due": state.needs_action(u.key)})
            rows.append(
                {
                    **{k: getattr(r, k) for k in ("id", "name", "category", "method", "url", "phone", "email_to", "per", "renew_days", "cost", "states", "automation", "notes", "verify_url")},
                    "mail_address": r.mail_address.strip(),
                    "applies": bool(units) if people else r.states is None,
                    "units": urows,
                    "due": sum(1 for u in urows if u["due"]),
                }
            )
        return {
            "version": __version__,
            "identity_exists": identity_path().exists(),
            "people": [_person_json(p) for p in people],
            "registries": rows,
            "playwright": browser.playwright_available(),
            "paths": {"identity": str(identity_path()), "state": str(state_path()), "letters": str(letters_dir())},
        }

    def save_identity(self, data: dict) -> None:
        people = []
        for p in data.get("people", []):
            if not (p.get("first_name") or p.get("last_name")):
                continue
            pid = (p.get("id") or f"{p.get('first_name','')}-{p.get('last_name','')}").strip().lower().replace(" ", "-")
            people.append(
                {
                    "id": pid,
                    "first_name": p.get("first_name", "").strip(),
                    "middle_name": p.get("middle_name", "").strip(),
                    "last_name": p.get("last_name", "").strip(),
                    "date_of_birth": p.get("date_of_birth", "").strip(),
                    "state": p.get("state", "").strip().upper(),
                    "addresses": [
                        {k: str(a.get(k, "")).strip() for k in ("label", "line1", "line2", "city", "state", "zip")} | {"current": bool(a.get("current", True))}
                        for a in p.get("addresses", [])
                        if a.get("line1")
                    ],
                    "emails": [e.strip() for e in p.get("emails", []) if e.strip()],
                    "phones": [x.strip() for x in p.get("phones", []) if x.strip()],
                }
            )
        path = identity_path()
        path.write_text("# optout identity file. Personal data; keep private.\n" + yaml.safe_dump({"people": people}, sort_keys=False, allow_unicode=True))
        os.chmod(path, 0o600)

    # ---- run control
    def start_run(self, ids: list[str] | None, categories: list[str] | None, redo: bool, email_mode: str) -> dict:
        with self.lock:
            if self.runner and self.runner.is_alive():
                return {"error": "a run is already in progress"}
            regs = load_registries()
            if ids:
                regs = [r for r in regs if r.id in set(ids)]
            if categories:
                regs = [r for r in regs if r.category in set(categories)]
            people = self.people()
            if not people:
                return {"error": "fill in your identity first"}
            state = State()
            units = [u for r in regs for u in units_for(r, people) if redo or state.needs_action(u.key)]
            if not units:
                return {"error": "nothing to do"}
            self.runner = Runner()
            self.runner.units = units
            self.runner.email_mode = email_mode
            self.runner.start()
            return {"ok": True, "total": len(units)}

    def action(self, act: str) -> dict:
        r = self.runner
        if not r or not r.is_alive():
            return {"error": "no run in progress"}
        if act == "stop":
            r.stop_flag = True
        r.actions.put(act)
        return {"ok": True}

    def run_status(self) -> dict:
        r = self.runner
        return r.snapshot() if r else {"phase": "idle", "index": -1, "total": 0, "current": None, "filled": [], "log": [], "results": [], "letter": ""}

    def mark(self, reg_id: str, status: str, unit_key: str | None) -> dict:
        regs = load_registries()
        reg = next((r for r in regs if r.id == reg_id), None)
        if not reg:
            return {"error": "unknown registry"}
        state = State()
        n = 0
        for u in units_for(reg, self.people()):
            if unit_key and u.key != unit_key:
                continue
            state.mark(u.key, status, reg.renew_days)
            n += 1
        return {"ok": True, "marked": n}

    def letter(self, reg_id: str, unit_key: str) -> str | None:
        regs = load_registries()
        reg = next((r for r in regs if r.id == reg_id), None)
        if not reg:
            return None
        for u in units_for(reg, self.people()):
            if u.key == unit_key:
                return letter_text(u)
        return None


def _person_json(p: Person) -> dict:
    return {
        "id": p.id,
        "first_name": p.first_name,
        "middle_name": p.middle_name,
        "last_name": p.last_name,
        "date_of_birth": p.date_of_birth,
        "state": p.residence_state,
        "addresses": [asdict(a) for a in p.addresses],
        "emails": p.emails,
        "phones": p.phones,
    }


def _unit_json(u: Unit) -> dict:
    return {
        "key": u.key,
        "registry": u.registry.id,
        "registry_name": u.registry.name,
        "method": u.registry.method,
        "url": u.registry.url,
        "phone_number": u.registry.phone,
        "email_to": u.registry.email_to,
        "notes": u.registry.notes.strip(),
        "person": u.person.id,
        "person_name": u.person.full_name,
        "describe": u.describe(),
        "values": {k: v for k, v in u.values().items() if k in {"full_name", "date_of_birth", "email", "phones", "address"}},
        "fields": u.registry.fields,
        "has_letter": bool(u.registry.mail_address),
    }


# ------------------------------------------------------------------ http


def make_handler(app: App):
    static_root = resources.files("optout").joinpath("static")

    class Handler(BaseHTTPRequestHandler):
        server_version = f"optout/{__version__}"

        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _text(self, text: str, ctype="text/plain; charset=utf-8", code=200, download: str | None = None):
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._text(static_root.joinpath("index.html").read_text(), "text/html; charset=utf-8")
            if u.path == "/api/overview":
                return self._json(app.overview())
            if u.path == "/api/run":
                return self._json(app.run_status())
            if u.path == "/api/letter":
                txt = app.letter(q.get("registry", [""])[0], q.get("key", [""])[0])
                if txt is None:
                    return self._json({"error": "not found"}, 404)
                return self._text(txt, download=f"{q.get('registry', ['letter'])[0]}.txt")
            return self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            data = self._body()
            if u.path == "/api/identity":
                app.save_identity(data)
                return self._json({"ok": True})
            if u.path == "/api/run/start":
                return self._json(app.start_run(data.get("ids"), data.get("categories"), bool(data.get("redo")), data.get("email_mode", "auto")))
            if u.path == "/api/run/action":
                return self._json(app.action(data.get("action", "")))
            if u.path == "/api/mark":
                return self._json(app.mark(data.get("registry", ""), data.get("status", "done"), data.get("key")))
            return self._json({"error": "not found"}, 404)

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    app = App()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"optout UI at {url}   (Ctrl-C to quit; data in {config_dir()})")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.runner and app.runner.is_alive():
            app.runner.stop_flag = True
            app.runner.actions.put("stop")
        httpd.server_close()
