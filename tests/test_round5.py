import pytest

from lsd_mcp import lsdfile, models, sa


def test_coarse_integer_factor_warns_but_creates(linear, lsd_root):
    models.set_saved("linear", "Linear", ["Z"])
    result = sa.create_design("linear", "Linear", {"n": [1, 2, "int"], "a": [0, 1]},
                              samples=24, validation_samples=2)
    assert "factor n has 2 levels for 24 samples" in result["warning"].replace("Integer ", "")
    assert "Linear_1_24.csv" == result["design_table"]


def test_nine_level_integer_factor_does_not_warn(linear, lsd_root):
    result = sa.create_design("linear", "Linear", {"n": [1, 9, "int"], "a": [0, 1]},
                              samples=17, validation_samples=2)
    assert "warning" not in result


def test_leading_minor_message(fake_r, linear, monkeypatch):
    def failing(command, **kwargs):
        from pathlib import Path
        from lsd_mcp.runner import Result
        (Path(command[-2]) / "error.txt").write_text(
            "R analysis failed: the leading minor of order 7 is not positive")
        return Result(1, "")
    monkeypatch.setattr(sa, "run", failing)
    result = sa.analyze("linear", "Linear", "Z")
    assert result["ok"] is False and "not positive definite" in result["message"]
    assert "polynomial" in result["message"] and "leading minor" in result["r_message"]


def test_values_by_lag(linear):
    path = linear / "Linear.lsd"
    text = lsdfile.read_text(path)
    text = text.replace("Var: Z 1 s + n n\t0", "Var: Z 4 s + n n\t5\t0\t0\t0")
    lsdfile.write_text(path, text)
    z = [e for o in models.describe_configuration("linear", "Linear")["objects"]
         for e in o["elements"] if e["name"] == "Z"][0]
    assert z["values_by_lag"] == [5.0, 0.0, 0.0, 0.0] and "value" not in z


def test_values_by_lag_with_instances_differing(linear):
    path = linear / "Linear.lsd"
    text = lsdfile.read_text(path).replace("Object: Unit C\t1", "Object: Unit C\t2")
    for name, value in (("a", "0.5\t0.6"), ("b", "2.5\t2.5"), ("c", "0\t0"), ("n", "5\t5")):
        text = text.replace("Param: %s 0 n + n n\t" % name, "Param: %s 0 n + n n\t%s\t#" % (name, value), 1)
    import re
    text = re.sub(r"\t#[^\n]*", "", text)
    text = text.replace("Var: Z 1 s + n n\t0", "Var: Z 2 s + n n\t1\t2\t3\t2")
    lsdfile.write_text(path, text)
    z = [e for o in models.describe_configuration("linear", "Linear")["objects"]
         for e in o["elements"] if e["name"] == "Z"][0]
    assert z["ranges_by_lag"] == [{"min": 1.0, "max": 3.0}, {"min": 2.0, "max": 2.0}]
    assert z["count_per_lag"] == 2


def test_equation_file_comes_first_in_source_files(linear):
    (linear / "a_first.h").write_text("//\n")
    (linear / "zz.cpp").write_text("//\n")
    assert models.list_models("models")[0]["source_files"] == ["fun_Linear.cpp", "a_first.h", "zz.cpp"]


def test_update_data_does_not_break_lag_values():
    text = ("\nLabel Root\n{\n}\n\nDATA\n\nObject: Root C\t1\nVar: X 2 s + n n\t1\t2\t<upd: 1 0 1 0>\n"
            "\nSIM_NUM 1\nSEED 1\nMAX_STEP 5\n")
    assert lsdfile.parse_text(text).element("X").values == [1.0, 2.0]
