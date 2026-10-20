from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    env = os.environ.get("OPTOUT_HOME")
    base = Path(env).expanduser() if env else Path.home() / ".config" / "optout"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def identity_path() -> Path:
    return config_dir() / "identity.yaml"


def state_path() -> Path:
    return config_dir() / "state.json"


def letters_dir() -> Path:
    d = config_dir() / "letters"
    d.mkdir(exist_ok=True)
    return d


def browser_profile_dir() -> Path:
    d = config_dir() / "browser-profile"
    d.mkdir(exist_ok=True)
    return d
