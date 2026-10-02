import gzip

import pytest

from lsd_mcp import models, run


def test_coordination_copy_compile_run_read(models_dir, lsd_root):
    models.copy_model("Literature/Coordination", "coor")
    models.set_run_settings("coor", "Single", steps=100)
    first = run.run_configuration("coor", "Single", seed=7)
    assert first["ok"], first
    assert first["result_files"] == ["Single_7.res.gz", "Single_7_7.tot.gz"]
    names = [item["series"] for item in first["first_run"]["series"]]
    assert "Mean 1" in names
    second_dir = models_dir / "coor"
    one = gzip.open(second_dir / "Single_7.res.gz", "rt").read()
    again = run.run_configuration("coor", "Single", seed=7)
    assert again["ok"]
    two = gzip.open(second_dir / "Single_7.res.gz", "rt").read()
    assert one == two
    assert run.run_configuration("coor", "Single", seed=8)["ok"]
    assert gzip.open(second_dir / "Single_8.res.gz", "rt").read() != one

    series = run.read_results("coor", "Single_7.res.gz", variables=["Mean"], max_points=10)
    assert len(series["steps"]) == 10
    assert series["steps"][0] == 0 and series["steps"][-1] == 100
    assert list(series["series"]) == ["Mean 1"]
    assert len(series["series"]["Mean 1"]) == 10
    window = run.read_results("coor", "Single_7.res.gz", variables=["Mean"], start=10, end=19)
    assert window["steps"] == list(range(10, 20))


def test_linear_set_values_then_run(linear, lsd_root):
    models.set_values("linear", "Linear", {"a": 1.25, "b": 2.0})
    result = run.run_configuration("linear", "Linear", seed=3)
    assert result["ok"], result
    expected = 2 * 1.25 - 3 * 2.0 + 7
    series = result["first_run"]["series"][0]
    assert series["series"] == "Z 1"
    assert series["last"] == pytest.approx(expected)


def test_run_reports_compile_error(linear, lsd_root):
    models.write_equations("linear", "this is not c++")
    result = run.run_configuration("linear", "Linear")
    assert result["ok"] is False and result["stage"] == "compile"
    assert result["errors"]


def test_read_results_rejects_paths(linear):
    with pytest.raises(models.ModelError):
        run.read_results("linear", "../x.res.gz")
