"""Identity file: the people, addresses, emails and phones to opt out.

Stored at ~/.config/optout/identity.yaml with mode 600. This is personal
data; the file is gitignored and never leaves the machine except in the
forms you submit.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import identity_path

TEMPLATE = """\
# optout identity file. Personal data: keep it private (mode 600).
# Add as many people as live at your addresses; most registries are per person.
people:
  - id: me                      # short handle used on the command line
    first_name: Jane
    middle_name: ""
    last_name: Doe
    date_of_birth: ""           # YYYY-MM-DD, optional; some forms ask
    state: CA                   # state of residence (enables state registries)
    addresses:
      - label: home
        line1: 123 Main St
        line2: ""
        city: Springfield
        state: CA
        zip: "90210"
        current: true
      # - label: previous
      #   line1: 9 Old Rd
      #   city: Elsewhere
      #   state: TX
      #   zip: "73301"
      #   current: false
    emails:
      - jane@example.com
    phones:
      - "555-555-0100"
"""

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


@dataclass
class Address:
    line1: str
    city: str
    state: str
    zip: str
    line2: str = ""
    label: str = "home"
    current: bool = True

    @property
    def key(self) -> str:
        return normalize_key(f"{self.line1} {self.line2} {self.zip}")

    @property
    def state_name(self) -> str:
        return US_STATES.get(self.state.upper(), self.state)

    def oneline(self) -> str:
        l2 = f", {self.line2}" if self.line2 else ""
        return f"{self.line1}{l2}, {self.city}, {self.state} {self.zip}"

    def block(self) -> str:
        lines = [self.line1]
        if self.line2:
            lines.append(self.line2)
        lines.append(f"{self.city}, {self.state} {self.zip}")
        return "\n".join(lines)


@dataclass
class Person:
    id: str
    first_name: str
    last_name: str
    middle_name: str = ""
    date_of_birth: str = ""
    state: str = ""
    addresses: list[Address] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def current_address(self) -> Address | None:
        for a in self.addresses:
            if a.current:
                return a
        return self.addresses[0] if self.addresses else None

    @property
    def primary_email(self) -> str:
        return self.emails[0] if self.emails else ""

    @property
    def primary_phone(self) -> str:
        return self.phones[0] if self.phones else ""

    @property
    def residence_state(self) -> str:
        if self.state:
            return self.state.upper()
        a = self.current_address
        return a.state.upper() if a else ""


def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9@.]+", "-", s.lower()).strip("-")


def normalize_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def format_phone(p: str) -> str:
    d = normalize_phone(p)
    if len(d) == 10:
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return p


def write_template(path: Path | None = None, force: bool = False) -> Path:
    path = path or identity_path()
    if path.exists() and not force:
        raise FileExistsError(path)
    path.write_text(TEMPLATE)
    os.chmod(path, 0o600)
    return path


def load_people(path: Path | None = None) -> list[Person]:
    path = path or identity_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No identity file at {path}. Run `optout init` and fill it in."
        )
    raw = yaml.safe_load(path.read_text()) or {}
    people: list[Person] = []
    for p in raw.get("people", []):
        addrs = [
            Address(
                line1=str(a.get("line1", "")),
                line2=str(a.get("line2", "") or ""),
                city=str(a.get("city", "")),
                state=str(a.get("state", "")).upper(),
                zip=str(a.get("zip", "")),
                label=str(a.get("label", "home")),
                current=bool(a.get("current", True)),
            )
            for a in p.get("addresses", []) or []
        ]
        people.append(
            Person(
                id=str(p.get("id") or normalize_key(p.get("first_name", "") + p.get("last_name", ""))),
                first_name=str(p.get("first_name", "")),
                middle_name=str(p.get("middle_name", "") or ""),
                last_name=str(p.get("last_name", "")),
                date_of_birth=str(p.get("date_of_birth", "") or ""),
                state=str(p.get("state", "") or "").upper(),
                addresses=addrs,
                emails=[str(e) for e in p.get("emails", []) or []],
                phones=[str(x) for x in p.get("phones", []) or []],
            )
        )
    if not people:
        raise ValueError(f"{path} has no people defined.")
    return people


def redact(s: str, keep: int = 2) -> str:
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "*" * (len(s) - keep * 2) + s[-keep:]
