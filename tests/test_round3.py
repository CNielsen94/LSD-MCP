"""Result reading (step labels, header forms, thinning) and smaller fixes."""

import gzip

import pytest

from lsd_mcp import models, run, sa

SYNTHETIC = (
    "E 1 (1 4)\t_s 1_1 (2 4)\t_s 1_2 (3 4)\tInnoShock R (1 4)\t\n"
    "NA\tNA\tNA\tNA\t\n"
    "1\tNA\tNA\t10\t\n"
    "2\t5\tNA\t20\t\n"
    "3\t6\t7\t30\t\n"
    "4\t8\t9\t40\t\n")


@pytest.fixture
def synthetic(linear):
    with gzip.open(linear / "Linear_9.res.gz", "wt") as handle:
        handle.write(SYNTHETIC)
    return linear


def test_row_number_is_the_time_step(synthetic):
    out = run.read_results("linear", "Linear_9.res.gz", variables=["E"])
    assert out["steps"] == [0, 1, 2, 3, 4]
    assert out["series"]["E 1"] == [None, 1, 2, 3, 4]
    window = run.read_results("linear", "Linear_9.res.gz", variables=["E"], start=2, end=3)
    assert window["steps"] == [2, 3] and window["series"]["E 1"] == [2, 3]


def test_summary_ignores_na_and_out_of_range_cells(synthetic):
    info = run.summarise(synthetic / "Linear_9.res.gz")
    assert info["steps"] == 5
    by_name = {item["series"]: item for item in info["series"]}
    assert by_name["E 1"]["last"] == 4 and by_name["E 1"]["mean"] == 2.5
    assert by_name["_s 1_1"]["mean"] == pytest.approx((5 + 6 + 8) / 3)


def test_instance_can_be_a_path_or_a_letter(synthetic):
    for wanted, labels in ((["_s"], ["_s 1_1", "_s 1_2"]), (["InnoShock"], ["InnoShock R"]),
                           (["_s 1_1"], ["_s 1_1"])):
        out = run.read_results("linear", "Linear_9.res.gz", variables=wanted)
        assert list(out["series"]) == labels


def test_thinning_limits_and_ranges(synthetic):
    last = run.read_results("linear", "Linear_9.res.gz", variables=["E"], max_points=1)
    assert last["steps"] == [4]
    for bad in (0, -2):
        with pytest.raises(models.ModelError):
            run.read_results("linear", "Linear_9.res.gz", max_points=bad)
    with pytest.raises(models.ModelError):
        run.read_results("linear", "Linear_9.res.gz", start=3, end=2)
    assert run.read_results("linear", "Linear_9.res.gz", variables=["E"], start=-5, end=999)["steps"] == [0, 1, 2, 3, 4]


@pytest.fixture
def industry(models_dir, lsd_root):
    models.copy_model("SantAnna/Industry", "ind")
    return models_dir / "ind"


def test_industry_steps_and_summary_order(industry):
    result = run.run_configuration("ind", "MarkI-Beta", seed=1)
    assert result["ok"], result
    summary = result["first_run"]
    assert summary["steps"] == 201
    names = [item["series"].split(" ")[0] for item in summary["series"]]
    assert not any(name.startswith("_") for name in names[:14])  # single-instance names first
    assert names[14].startswith("_")
    assert "omitted" in summary["note"]
    assert summary["series"][0]["series"] == "E 1" and summary["series"][0]["last"] is not None
    out = run.read_results("ind", "MarkI-Beta_1.res.gz", variables=["E", "_s"], max_points=300)
    assert out["steps"][0] == 0 and out["steps"][-1] == 200
    assert out["series"]["E 1"][0] is None
    assert any(key.startswith("_s 1_1 (") for key in out["series"])
    assert "series omitted" in out["note"]


def test_sa_window_is_validated(linear, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    sa.create_design("linear", "Linear", {"a": [0, 1], "b": [2, 3]}, samples=4, validation_samples=2)
    for arguments in ({"ini_drop": -1}, {"ini_drop": 10}, {"n_keep": 0}, {"n_keep": 11},
                      {"ini_drop": 5, "n_keep": 6}):
        with pytest.raises(models.ModelError) as err:
            sa.analyze("linear", "Linear", "Z", **arguments)
        assert "MAX_STEP" in str(err.value) or "n_keep" in str(err.value)


def test_timeouts_below_one_are_refused(linear, lsd_root):
    with pytest.raises(models.ModelError):
        run.run_configuration("linear", "Linear", timeout_s=0)
    with pytest.raises(models.ModelError):
        sa.run_design("linear", "Linear", timeout_s=0)


def test_set_values_reports_replacement(linear):
    first = models.set_values("linear", "Linear", {"a": 0.1}, new_config="Other")
    assert first["replaced"] is False and "backup" not in first
    again = models.set_values("linear", "Linear", {"a": 0.2}, new_config="Other")
    assert again["replaced"] is True and again["backup"] == "Other.lsd.bak"
    itself = models.set_values("linear", "Linear", {"a": 0.3})
    assert itself["replaced"] is True and itself["backup"] == "Linear.lsd.bak"


def test_copy_model_skips_design_output(linear, models_dir, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    sa.create_design("linear", "Linear", {"a": [0, 1], "b": [2, 3]}, samples=3, validation_samples=2)
    (linear / "Linear_sa" / "Z-kriging").mkdir(parents=True)
    (linear / "Linear_sa" / "Z-kriging" / "fit.csv").write_text("metric,value\n")
    (linear / "extra.lsd").write_text((linear / "Linear.lsd").read_text())
    (linear / "extra_2.lsd").write_text((linear / "Linear.lsd").read_text())
    files = models.copy_model("linear", "copy", source_group="models")["files"]
    assert "Linear.sa" in files and "Linear.lsd" in files
    assert "extra_2.lsd" in files  # no design for 'extra', so it is a real configuration
    assert not [name for name in files if name.startswith("Linear_")]
