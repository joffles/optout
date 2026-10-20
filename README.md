# optout

A local, AI-free Python CLI that takes your phone numbers, email addresses and
postal addresses and works through every do-not-call, do-not-mail,
pre-approved-credit and data-broker opt-out there is, then tracks when each
one needs renewing.

It costs no tokens to run. The registry catalog is a static YAML file, the
form filling is heuristic, and your data never leaves the machine except in
the forms you submit.

## Honest scope

Almost none of these sites offer an API, and most (donotcall.gov,
OptOutPrescreen, DMAchoice, every people-search broker) use CAPTCHAs plus
email or phone confirmation and forbid automated submission. So `optout` is
an **assisted** runner, not a fire-and-forget bot:

- it knows all the registries, which ones apply to you (state of residence),
  and how many submissions each needs (per person, per phone, per address);
- for web forms it drives **your own installed Chrome** through Playwright
  with a persistent profile, so you look like a normal returning user
  (real cookies, real user agent, no `navigator.webdriver`, human-paced
  typing), autofills the fields it recognises, and then hands the page to
  you to solve the CAPTCHA, check the values and click submit;
- for email opt-outs it drafts the message in your mail client (or sends via
  SMTP, or writes `.eml` files);
- for mail-in registries it generates printable letters;
- it records done / pending / skipped per submission and tells you what is
  due or about to expire.

Expect the first full pass to take an hour or two of clicking. After that,
`optout status` tells you what to redo and when.

## Install

```bash
cd ~/code/optout
uv sync --all-extras
uv run playwright install chromium   # only needed if you lack Google Chrome
```

Or `uv tool install --with playwright .` for a global `optout` command.

## Use

```bash
optout init                 # writes ~/.config/optout/identity.yaml (mode 600)
$EDITOR ~/.config/optout/identity.yaml
optout identity             # redacted check of what was parsed
optout list                 # every registry + your progress
optout show optoutprescreen # details, gotchas, per-person status
optout run                  # work through everything that is due
optout run -c phone         # just the do-not-call lists
optout run national-dnc dmachoice
optout status               # what is due or expiring in 60 days
optout mark whitepages done --date 2026-09-01   # record something you did by hand
optout letters              # print-and-sign letters for mail-in registries
optout email epsilon        # show the email body that will be sent
```

Inside `optout run`, after each page opens: `d` done, `s` skip, `f` re-run
autofill (for multi-step forms), `x` failed, `p` leave pending.

Email sending: by default it opens a `mailto:` draft. Set
`OPTOUT_SMTP_HOST/PORT/USER/PASS/FROM` to send directly, or pass
`--email-mode eml` to write drafts to disk.

## What is in the catalog

| Category | Registries |
|---|---|
| phone | National Do Not Call Registry; state lists for TX, PA, IN, CO, MO, LA (shown only if you live there) |
| mail | DMAchoice, Catalog Choice, Valpak, Save/Vericast (RedPlum), Yellow Pages opt-out |
| email | DMAchoice eMPS |
| credit offers | OptOutPrescreen (5 years online; the mailed form is permanent) |
| data brokers | California DROP (CA residents: one request to 500+ brokers), Acxiom, Epsilon, Experian Marketing, LexisNexis, PeopleConnect (Intelius / TruthFinder / Instant Checkmate / US Search), Whitepages, Spokeo, BeenVerified, Radaris, MyLife, FastPeopleSearch, TruePeopleSearch, PeopleFinders, ClustrMaps, Nuwber, ThatsThem, USPhoneBook |

Edit `src/optout/data/registries.yaml` (or pass `--catalog your.yaml`) to add
or fix entries. Sites move; the `notes` field records the gotchas as of
October 2026.

## Things it will not do

- Store or type your Social Security number. OptOutPrescreen asks for it
  optionally; leave it blank or type it yourself.
- Solve CAPTCHAs or click the final submit button for you.
- Stop scammers. Registries only bind legitimate marketers; pair this with
  your carrier's call-blocking and your mail client's spam filter.

## Privacy

`~/.config/optout/` holds `identity.yaml`, `state.json`, generated letters
and the Chrome profile, all mode 600/700 and gitignored. Delete the directory
to forget everything.

## Dev

```bash
uv run pytest
```
