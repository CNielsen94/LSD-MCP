"""Behaviour fixes: seeds, equation backups, analysis safety, result reading."""

import gzip
from pathlib import Path

import pytest

from lsd_mcp import build, lsdfile, models, run, sa

GCC_OUTPUT = """In file included from fun_Bad.cpp:1:
fun_Bad.cpp: In member function 'double variable::fun(object*)':
/home/lsd/LSD/src/fun_head.h:187:17: error: expected ';' before 'res'
  187 |                 res = X; \\
      |                 ^~~
fun_Bad.cpp:8:1: note: in expansion of macro 'RESULT'
    8 | RESULT( v[0] + v[1] )
      | ^~~~~~
"""


def set_seed_zero(folder, name="Linear"):
    path = folder / (name + ".lsd")
    lsdfile.write_text(path, lsdfile.set_settings(lsdfile.read_text(path), SEED=0))


def test_set_run_settings_refuses_values_below_one(linear):
    for arguments in ({"seed": 0}, {"runs": 0}, {"steps": -3}):
        with pytest.raises(models.ModelError):
            models.set_run_settings("linear", "Linear", **arguments)


def test_run_refuses_bad_arguments(linear, lsd_root):
    with pytest.raises(models.ModelError):
        run.run_configuration("linear", "Linear", seed=0)
    with pytest.raises(models.ModelError):
        run.run_configuration("linear", "Linear", runs=0)


def test_seed_zero_configuration_is_reported_everywhere(linear, lsd_root):
    set_seed_zero(linear)
    described = models.describe_configuration("linear", "Linear")
    assert "set_run_settings(seed=1)" in described["warning"]
    for call in (lambda: run.run_configuration("linear", "Linear"),
                 lambda: models.set_values("linear", "Linear", {"a": 1}),
                 lambda: sa.create_design("linear", "Linear", {"a": [0, 1]}, samples=3)):
        with pytest.raises(models.ModelError) as err:
            call()
        assert "SEED 0" in str(err.value) and "set_run_settings(seed=1)" in str(err.value)
    models.set_run_settings("linear", "Linear", seed=1)
    assert "warning" not in models.describe_configuration("linear", "Linear")
    assert run.run_configuration("linear", "Linear")["ok"]


def test_write_equations_keeps_first_original_and_latest_backup(linear):
    first = (linear / "fun_Linear.cpp").read_text()
    models.write_equations("linear", first + "// one\n")
    result = models.write_equations("linear", first + "// two\n")
    assert result == {"written": "fun_Linear.cpp", "backup": "fun_Linear.cpp.bak",
                      "original": "fun_Linear.cpp.orig"}
    assert (linear / "fun_Linear.cpp.orig").read_text() == first
    assert (linear / "fun_Linear.cpp.bak").read_text() == first + "// one\n"
    info = models.list_models("models")[0]
    assert info["equation_file"] == "fun_Linear.cpp"
    copy = models.copy_model("linear", "linear2", source_group="models")
    assert not [name for name in copy["files"] if name.endswith((".bak", ".orig"))]


def test_totals_file_is_explained(linear, lsd_root):
    assert run.run_configuration("linear", "Linear", seed=4, runs=2)["ok"]
    with pytest.raises(models.ModelError) as err:
        run.read_results("linear", "Linear_4_5.tot.gz")
    text = str(err.value)
    assert "totals file" in text and "Linear_4.res.gz" in text and "Linear_5.res.gz" in text


def test_read_results_reports_names_not_found(linear, lsd_root):
    assert run.run_configuration("linear", "Linear", seed=1)["ok"]
    out = run.read_results("linear", "Linear_1.res.gz", variables=["Z", "Nope"])
    assert list(out["series"]) == ["Z 1"] and out["not_found"] == ["Nope"]
    assert "not_found" not in run.read_results("linear", "Linear_1.res.gz", variables=["Z"])


def test_configurations_sorted_naturally(linear):
    for number in (2, 10, 1):
        (linear / ("SAbase_%d.lsd" % number)).write_text((linear / "Linear.lsd").read_text())
    assert models.list_models("models")[0]["configurations"] == [
        "Linear", "SAbase_1", "SAbase_2", "SAbase_10"]


def test_polynomial_needs_two_factors(linear, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    sa.create_design("linear", "Linear", {"a": [0, 1]}, samples=5, validation_samples=2)
    with pytest.raises(models.ModelError) as err:
        sa.analyze("linear", "Linear", "Z", metamodel="polynomial")
    assert "two factors" in str(err.value)


# --- analysis with a fake R (fixture fake_r in conftest.py) ---

def test_failed_analysis_keeps_previous_results(fake_r, linear):
    good = sa.analyze("linear", "Linear", "Z")
    assert good["ok"] and "warning" not in good
    before = (linear / "Linear_sa" / "Z-kriging" / "sobol.csv").read_text()
    fake_r["fail"] = True
    bad = sa.analyze("linear", "Linear", "Z")
    assert bad["ok"] is False and "boom" in bad["message"]
    assert (linear / "Linear_sa" / "Z-kriging" / "sobol.csv").read_text() == before
    assert not list(linear.rglob("error.txt"))


def test_low_fit_gives_a_warning(fake_r):
    fake_r["q2"] = 0.3
    result = sa.analyze("linear", "Linear", "Z")
    assert result["ok"] and "not reliable" in result["warning"] and "rule of thumb" in result["warning"]


def test_r_seed_is_passed_to_r(fake_r, monkeypatch):
    seen = []
    original = sa.run

    def spy(command, **kwargs):
        seen.append(str(command[-1]))
        return original(command, **kwargs)

    monkeypatch.setattr(sa, "run", spy)
    sa.analyze("linear", "Linear", "Z")
    sa.analyze("linear", "Linear", "Z", r_seed=7)
    assert seen == ["1", "7"]


# --- compiler diagnostics ------------------------------------------------------------

def test_gcc_macro_error_gets_equation_location():
    errors = build.parse_errors(GCC_OUTPUT, 30, Path("/home/lsd/LSD/src"), Path("/work/m"))
    assert errors == ["fun_head.h:187: expected ';' before 'res' "
                      "[in equation file: fun_Bad.cpp:8, in expansion of macro 'RESULT']"]


def test_macro_error_without_note_gets_a_hint():
    text = "/home/lsd/LSD/src/fun_head.h:187:17: error: expected ';' before 'res'\n"
    errors = build.parse_errors(text, 30, Path("/home/lsd/LSD/src"), Path("/work/m"))
    assert "inside LSD's macros" in errors[0] and errors[0].startswith("fun_head.h:187:")


def test_error_in_equation_file_is_left_alone():
    text = "fun_Bad.cpp:7:9: error: expected ';' after expression\n"
    assert build.parse_errors(text, 30, Path("/home/lsd/LSD/src"), Path("/work/m")) == [
        "fun_Bad.cpp:7: expected ';' after expression"]


def test_real_compiler_missing_semicolon(linear, lsd_root):
    bad = '#include "fun_head.h"\n\nMODELBEGIN\n\nEQUATION( "Z" )\nv[0] = V( "a" )\nRESULT( v[0] )\n\nMODELEND\n\nvoid close_sim( void )\n{\n}\n'
    models.write_equations("linear", bad)
    built = build.compile_model(lsd_root, linear)
    assert not built.ok
    assert "fun_Linear.cpp:6" in built.errors[0] or "equation file: fun_Linear.cpp:7" in built.errors[0], built.errors
