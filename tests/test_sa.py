import csv
import re

import pytest

from conftest import needs_r
from lsd_mcp import lsdfile, models, sa

FACTORS = {"a": [0, 1], "b": [2, 3], "c": [-1, 1]}


def make_design(linear, **options):
    models.set_saved("linear", "Linear", ["Z"])
    args = dict(samples=20, validation_samples=5, runs_per_point=2, seed=10)
    args.update(options)
    return sa.create_design("linear", "Linear", FACTORS, **args)


def read_rows(path):
    with open(path, newline="") as handle:
        return list(csv.reader(handle))


def test_create_design_files(linear, lsd_root):
    info = make_design(linear)
    assert info["design_table"] == "Linear_1_20.csv"
    assert info["validation_table"] == "Linear_21_25.csv"
    assert (linear / "Linear.sa").read_text().splitlines() == [
        "a 0 2 f: 0 1", "b 0 2 f: 2 3", "c 0 2 f: -1 1"]
    design = read_rows(linear / "Linear_1_20.csv")
    valid = read_rows(linear / "Linear_21_25.csv")
    assert design[0] == ["a", "b", "c"] == valid[0]
    assert len(design) == 21 and len(valid) == 6
    for row in design[1:] + valid[1:]:
        assert all(re.fullmatch(r"-?\d+\.\d{6}", cell) for cell in row)
    for row in design[1:]:
        a, b, c = map(float, row)
        assert 0 <= a <= 1 and 2 <= b <= 3 and -1 <= c <= 1
    for k in range(1, 26):
        parsed = lsdfile.parse(linear / ("Linear_%d.lsd" % k))
        assert parsed.settings["SIM_NUM"] == "2"
        assert parsed.settings["SEED"] == str(10 + (k - 1) * 2)
        assert parsed.settings["EQUATION"] == "fun_Linear.cpp"
        table = design[k] if k <= 20 else valid[k - 20]
        assert parsed.element("a").values == [pytest.approx(float(table[0]))]
        assert parsed.element("c").values == [pytest.approx(float(table[2]))]


def test_latin_hypercube_covers_every_stratum(linear, lsd_root):
    make_design(linear)
    column = [float(row[0]) for row in read_rows(linear / "Linear_1_20.csv")[1:]]
    strata = sorted(int(value * 20 - 1e-9) if value < 1 else 19 for value in column)
    assert strata == list(range(20))


def test_design_is_reproducible_and_refuses_overwrite(linear, lsd_root):
    make_design(linear)
    first = (linear / "Linear_1_20.csv").read_text()
    with pytest.raises(models.ModelError):
        make_design(linear)
    make_design(linear, overwrite=True)
    assert (linear / "Linear_1_20.csv").read_text() == first
    make_design(linear, overwrite=True, seed=11)
    assert (linear / "Linear_1_20.csv").read_text() != first


def test_integer_factor_and_validation(linear, lsd_root):
    with pytest.raises(models.ModelError):
        sa.create_design("linear", "Linear", {"a": [0, 1]}, samples=5, runs_per_point=1)
    with pytest.raises(models.ModelError):
        sa.create_design("linear", "Linear", {"Z": [0, 1]}, samples=5)
    sa.create_design("linear", "Linear", {"n": [1, 9, "int"], "a": [0, 1]}, samples=8,
                     validation_samples=3, method="random")
    assert "n 0 2 i: 1 9" in (linear / "Linear.sa").read_text()
    for row in read_rows(linear / "Linear_1_8.csv")[1:]:
        assert float(row[0]) == int(float(row[0]))


def test_run_design_and_result_files(linear, lsd_root):
    make_design(linear, samples=6, validation_samples=3, runs_per_point=2, seed=1)
    outcome = sa.run_design("linear", "Linear", threads=4)
    assert outcome["ok"], outcome
    assert (outcome["points"], outcome["ran"], outcome["already_done"]) == (9, 9, 0)
    results = sorted(path.name for path in linear.glob("Linear_*_*.res.gz"))
    assert len(results) == 18
    assert "Linear_1_1.res.gz" in results and "Linear_9_18.res.gz" in results
    again = sa.run_design("linear", "Linear")
    assert (again["ran"], again["already_done"]) == (0, 9)
    # point 1 result equals Z = 2a - 3b + 7 for its parameters
    first = read_rows(linear / "Linear_1_6.csv")[1]
    a, b = float(first[0]), float(first[1])
    import gzip
    lines = gzip.open(linear / "Linear_1_1.res.gz", "rt").read().splitlines()
    assert float(lines[-1]) == pytest.approx(2 * a - 3 * b + 7, abs=1e-4)


def test_analyze_without_results_says_so(linear, lsd_root):
    make_design(linear, samples=4, validation_samples=2)
    result = sa.analyze("linear", "Linear", "Z")
    assert result["ok"] is False and "sa_run_design" in result["message"]


def test_analyze_message_when_r_missing(linear, lsd_root, monkeypatch):
    make_design(linear, samples=4, validation_samples=2)
    sa.run_design("linear", "Linear")
    monkeypatch.setenv("LSD_MCP_RSCRIPT", "no-such-rscript")
    result = sa.analyze("linear", "Linear", "Z")
    assert result["ok"] is False and "Rscript not found" in result["message"]


@needs_r
@pytest.mark.parametrize("metamodel", ["kriging", "polynomial"])
def test_analyze_sobol_effects(linear, lsd_root, metamodel):
    make_design(linear, samples=30, validation_samples=10, seed=1)
    assert sa.run_design("linear", "Linear")["ok"]
    result = sa.analyze("linear", "Linear", "Z", metamodel=metamodel)
    assert result["ok"], result
    direct = {row["factor"]: row["direct"] for row in result["sobol"]}
    assert direct["a"] == pytest.approx(4 / 13, abs=0.03)
    assert direct["b"] == pytest.approx(9 / 13, abs=0.03)
    assert abs(direct["c"]) < 0.03
