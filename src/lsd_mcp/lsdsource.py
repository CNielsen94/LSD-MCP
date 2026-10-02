"""Locate the LSD source tree, fetching the release tag if needed."""

import os
import shutil
import threading
from pathlib import Path

from . import config
from .runner import run

_lock = threading.Lock()


class LsdSourceError(Exception):
    pass


def uses_fetched_source() -> bool:
    return not os.environ.get("LSDROOT")


def lsd_root() -> Path:
    value = os.environ.get("LSDROOT")
    if value:
        root = Path(value).expanduser().resolve()
        if not (root / "src").is_dir():
            raise LsdSourceError("LSDROOT=%s has no src/ folder" % root)
        return root
    return fetch(config.lsd_tag())


def fetch(tag: str) -> Path:
    target = config.home() / ("Lsd-" + tag)
    with _lock:
        if (target / "src" / "lsdmain.cpp").is_file():
            return target
        config.home().mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)  # leftover of an interrupted fetch
        url = "https://github.com/marcov64/Lsd"
        # Sparse clone: the whole repository is 260 MB, we need three folders.
        steps = [
            ["git", "clone", "-q", "--depth", "1", "--filter=blob:none",
             "--no-checkout", "--branch", tag, url, target],
            ["git", "-C", target, "sparse-checkout", "set", "--no-cone",
             "/src/", "/Example/", "/Rpkg/"],
            ["git", "-C", target, "checkout", "-q"],
        ]
        for step in steps:
            result = run(step, timeout=900)
            if not result.ok:
                raise LsdSourceError(
                    "fetching LSD %s failed: %s" % (tag, result.output[-500:]))
    return target
