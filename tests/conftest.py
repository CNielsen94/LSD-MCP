import shutil
from pathlib import Path

import pytest

from lsd_mcp import build, lsdsource, models, sa

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


@pytest.fixture
def fake_r(linear, lsd_root, monkeypatch):
    models.set_saved("linear", "Linear", ["Z"])
    make = sa.create_design("linear", "Linear", {"a": [0, 1], "b": [2, 3]},
                            samples=4, validation_samples=2)
    assert sa.run_design("linear", "Linear")["ok"]
    monkeypatch.setattr(sa, "rscript_status", lambda: {"rscript": True, "lsdsensitivity": True, "message": ""})
    behaviour = {"q2": 0.9, "fail": False,
                 "sobol": "factor,direct,interactions\na,0.4,0.1\nb,0.5,0.0\n"}

    def fake_run(command, cwd=None, timeout=None, **kwargs):
        scratch = Path(command[-2])
        if behaviour["fail"]:
            (scratch / "error.txt").write_text("R analysis failed: boom")
            return run_result(1)
        (scratch / "fit.csv").write_text("metric,value\nQ2,%s\n" % behaviour["q2"])
        (scratch / "sobol.csv").write_text(behaviour["sobol"])
        return run_result(0)

    monkeypatch.setattr(sa, "run", fake_run)
    return behaviour


def run_result(code):
    from lsd_mcp.runner import Result
    return Result(code, "")
