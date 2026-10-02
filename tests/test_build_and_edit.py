import shutil
from pathlib import Path

import pytest

from lsd_mcp import build, lsdfile, models
from lsd_mcp.runner import run


def test_utilities_build_and_work(utilities, linear):
    assert sorted(utilities) == ["lsd_confgen", "lsd_getlimits", "lsd_getsaved", "lsd_mcstats"]
    for exe in utilities.values():
        assert exe.is_file()
    out = linear / "saved.csv"
    result = run([utilities["lsd_getsaved"], "-f", linear / "Linear.lsd", "-o", out])
    assert result.ok, result.output
    assert out.read_text().splitlines()[1].startswith("Z,variable,Unit")


def test_set_values_changes_exactly_the_intended_lines(linear):
    original = (linear / "Linear.lsd").read_text()
    models.set_values("linear", "Linear", {"a": 0.7, "b": 1.5}, new_config="Changed")
    new = (linear / "Changed.lsd").read_text()
    diff = [(a, b) for a, b in zip(original.splitlines(), new.splitlines()) if a != b]
    assert diff == [("Param: a 0 n + n n\t0.5", "Param: a 0 n + n n\t0.7"),
                    ("Param: b 0 n + n n\t2.5", "Param: b 0 n + n n\t1.5")]
    assert len(original.splitlines()) == len(new.splitlines())
    assert "EQUATION fun_Linear.cpp" in new.splitlines()
    assert (linear / "Linear.lsd").read_text() == original


def test_set_values_in_place_keeps_backup_and_descriptions(linear):
    original = (linear / "Linear.lsd").read_text()
    models.set_values("linear", "Linear", {"c": 3})
    assert (linear / "Linear.lsd.bak").read_text() == original
    new = (linear / "Linear.lsd").read_text()
    assert "\nDESCRIPTION\n" in new and "EQUATION fun_Linear.cpp" in new
    assert lsdfile.parse(linear / "Linear.lsd").element("c").values == [3.0]


def test_set_values_unknown_element(linear):
    result = models.set_values
    with pytest.raises(models.ModelError):
        result("linear", "Linear", {"nope": 1})


def test_set_values_every_instance(models_dir, lsd_root):
    models.copy_model("Literature/Coordination", "coor")
    models.set_values("coor", "Single", {"Strategy": 0.25}, new_config="Flat")
    parsed = lsdfile.parse(models_dir / "coor" / "Flat.lsd")
    values = parsed.element("Strategy").values
    assert len(values) == 500 and set(values) == {0.25}
    assert "\nDESCRIPTION\n" in (models_dir / "coor" / "Flat.lsd").read_text()


def test_paths_cannot_escape(models_dir, lsd_root):
    for bad in ("../x", "/etc", "a/../../b"):
        with pytest.raises(models.ModelError):
            models.resolve_model(bad, "models")
    with pytest.raises(models.ModelError):
        models.copy_model("Literature/Coordination", "../outside")
    with pytest.raises(models.ModelError):
        models.resolve_model("Literature/../../src", "examples")


def test_copy_model_has_sources_only(models_dir, lsd_root):
    info = models.copy_model("Literature/Coordination", "coor")
    assert "fun_coor.cpp" in info["files"] and "Single.lsd" in info["files"]
    for name in info["files"]:
        assert not name.endswith((".o", ".gz", ".res"))
    with pytest.raises(models.ModelError):
        models.copy_model("Literature/Coordination", "coor")


def test_list_models(models_dir, linear, lsd_root):
    found = models.list_models("models")
    assert [item["model"] for item in found] == ["linear"]
    assert found[0]["equation_file"] == "fun_Linear.cpp"
    assert found[0]["configurations"] == ["Linear"]
    examples = [item["model"] for item in models.list_models("examples")]
    assert "Literature/Coordination" in examples


def test_write_equations_keeps_backup(linear):
    before = (linear / "fun_Linear.cpp").read_text()
    models.write_equations("linear", before + "\n// edited\n")
    assert (linear / "fun_Linear.cpp.bak").read_text() == before
    assert models.read_equations("linear").endswith("// edited\n")


def test_compile_error_reports_line_number(linear, lsd_root):
    source = (linear / "fun_Linear.cpp").read_text()
    broken = source.replace('RESULT( ( ( 2.0', 'RESULT( ( ( 2.0 +* ', 1)
    line = [i for i, text in enumerate(broken.splitlines(), 1) if "+*" in text][0]
    models.write_equations("linear", broken)
    built = build.compile_model(lsd_root, linear)
    assert not built.ok
    assert any(error.startswith("fun_Linear.cpp:%d:" % line) for error in built.errors), built.errors


def test_rebuild_only_when_equations_change(linear, lsd_root):
    first = build.compile_model(lsd_root, linear)
    assert first.ok
    again = build.compile_model(lsd_root, linear)
    assert again.cached
    models.write_equations("linear", (linear / "fun_Linear.cpp").read_text() + "\n// x\n")
    assert not build.compile_model(lsd_root, linear).cached


def test_refuses_to_write_under_the_lsd_source(lsd_root, monkeypatch):
    monkeypatch.setenv("LSD_MODELS", str(lsd_root / "Example"))
    with pytest.raises(models.ModelError):
        models.write_equations("Literature/Coordination", "x")
    with pytest.raises(models.ModelError):
        models.set_values("Literature/Coordination", "Single", {"c1": 1})


def test_copy_model_creates_modelinfo(linear, models_dir):
    info = models.copy_model("linear", "linear2", source_group="models")
    assert "modelinfo.txt" in info["files"]
    assert (models_dir / "linear2" / "modelinfo.txt").read_text() == "linear2\n"
