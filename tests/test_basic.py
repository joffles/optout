from datetime import date, timedelta
from pathlib import Path

import pytest

from optout import catalog, identity, state as st
from optout.letters import email_body, letter_text
from optout.browser import _match_field, _pick_state_option

IDENT = """
people:
  - id: a
    first_name: Ann
    last_name: Lee
    state: CA
    addresses:
      - {line1: 1 Main St, city: Oakland, state: CA, zip: "94601"}
      - {line1: 2 Old Rd, city: Austin, state: TX, zip: "73301", current: false}
    emails: [ann@example.com, ann2@example.com]
    phones: ["(510) 555-0100", "510-555-0101", "5105550102", "5105550103"]
  - id: b
    first_name: Bob
    last_name: Lee
    state: TX
    addresses:
      - {line1: 1 Main St, city: Oakland, state: CA, zip: "94601"}
    emails: [bob@example.com]
    phones: ["5105550200"]
"""


@pytest.fixture
def people(tmp_path):
    p = tmp_path / "id.yaml"
    p.write_text(IDENT)
    return identity.load_people(p)


def test_catalog_loads_and_ids_unique():
    regs = catalog.load_registries()
    ids = [r.id for r in regs]
    assert len(ids) == len(set(ids)) and len(regs) > 20
    for r in regs:
        assert r.category in catalog.CATEGORIES and r.method in catalog.METHODS
        if r.method == "email":
            assert r.email_to
        if r.method == "web":
            assert r.url.startswith("https://")


def test_units_split_phones_by_three(people):
    regs = catalog.load_registries()
    dnc = catalog.find(regs, "national-dnc")
    units = catalog.units_for(dnc, people)
    a = [u for u in units if u.person.id == "a"]
    assert [len(u.phones) for u in a] == [3, 1]
    assert len([u for u in units if u.person.id == "b"]) == 1


def test_state_filter_and_per_address_email(people):
    regs = catalog.load_registries()
    assert len(catalog.units_for(catalog.find(regs, "ca-drop"), people)) == 1  # Ann only
    assert len(catalog.units_for(catalog.find(regs, "tx-no-call"), people)) == 1  # Bob only
    assert len(catalog.units_for(catalog.find(regs, "dmachoice"), people)) == 3  # 2 + 1 addresses
    assert len(catalog.units_for(catalog.find(regs, "dmachoice-emps"), people)) == 3


def test_state_roundtrip_and_expiry(tmp_path):
    s = st.State(tmp_path / "s.json")
    s.mark("k", "done", 10, when=date.today() - timedelta(days=11))
    assert st.State(tmp_path / "s.json").needs_action("k")
    s.mark("k", "done", 10)
    assert not s.needs_action("k")
    s.mark("k2", "skipped", None)
    assert not s.needs_action("k2")
    s.mark("k3", "done", None)
    assert not s.needs_action("k3") and s.get("k3").expires is None


def test_letters_and_email(people):
    regs = catalog.load_registries()
    u = catalog.units_for(catalog.find(regs, "optoutprescreen"), people)[0]
    txt = letter_text(u)
    assert "Ann Lee" in txt and "P.O. Box 530200" in txt and "1 Main St" in txt
    e = catalog.units_for(catalog.find(regs, "epsilon"), people)[0]
    assert "ann@example.com" in email_body(e)


def test_autofill_heuristics():
    assert _match_field("first_name | given name") == "first_name"
    assert _match_field("address2 | apt") == "line2"
    assert _match_field("street address | address1") == "line1"
    assert _match_field("username") is None
    assert _match_field("ssn") is None
    assert _match_field("confirm password") is None
    assert _match_field("name") == "full_name"
    assert _match_field("zipcode") == "zip"
    assert _pick_state_option([("", "Select"), ("CA", "California"), ("TX", "Texas")], "tx") == "TX"
    assert _pick_state_option([("5", "California"), ("44", "Texas")], "CA") == "5"
