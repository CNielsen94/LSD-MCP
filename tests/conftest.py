import shutil
from pathlib import Path

import pytest

from lsd_mcp import build, lsdsource, sa

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def lsd_root():
    """One fetched LSD source (or $LSDROOT) shared by all tests."""
    return lsdsource.lsd_root()


@pytest.fixture(scope="session")
def utilities(lsd_root):
    return build.utilities(lsd_root)


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    folder = tmp_path / "models"
    monkeypatch.setenv("LSD_MODELS", str(folder))
    folder.mkdir()
    return folder


@pytest.fixture
def linear(models_dir, lsd_root):
    """The Linear test model (Z = 2a - 3b + 7) copied into the models folder."""
    target = models_dir / "linear"
    shutil.copytree(DATA / "linear", target)
    return target


needs_r = pytest.mark.skipif(
    not (sa.rscript_status()["rscript"] and sa.rscript_status()["lsdsensitivity"]),
    reason="Rscript with LSDsensitivity is not available")
