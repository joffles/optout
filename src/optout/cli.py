"""optout command line."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .catalog import CATEGORIES, Registry, Unit, find, load_registries, units_for
from .identity import load_people, redact, write_template
from .letters import email_body, letter_text, write_letter
from .paths import config_dir, identity_path, state_path
from .runners import run_unit
from .state import STATUSES, State

con = Console()


def _regs(args) -> list[Registry]:
    regs = load_registries(Path(args.catalog) if getattr(args, "catalog", None) else None)
    if getattr(args, "category", None):
        regs = [r for r in regs if r.category in args.category]
    if getattr(args, "method", None):
        regs = [r for r in regs if r.method in args.method]
    return regs


def _people(args):
    people = load_people(Path(args.identity) if getattr(args, "identity", None) else None)
    if getattr(args, "person", None):
        people = [p for p in people if p.id in args.person]
        if not people:
            con.print(f"[red]No person matches {args.person}[/red]")
            sys.exit(2)
    return people


def _all_units(regs, people, state: State, *, only_due: bool) -> list[Unit]:
    out = []
    for r in regs:
        for u in units_for(r, people):
            if not only_due or state.needs_action(u.key):
                out.append(u)
    return out


def _status_cell(state: State, unit: Unit) -> str:
    rec = state.get(unit.key)
    if rec is None:
        return "[yellow]todo[/yellow]"
    if rec.status == "done":
        if rec.is_expired():
            return f"[red]expired {rec.expires}[/red]"
        if rec.expires_within(60):
            return f"[yellow]renew by {rec.expires}[/yellow]"
        return f"[green]done {rec.at}[/green]" + (f" (until {rec.expires})" if rec.expires else "")
    colour = {"pending": "cyan", "skipped": "dim", "failed": "red"}[rec.status]
    return f"[{colour}]{rec.status} {rec.at}[/{colour}]"


# ------------------------------------------------------------- commands

def cmd_init(args):
    try:
        p = write_template(force=args.force)
    except FileExistsError as e:
        con.print(f"[yellow]{e} already exists. Use --force to overwrite.[/yellow]")
        return 1
    con.print(f"Wrote template to [bold]{p}[/bold]. Edit it, then run `optout list`.")
    return 0


def cmd_identity(args):
    for p in _people(args):
        con.print(f"[bold]{p.id}[/bold]: {p.full_name}  state={p.residence_state}  dob={'set' if p.date_of_birth else 'unset'}")
        for a in p.addresses:
            con.print(f"   addr [{a.label}{'' if a.current else ', previous'}]: {a.oneline()}")
        for e in p.emails:
            con.print(f"   email: {redact(e, 3)}")
        for ph in p.phones:
            con.print(f"   phone: {redact(ph, 3)}")
    con.print(f"\nfiles: {identity_path()}  {state_path()}")
    return 0


def cmd_list(args):
    regs = _regs(args)
    try:
        people = _people(args)
    except FileNotFoundError:
        people = None
    state = State()
    t = Table(title=f"optout registries ({len(regs)})", show_lines=False)
    t.add_column("id", style="bold", no_wrap=True)
    t.add_column("name")
    t.add_column("cat")
    t.add_column("via")
    t.add_column("per")
    t.add_column("renew")
    t.add_column("cost")
    t.add_column("status" if people else "states")
    for r in regs:
        renew = "never" if r.renew_days is None else f"{r.renew_days}d"
        if people:
            units = units_for(r, people)
            if not units:
                last = "[dim]n/a (state)[/dim]"
            else:
                done = sum(1 for u in units if not state.needs_action(u.key))
                last = f"{done}/{len(units)} " + ("[green]ok[/green]" if done == len(units) else "[yellow]due[/yellow]")
        else:
            last = ",".join(r.states) if r.states else "all"
        t.add_row(r.id, r.name, r.category, r.method, r.per, renew, r.cost, last)
    con.print(t)
    return 0


def cmd_show(args):
    regs = load_registries()
    r = find(regs, args.id)
    con.print(f"[bold]{r.name}[/bold]  ({r.id})")
    for k in ("category", "method", "url", "phone", "email_to", "per", "fields", "renew_days", "cost", "states", "verify_url", "automation"):
        v = getattr(r, k)
        if v:
            con.print(f"  {k:12} {v}")
    if r.mail_address:
        con.print("  mail_address " + r.mail_address.strip().replace("\n", "\n               "))
    if r.notes:
        con.print("\n" + r.notes.strip())
    try:
        people = _people(args)
        state = State()
        con.print("")
        for u in units_for(r, people):
            con.print(f"  {u.person.id:10} {u.describe():45} {_status_cell(state, u)}")
    except FileNotFoundError:
        pass
    return 0


def cmd_status(args):
    regs = _regs(args)
    people = _people(args)
    state = State()
    units = _all_units(regs, people, state, only_due=False)
    due = [u for u in units if state.needs_action(u.key)]
    soon = [u for u in units if (rec := state.get(u.key)) and rec.status == "done" and rec.expires_within(args.days)]
    t = Table(title="status")
    t.add_column("registry", style="bold")
    t.add_column("person")
    t.add_column("unit")
    t.add_column("status")
    for u in units:
        if args.all or u in due or u in soon:
            t.add_row(u.registry.id, u.person.id, u.describe(), _status_cell(state, u))
    con.print(t)
    con.print(f"{len(units)} submissions total, [yellow]{len(due)} to do[/yellow], {len(soon)} renewing within {args.days} days.")
    return 0


def cmd_run(args):
    regs = _regs(args)
    if args.ids:
        regs = [find(regs, i) for i in args.ids]
    people = _people(args)
    state = State()
    units = _all_units(regs, people, state, only_due=not args.redo)
    if not units:
        con.print("[green]Nothing to do.[/green]")
        return 0
    con.print(f"{len(units)} submission(s) to work through. Ctrl-C to stop; progress is saved after each one.\n")
    for i, u in enumerate(units, 1):
        r = u.registry
        con.rule(f"[{i}/{len(units)}] {r.name}  -  {u.person.full_name}: {u.describe()}")
        if r.notes:
            con.print(f"[dim]{r.notes.strip()}[/dim]")
        if args.dry_run:
            con.print(f"would open: {r.url or r.email_to or r.phone}")
            continue
        try:
            status = run_unit(u, fill=not args.no_fill, mode=args.email_mode, out_dir=Path(args.out) if args.out else None, console=con.print)
        except KeyboardInterrupt:
            con.print("\nstopped; run again to continue.")
            return 130
        except Exception as exc:  # noqa: BLE001
            con.print(f"[red]error: {exc}[/red]")
            status = "failed"
        state.mark(u.key, status, r.renew_days)
        con.print(f"-> {status}\n")
    return 0


def cmd_mark(args):
    regs = load_registries()
    r = find(regs, args.id)
    people = _people(args)
    state = State()
    when = date.fromisoformat(args.date) if args.date else None
    n = 0
    for u in units_for(r, people):
        state.mark(u.key, args.status, r.renew_days, note=args.note or "", when=when)
        n += 1
    con.print(f"marked {n} submission(s) for {r.id} as {args.status}")
    return 0


def cmd_letters(args):
    regs = [r for r in _regs(args) if r.mail_address]
    if args.ids:
        regs = [find(regs, i) for i in args.ids]
    people = _people(args)
    out = Path(args.out) if args.out else None
    for r in regs:
        for u in units_for(r, people):
            if args.print:
                con.print(letter_text(u))
            else:
                con.print(f"wrote {write_letter(u, out)}")
    return 0


def cmd_email(args):
    regs = load_registries()
    r = find(regs, args.id)
    people = _people(args)
    for u in units_for(r, people):
        con.print(f"To: {r.email_to}\n")
        con.print(email_body(u))
    return 0


# ----------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="optout", description="Work through every do-not-call / do-not-mail / data-broker opt-out, locally, no AI.")
    ap.add_argument("--version", action="version", version=f"optout {__version__}")
    ap.add_argument("--identity", help=f"identity YAML (default {identity_path()})")
    ap.add_argument("--catalog", help="alternate registries.yaml")
    ap.add_argument("--person", "-p", action="append", help="limit to person id (repeatable)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write an identity template")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("identity", help="show the identity file (redacted)")
    s.set_defaults(fn=cmd_identity)

    def filters(p):
        p.add_argument("--category", "-c", action="append", choices=CATEGORIES)
        p.add_argument("--method", "-m", action="append", choices=("web", "email", "phone", "mail"))

    s = sub.add_parser("list", help="list registries and progress")
    filters(s)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="details for one registry")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("status", help="what is due or expiring")
    filters(s)
    s.add_argument("--days", type=int, default=60, help="renewal lookahead")
    s.add_argument("--all", action="store_true", help="show everything, not just due")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("run", help="work through due submissions (assisted)")
    filters(s)
    s.add_argument("ids", nargs="*", help="registry ids (default: all due)")
    s.add_argument("--redo", action="store_true", help="include ones already done")
    s.add_argument("--no-fill", action="store_true", help="open pages without autofilling")
    s.add_argument("--email-mode", choices=("auto", "mailto", "smtp", "eml"), default="auto")
    s.add_argument("--out", help="directory for letters / .eml drafts")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("mark", help="record a submission you did by hand")
    s.add_argument("id")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("letters", help="generate postal letters for mail-in registries")
    filters(s)
    s.add_argument("ids", nargs="*")
    s.add_argument("--out")
    s.add_argument("--print", action="store_true", help="print instead of writing files")
    s.set_defaults(fn=cmd_letters)

    s = sub.add_parser("email", help="print the opt-out email body for a registry")
    s.add_argument("id")
    s.set_defaults(fn=cmd_email)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except (FileNotFoundError, KeyError, ValueError) as e:
        con.print(f"[red]{e}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
