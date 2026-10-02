from pathlib import Path

import pytest

from lsd_mcp import lsdfile

DATA = Path(__file__).parent / "data"


def example(lsd_root, relative):
    return lsd_root / "Example" / relative


def test_linear_flat_model():
    parsed = lsdfile.parse(DATA / "linear" / "Linear.lsd")
    assert list(parsed.objects) == ["Root", "Unit"]
    assert parsed.objects["Unit"].parent == "Root"
    a = parsed.element("a")
    assert (a.kind, a.lags, a.saved, a.values) == ("Param", 0, False, [0.5])
    z = parsed.element("Z")
    assert (z.kind, z.lags, z.saved) == ("Var", 1, True)
    assert parsed.settings == {"SIM_NUM": "1", "SEED": "1", "MAX_STEP": "10",
                               "EQUATION": "fun_Linear.cpp"}


def test_coordination_nested_with_many_instances(lsd_root):
    parsed = lsdfile.parse(example(lsd_root, "Literature/Coordination/Single.lsd"))
    assert parsed.objects["Agent"].parent == "Pop"
    assert parsed.objects["Agent"].instances == 500
    strategy = parsed.element("Strategy")
    assert len(strategy.values) == 500
    assert strategy.values[0] == pytest.approx(0.002)
    assert parsed.element("Mean").saved
    assert parsed.settings["MAX_STEP"] == "2000"
    assert parsed.settings["EQUATION"] == "fun_coor.cpp"


def test_multicountry_counts_per_parent_instance(lsd_root):
    parsed = lsdfile.parse(example(lsd_root, "SantAnna/Multicountry-simple/Sim_2c.lsd"))
    counts = {name: obj.instances for name, obj in parsed.objects.items()}
    assert counts == {"Root": 1, "country": 2, "sector": 4, "firm": 1000, "comp": 2000}
    assert parsed.objects["firm"].blocks == 4


def test_lagged_variable_has_one_value_per_lag_and_instance(lsd_root):
    parsed = lsdfile.parse(example(lsd_root, "SantAnna/Multicountry-simple/Sim_2c.lsd"))
    element = parsed.element("AvgProd")
    assert element.kind == "Var" and element.lags == 2
    assert len(element.values) == 2 * parsed.objects[element.obj].instances


def test_parses_every_example(lsd_root):
    count = 0
    for path in (lsd_root / "Example").rglob("*.lsd"):
        parsed = lsdfile.parse(path)
        assert parsed.objects
        assert "MAX_STEP" in parsed.settings
        count += 1
    assert count > 100


def test_set_settings_changes_only_those_lines():
    text = (DATA / "linear" / "Linear.lsd").read_text()
    new = lsdfile.set_settings(text, SIM_NUM=3, MAX_STEP=20)
    changed = [(a, b) for a, b in zip(text.splitlines(), new.splitlines()) if a != b]
    assert changed == [("SIM_NUM 1", "SIM_NUM 3"), ("MAX_STEP 10", "MAX_STEP 20")]


def test_set_save_flags():
    text = (DATA / "linear" / "Linear.lsd").read_text()
    new, changed = lsdfile.set_save_flags(text, ["a", "Z"], saved=True)
    assert changed == ["Z", "a"]
    parsed = lsdfile.parse_text(new)
    assert parsed.element("a").saved and parsed.element("Z").saved
    assert not parsed.element("b").saved
    new, _ = lsdfile.set_save_flags(new, ["Z"], saved=False)
    assert not lsdfile.parse_text(new).element("Z").saved
