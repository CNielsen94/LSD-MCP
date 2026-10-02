"""Settings read from the environment each time they are asked for."""

import os
from pathlib import Path

DEFAULT_TAG = "8.1-stable-5"


def lsd_tag() -> str:
    return os.environ.get("LSD_TAG") or DEFAULT_TAG


def home() -> Path:
    """Cache folder: fetched LSD source and all build output."""
    value = os.environ.get("LSD_MCP_HOME")
    if value:
        return Path(value).expanduser().resolve()
    return (Path.home() / ".cache" / "lsd-mcp").resolve()


def models_dir() -> Path:
    """The user's models folder, created on first use."""
    value = os.environ.get("LSD_MODELS")
    if value:
        path = Path(value).expanduser()
    else:
        path = Path.home() / "lsd-models"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def rscript() -> str:
    return os.environ.get("LSD_MCP_RSCRIPT") or "Rscript"
