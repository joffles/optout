"""Registry catalog loader and applicability logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from .identity import Address, Person, format_phone

CATEGORIES = ("phone", "mail", "email", "credit-offers", "data-broker")
METHODS = ("web", "email", "phone", "mail")


@dataclass
class Registry:
    id: str
    name: str
    category: str
    method: str
    url: str = ""
    phone: str = ""
    email_to: str = ""
    email_subject: str = ""
    mail_address: str = ""
    per: str = "person"
    max_per_submission: int = 1
    fields: list[str] = field(default_factory=list)
    renew_days: int | None = None
    cost: str = "Free"
    states: list[str] | None = None
    verify_url: str = ""
    automation: str = "assisted"
    notes: str = ""

    def applies_to(self, person: Person) -> bool:
        if not self.states:
            return True
        return person.residence_state in {s.upper() for s in self.states}


@dataclass
class Unit:
    """One concrete submission: a registry x person x (phone|email|address)."""

    registry: Registry
    person: Person
    phones: list[str] = field(default_factory=list)
    email: str = ""
    address: Address | None = None

    @property
    def key(self) -> str:
        r = self.registry
        if r.per == "phone":
            tail = "+".join(sorted(p for p in map(_digits, self.phones)))
        elif r.per == "email":
            tail = self.email.lower()
        elif r.per == "address":
            tail = self.address.key if self.address else "no-address"
        else:
            tail = "person"
        return f"{r.id}::{self.person.id}::{tail}"

    def describe(self) -> str:
        r = self.registry
        if r.per == "phone":
            return ", ".join(format_phone(p) for p in self.phones)
        if r.per == "email":
            return self.email
        if r.per == "address":
            return self.address.oneline() if self.address else "(no address)"
        return self.person.full_name

    def values(self) -> dict[str, str]:
        """Flat field map used by autofill, email bodies and letters."""
        p = self.person
        a = self.address or p.current_address
        v = {
            "first_name": p.first_name,
            "middle_name": p.middle_name,
            "last_name": p.last_name,
            "full_name": p.full_name,
            "date_of_birth": p.date_of_birth,
            "email": self.email or p.primary_email,
            "phone": format_phone(self.phones[0]) if self.phones else format_phone(p.primary_phone),
            "phones": ", ".join(format_phone(x) for x in (self.phones or p.phones)),
        }
        if a:
            v.update(
                {
                    "line1": a.line1,
                    "line2": a.line2,
                    "city": a.city,
                    "state": a.state,
                    "state_name": a.state_name,
                    "zip": a.zip,
                    "address": a.oneline(),
                }
            )
        return v


def _digits(p: str) -> str:
    return "".join(ch for ch in p if ch.isdigit())


def default_catalog_path() -> Path:
    import os

    env = os.environ.get("OPTOUT_CATALOG")
    if env:
        return Path(env).expanduser()
    return Path(str(resources.files("optout").joinpath("data/registries.yaml")))


def load_registries(path: Path | None = None) -> list[Registry]:
    path = path or default_catalog_path()
    raw = yaml.safe_load(path.read_text()) or []
    regs: list[Registry] = []
    for r in raw:
        r = dict(r)
        r.setdefault("fields", [])
        regs.append(Registry(**r))
    return regs


def find(regs: list[Registry], ident: str) -> Registry:
    for r in regs:
        if r.id == ident:
            return r
    matches = [r for r in regs if ident.lower() in r.id or ident.lower() in r.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise KeyError(f"'{ident}' is ambiguous: " + ", ".join(m.id for m in matches))
    raise KeyError(f"No registry '{ident}'. Try `optout list`.")


def units_for(reg: Registry, people: list[Person]) -> list[Unit]:
    units: list[Unit] = []
    for p in people:
        if not reg.applies_to(p):
            continue
        if reg.per == "phone":
            phones = list(dict.fromkeys(_digits(x) for x in p.phones if _digits(x)))
            n = max(1, reg.max_per_submission)
            for i in range(0, len(phones), n):
                units.append(Unit(reg, p, phones=phones[i : i + n], email=p.primary_email, address=p.current_address))
        elif reg.per == "email":
            for e in dict.fromkeys(p.emails):
                units.append(Unit(reg, p, email=e, address=p.current_address))
        elif reg.per == "address":
            for a in p.addresses:
                units.append(Unit(reg, p, address=a, email=p.primary_email, phones=p.phones[:1]))
        else:
            units.append(Unit(reg, p, phones=p.phones, email=p.primary_email, address=p.current_address))
    return units
