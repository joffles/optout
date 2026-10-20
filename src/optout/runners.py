"""Dispatch a Unit to the right channel: web, email, phone or postal mail."""
from __future__ import annotations

import os
import platform
import smtplib
import subprocess
import urllib.parse
import webbrowser
from email.message import EmailMessage
from pathlib import Path

from . import browser
from .catalog import Unit
from .letters import email_body, email_subject, write_letter


def run_unit(unit: Unit, *, fill: bool = True, mode: str = "auto", out_dir: Path | None = None, console=print) -> str:
    m = unit.registry.method
    if m == "web":
        return browser.run_assisted(unit, fill=fill, console=console)
    if m == "email":
        return run_email(unit, mode=mode, out_dir=out_dir, console=console)
    if m == "phone":
        return run_phone(unit, console=console)
    if m == "mail":
        path = write_letter(unit, out_dir)
        console(f"Letter written: {path}  (print, sign, mail)")
        return "pending"
    raise ValueError(f"unknown method {m}")


def run_email(unit: Unit, *, mode: str = "auto", out_dir: Path | None = None, console=print) -> str:
    reg = unit.registry
    subject = email_subject(unit)
    body = email_body(unit)
    sender = unit.values().get("email", "")

    if mode == "auto":
        mode = "smtp" if os.environ.get("OPTOUT_SMTP_HOST") else "mailto"

    if mode == "smtp":
        host = os.environ["OPTOUT_SMTP_HOST"]
        port = int(os.environ.get("OPTOUT_SMTP_PORT", "587"))
        user = os.environ.get("OPTOUT_SMTP_USER", "")
        pw = os.environ.get("OPTOUT_SMTP_PASS", "")
        frm = os.environ.get("OPTOUT_SMTP_FROM", user or sender)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = frm, reg.email_to, subject
        msg.set_content(body)
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            if user:
                s.login(user, pw)
            s.send_message(msg)
        console(f"Sent to {reg.email_to} via {host}")
        return "done"

    if mode == "eml":
        out_dir = out_dir or Path.cwd()
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, reg.email_to, subject
        msg.set_content(body)
        path = out_dir / f"{reg.id}-{unit.person.id}.eml"
        path.write_bytes(bytes(msg))
        console(f"Draft written: {path}")
        return "pending"

    # mailto: opens the user's mail client with everything prefilled
    q = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    url = f"mailto:{reg.email_to}?{q}"
    webbrowser.open(url)
    console(f"Opened a draft to {reg.email_to} in your mail client.")
    console("Send it, then: [d]one  [s]kip  [p]ending")
    return browser._ask_outcome(console)


def run_phone(unit: Unit, *, console=print) -> str:
    reg = unit.registry
    console(f"Call {reg.phone} from {unit.describe()}.")
    if platform.system() == "Darwin":
        digits = "".join(ch for ch in reg.phone if ch.isdigit())
        try:
            subprocess.run(["open", f"tel:{digits}"], check=False)
        except OSError:
            pass
    return browser._ask_outcome(console)
