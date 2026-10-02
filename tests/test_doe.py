"""NOLH and elementary effects designs, made by LSD's own design code (lsd_doe).

The reference files in tests/data/doe_gui are what LSD's interface wrote for
the Linear model (Z = 2a - 3b + 7) from baseline/Linear.sa, which has five
factors: a, b, c, n and the initial value of Z.
"""

import csv
import json
import platform
import shutil
from pathlib import Path

import pytest

from lsd_mcp import models, sa

GUI = Path(__file__).parent / "data" / "doe_gui"
LINUX = platform.system() == "Linux"


@pytest.fixture
def baseline(models_dir, lsd_root):
    """The interface's starting point (Linear.lsd + five-factor Linear.sa) as a model."""
    target = models_dir / "base"
    shutil.copytree(GUI / "baseline", target)
    return target


def same_files(made: Path, reference: Path):
    """Every file LSD's interface wrote is in `made`, byte for byte."""
    files = sorted(path.name for path in reference.iterdir())
    assert files
    for name in files:
        assert (made / name).read_bytes() == (reference / name).read_bytes(), name


def test_nolh_is_byte_identical_to_the_interface(baseline):
    assert sa.run_doe(baseline, "Linear", "-m", "nolh") == {"points": 17, "first": 1, "last": 17}
    same_files(baseline, GUI / "nolh")


def test_appended_monte_carlo_set_is_byte_identical(baseline):
    sa.run_doe(baseline, "Linear", "-m", "nolh")
    assert sa.run_doe(baseline, "Linear", "-m", "mc", "-n", "10", "-i", "18") == \
        {"points": 10, "first": 18, "last": 27}
    same_files(baseline, GUI / "nolh_append")
    same_files(baseline, GUI / "nolh")  # the first 17 are untouched


def test_monte_carlo_range_is_byte_identical(baseline):
    sa.run_doe(baseline, "Linear", "-m", "mc", "-n", "10")
    same_files(baseline, GUI / "mc")


def unit_rows(path, lows, highs):
    """Design table rows scaled to [0, 1] per factor."""
    with open(path, newline="") as handle:
        rows = list(csv.reader(handle))[1:]
    return [[(float(cell) - lo) / (hi - lo) for cell, lo, hi in zip(row, lows, highs)] for row in rows]


def test_ee_has_the_structure_of_a_morris_design(baseline):
    made = sa.run_doe(baseline, "Linear", "-m", "ee")
    assert made == {"points": 60, "first": 1, "last": 60}  # 10 trajectories x (5 factors + 1)
    assert len(list(baseline.glob("Linear_*.lsd"))) == 60
    rows = unit_rows(baseline / "Linear_1_60.csv", [0, 2, -1, 1, 0], [1, 3, 1, 9, 10])
    for start in range(0, 60, 6):
        factors_moved = set()
        for step in range(5):
            before, after = rows[start + step], rows[start + step + 1]
            changed = [i for i in range(5) if abs(after[i] - before[i]) > 1e-5]
            assert len(changed) == 1, (start, step)
            i = changed[0]
            factors_moved.add(i)
            if i != 3:  # n is rounded to an integer, so its step is not exactly the jump
                assert abs(after[i] - before[i]) == pytest.approx(2 / 3, abs=1e-5)
        assert factors_moved == {0, 1, 2, 3, 4}  # each factor once per trajectory
    for row in rows:
        for i in (0, 1, 2, 4):
            level = row[i] * 3
            assert abs(level - round(level)) < 1e-4  # four levels


def test_unusable_settings_are_refused_by_the_program(baseline):
    for options in (["-m", "ee", "-l", "3"], ["-m", "ee", "-t", "1"], ["-m", "ee", "-t", "20", "-p", "10"],
                    ["-m", "ee", "-j", "0"], ["-m", "mc"], ["-m", "other"]):
        with pytest.raises(models.ModelError):
            sa.run_doe(baseline, "Linear", *options)
    assert not list(baseline.glob("Linear_*"))


# --- through sa.create_design --------------------------------------------------

FACTORS = {"a": [0, 1], "b": [2, 3], "c": [-1, 1]}


def test_nolh_design_files_sidecar_and_seeds(linear, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    info = sa.create_design("linear", "Linear", FACTORS, method="nolh", validation_samples=5,
                            runs_per_point=3, seed=10, samples=99)
    assert (info["points"], info["validation_points"], info["method"]) == (17, 5, "nolh")
    assert info["design_table"] == "Linear_1_17.csv" and info["validation_table"] == "Linear_18_22.csv"
    assert "samples is not used" in info["note"]
    assert json.loads((linear / "Linear_design.json").read_text()) == {
        "method": "nolh", "extended": False, "points": 17, "validation_points": 5,
        "runs_per_point": 3, "seed": 10}
    assert (linear / "Linear.sa").read_text().splitlines() == [
        "a 0 2 f: 0 1", "b 0 2 f: 2 3", "c 0 2 f: -1 1"]
    for k in range(1, 23):
        parsed = sa.lsdfile.parse(linear / ("Linear_%d.lsd" % k))
        assert parsed.settings["SIM_NUM"] == "3"
        assert parsed.settings["SEED"] == str(10 + (k - 1) * 3)
        assert parsed.settings["EQUATION"] == "fun_Linear.cpp"
    rows = unit_rows(linear / "Linear_1_17.csv", [0, 2, -1], [1, 3, 1])
    for i in range(3):  # a near-orthogonal Latin hypercube: 17 distinct levels per factor
        assert len(set(round(row[i], 4) for row in rows)) == 17
    assert info["configurations"] == "Linear_1.lsd ... Linear_22.lsd"


def test_nolh_extended_uses_the_larger_table(linear, lsd_root):
    info = sa.create_design("linear", "Linear", FACTORS, method="nolh", extended=True,
                            validation_samples=2)
    assert info["points"] == 33
    assert json.loads((linear / "Linear_design.json").read_text())["extended"] is True


def test_ee_design_files_and_sidecar(linear, lsd_root):
    info = sa.create_design("linear", "Linear", FACTORS, method="ee", trajectories=4, pool=20,
                            levels=6, jump=3, runs_per_point=2, seed=7, validation_samples=9)
    assert (info["points"], info["validation_points"]) == (16, 0)
    assert "validation_samples is ignored" in info["note"]
    assert "validation_table" not in info and info["design_table"] == "Linear_1_16.csv"
    assert json.loads((linear / "Linear_design.json").read_text()) == {
        "method": "ee", "levels": 6, "jump": 3, "trajectories": 4, "pool": 20, "points": 16,
        "validation_points": 0, "runs_per_point": 2, "seed": 7}
    assert sorted(path.name for path in linear.glob("Linear_*.csv")) == ["Linear_1_16.csv"]
    assert not (linear / "Linear_17.lsd").exists()
    for k in range(1, 17):
        assert sa.lsdfile.parse(linear / ("Linear_%d.lsd" % k)).settings["SEED"] == str(7 + (k - 1) * 2)
    rows = unit_rows(linear / "Linear_1_16.csv", [0, 2, -1], [1, 3, 1])
    for start in range(0, 16, 4):
        for step in range(3):
            moved = [abs(rows[start + step + 1][i] - rows[start + step][i]) for i in range(3)]
            assert sorted(round(m, 4) for m in moved) == [0, 0, 0.6]  # jump 3 of 5 intervals
    for row in rows:
        for value in row:
            assert abs(value * 5 - round(value * 5)) < 1e-4  # six levels


def test_designs_are_reproducible_per_seed(linear, lsd_root):
    options = dict(method="ee", trajectories=3, pool=30)
    sa.create_design("linear", "Linear", FACTORS, seed=1, **options)
    first = (linear / "Linear_1_12.csv").read_text()
    sa.create_design("linear", "Linear", FACTORS, seed=1, overwrite=True, **options)
    assert (linear / "Linear_1_12.csv").read_text() == first
    sa.create_design("linear", "Linear", FACTORS, seed=2, overwrite=True, **options)
    assert (linear / "Linear_1_12.csv").read_text() != first


def test_refuses_overwrite_and_overwrite_removes_the_old_design(linear, lsd_root):
    sa.create_design("linear", "Linear", FACTORS, method="nolh")
    with pytest.raises(models.ModelError):
        sa.create_design("linear", "Linear", FACTORS, method="ee")
    assert (linear / "Linear_design.json").is_file()
    sa.create_design("linear", "Linear", FACTORS, method="ee", trajectories=2, pool=5, overwrite=True)
    assert sorted(path.name for path in linear.glob("Linear_*.csv")) == ["Linear_1_8.csv"]
    assert not (linear / "Linear_9.lsd").exists()
    assert json.loads((linear / "Linear_design.json").read_text())["method"] == "ee"
    sa.create_design("linear", "Linear", FACTORS, samples=6, validation_samples=2, overwrite=True)
    assert json.loads((linear / "Linear_design.json").read_text())["method"] == "lhs"


def test_copy_model_skips_the_design_file(linear, lsd_root, models_dir):
    sa.create_design("linear", "Linear", FACTORS, method="nolh")
    info = models.copy_model("linear", "copy", source_group="models")
    assert "Linear_design.json" not in info["files"] and "Linear.sa" in info["files"]
    assert not (models_dir / "copy" / "Linear_design.json").exists()


def test_argument_checks(linear, lsd_root):
    for options in (dict(method="lhs"), dict(method="random", samples=1), dict(method="other", samples=5),
                    dict(method="ee", levels=3), dict(method="ee", trajectories=1),
                    dict(method="ee", trajectories=20, pool=10), dict(method="ee", jump=0),
                    dict(method="nolh", validation_samples=0), dict(method="nolh", runs_per_point=1)):
        with pytest.raises(models.ModelError):
            sa.create_design("linear", "Linear", FACTORS, **options)
    assert not list(linear.glob("Linear_*")) and not (linear / "Linear.sa").exists()


def test_nolh_with_too_many_factors_fails_clearly(linear, lsd_root):
    # the Linear model has four parameters at most; ask for a table it cannot fill
    with pytest.raises(models.ModelError):
        sa.run_doe(linear, "Linear", "-m", "nolh")  # no Linear.sa at all
    (linear / "Linear.sa").write_text("a 0 2 f: 0 1\nb 0 2 f: 2 3\n")
    assert sa.run_doe(linear, "Linear", "-m", "nolh")["points"] == 17


def test_design_from_the_interface_sa_file(baseline):
    models.set_saved("base", "Linear", ["Z"])
    info = sa.design_from_sa("base", "Linear", "nolh", validation_samples=10, runs_per_point=2)
    assert (info["points"], info["validation_points"]) == (17, 10)
    info = sa.design_from_sa("base", "Linear", "ee", overwrite=True)
    assert info["points"] == 60 and info["levels"] == 4 and info["jump"] == 2


@pytest.mark.skipif(not LINUX, reason="LSD's shuffle depends on the C++ library; see the Docker test")
def test_ee_is_byte_identical_to_the_interface_on_linux(baseline):
    sa.run_doe(baseline, "Linear", "-m", "ee")
    same_files(baseline, GUI / "ee")


# --- sa_analyze checks that need no R --------------------------------------------

def test_analyze_refuses_the_wrong_method(linear, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    sa.create_design("linear", "Linear", FACTORS, method="ee", trajectories=3, pool=9)
    for metamodel in ("kriging", "polynomial"):
        with pytest.raises(models.ModelError) as err:
            sa.analyze("linear", "Linear", "Z", metamodel=metamodel)
        assert "elementary effects design" in str(err.value)
    with pytest.raises(models.ModelError) as err:
        sa.analyze("linear", "Linear", "Z", metamodel="ee", levels=6)
    assert "does not match the design" in str(err.value)
    result = sa.analyze("linear", "Linear", "Z")  # chooses ee, finds no results
    assert result["ok"] is False and "sa_run_design" in result["message"]

    sa.create_design("linear", "Linear", FACTORS, samples=6, validation_samples=2, overwrite=True)
    with pytest.raises(models.ModelError) as err:
        sa.analyze("linear", "Linear", "Z", metamodel="ee")
    assert "method 'lhs'" in str(err.value)
    with pytest.raises(models.ModelError) as err:
        sa.analyze("linear", "Linear", "Z", levels=4, jump=2)
    assert "only used with metamodel='ee'" in str(err.value)


def test_analyze_ee_without_a_design_file_needs_levels_and_jump(baseline):
    models.set_saved("base", "Linear", ["Z"])
    sa.run_doe(baseline, "Linear", "-m", "ee")
    with pytest.raises(models.ModelError) as err:
        sa.analyze("base", "Linear", "Z", metamodel="ee")
    assert "pass levels and jump" in str(err.value)
    with pytest.raises(models.ModelError) as err:
        sa.analyze("base", "Linear", "Z", metamodel="kriging")
    assert "no out-of-sample table" in str(err.value)
    result = sa.analyze("base", "Linear", "Z", metamodel="ee", levels=4, jump=2)
    assert result["ok"] is False and "sa_run_design" in result["message"]


def test_analyze_ee_with_a_meta_model_design_without_file_is_refused(baseline):
    models.set_saved("base", "Linear", ["Z"])
    sa.run_doe(baseline, "Linear", "-m", "nolh")
    sa.run_doe(baseline, "Linear", "-m", "mc", "-n", "10", "-i", "18")
    with pytest.raises(models.ModelError) as err:
        sa.analyze("base", "Linear", "Z", metamodel="ee", levels=4, jump=2)
    assert "meta-model design" in str(err.value)


def test_too_few_runs_per_point_is_explained():
    from lsd_mcp import sa
    result = sa._r_failure("R analysis failed: Not enough data files (baseName_XX_YY.res[.gz]) found")
    assert result["ok"] is False
    assert "two runs per design point" in result["message"]
    assert "Not enough data files" in result["r_message"]
