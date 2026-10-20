"""Postal letter and email body generation."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .catalog import Unit
from .paths import letters_dir

EMAIL_TEMPLATE = """\
To whom it may concern,

Please remove me from all of your marketing, people-search and data-sharing
lists and suppress my records from any future sale, license or display.
This is a request under applicable consumer privacy law (including, where
it applies, the CCPA/CPRA right to opt out and delete).

Name:            {full_name}
Date of birth:   {date_of_birth}
Address:         {address}
Email(s):        {email}
Phone(s):        {phones}

Please confirm in writing when this has been completed. Do not use the
information in this message for any purpose other than processing this
opt-out.

Regards,
{full_name}
"""

LETTER_TEMPLATE = """\
{full_name}
{address_block}

{today}

{recipient}

Re: Opt-out / removal request

To whom it may concern,

Please remove the person named below from all marketing, prospecting and
data-sharing lists you maintain or supply to others, and suppress these
records permanently.

    Name:           {full_name}
    Date of birth:  {date_of_birth}
    Address:        {address}
    Email(s):       {email}
    Phone(s):       {phones}

Please send written confirmation to the address above.

Sincerely,


_______________________________
{full_name}
"""


def email_body(unit: Unit) -> str:
    v = unit.values()
    v.setdefault("address", "")
    v["date_of_birth"] = v.get("date_of_birth") or "(not provided)"
    return EMAIL_TEMPLATE.format(**v)


def email_subject(unit: Unit) -> str:
    return unit.registry.email_subject or f"Opt-out / removal request - {unit.person.full_name}"


def letter_text(unit: Unit) -> str:
    v = unit.values()
    a = unit.address or unit.person.current_address
    v["address_block"] = a.block() if a else ""
    v.setdefault("address", "")
    v["date_of_birth"] = v.get("date_of_birth") or "(not provided)"
    v["today"] = date.today().strftime("%B %d, %Y")
    v["recipient"] = (unit.registry.mail_address or unit.registry.name).strip()
    return LETTER_TEMPLATE.format(**v)


def write_letter(unit: Unit, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or letters_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{unit.registry.id}-{unit.person.id}-{date.today().isoformat()}.txt"
    path = out_dir / fname
    path.write_text(letter_text(unit))
    return path
