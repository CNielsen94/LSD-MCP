"""Series keys, multi-file models, describe options, analysis folders."""

import gzip

import pytest

from lsd_mcp import build, lsdfile, models, run, sa

SYNTHETIC = (
    "_s 1_1 (1 4)\t_s 1_1 (1 4)\t_s 1_1 (2 4)\tHHI 1 (0 4)\t\n"
    "NA\tNA\tNA\t0\t\n"
    "1\t10\tNA\t1\t\n"
    "2\t20\t5\t2\t\n"
    "3\t30\t6\t3\t\n"
    "4\t40\t7\t4\t\n")


@pytest.fixture
def duplicates(linear):
    with gzip.open(linear / "Linear_9.res.gz", "wt") as handle:
        handle.write(SYNTHETIC)
    return linear


def test_repeated_labels_get_span_and_counter_keys(duplicates):
    out = run.read_results("linear", "Linear_9.res.gz")
    assert list(out["series"]) == ["_s 1_1 (1 4)", "_s 1_1 (1 4) #2", "_s 1_1 (2 4)", "HHI 1"]
    assert out["series"]["_s 1_1 (1 4)"][1:] == [1, 2, 3, 4]
    assert out["series"]["_s 1_1 (1 4) #2"][1:] == [10, 20, 30, 40]


def test_filters_go_from_specific_to_general(duplicates):
    def keys(wanted):
        return list(run.read_results("linear", "Linear_9.res.gz", variables=[wanted])["series"])
    assert keys("_s 1_1 (2 4)") == ["_s 1_1 (2 4)"]
    assert keys("_s 1_1 (1 4) #2") == ["_s 1_1 (1 4) #2"]
    assert keys("_s 1_1 (1 4)") == ["_s 1_1 (1 4)", "_s 1_1 (1 4) #2"]
    assert len(keys("_s 1_1")) == 3 and len(keys("_s")) == 3
    assert keys("HHI") == ["HHI 1"]
    summary = run.summarise(duplicates / "Linear_9.res.gz")
    assert len(set(item["series"] for item in summary["series"])) == 4


@pytest.fixture
def industry(models_dir, lsd_root):
    models.copy_model("SantAnna/Industry", "ind")
    result = run.run_configuration("ind", "MarkI-Beta", seed=1)
    assert result["ok"]
    return models_dir / "ind"


def test_industry_duplicate_labels_are_never_overwritten(industry):
    with gzip.open(industry / "MarkI-Beta_1.res.gz", "rt") as handle:
        header = handle.readline().rstrip("\n").split("\t")
    same = [cell for cell in header if cell.startswith("_s 1_1 (")]
    assert len(same) > 1
    out = run.read_results("ind", "MarkI-Beta_1.res.gz", variables=["_s 1_1"])
    assert len(out["series"]) == min(len(same), run.MAX_SERIES)
    one = run.read_results("ind", "MarkI-Beta_1.res.gz", variables=[list(out["series"])[0]])
    assert len(one["series"]) == 1
    assert list(run.read_results("ind", "MarkI-Beta_1.res.gz", variables=["HHI"])["series"]) == ["HHI 1"]
    capped = run.read_results("ind", "MarkI-Beta_1.res.gz", variables=["_s"])
    assert len(capped["series"]) == run.MAX_SERIES and "narrow variables" in capped["note"]


def test_constant_response_degenerate_fit_and_note(fake_r, linear):
    fake_r["sobol"] = "factor,direct,interactions\na,0.3,0.1\nb,0.3,0.1\n"
    result = sa.analyze("linear", "Linear", "Z")
    assert result["ok"] and "could not separate the factors" in result["warning"]
    assert "Q2" not in result["warning"]
    fake_r["q2"] = 0.2
    both = sa.analyze("linear", "Linear", "Z")
    assert "not reliable" in both["warning"] and "could not separate" in both["warning"]
    assert not sa.factors_not_separated([{"direct": 1, "interactions": 0}])


def test_each_analysis_has_its_own_folder(fake_r, linear):
    first = sa.analyze("linear", "Linear", "Z")
    second = sa.analyze("linear", "Linear", "Z", metamodel="polynomial")
    assert "Linear_sa/Z-kriging/fit.csv" in first["files"]
    assert "Linear_sa/Z-polynomial/sobol.csv" in second["files"]
    assert (linear / "Linear_sa" / "Z-kriging" / "sobol.csv").is_file()
    assert sa.analysis_folder("_s/a b", "kriging") == "_s_a_b-kriging"


def test_run_without_result_file_explains(models_dir, lsd_root):
    models.copy_model("Literature/PercolationVect", "perc")
    result = run.run_configuration("perc", "BasicSmall")
    assert result["ok"] and "no result file" in result["note"]
    assert "stopped" in result["output_tail"]


def test_multi_file_models(linear):
    (linear / "extra.h").write_text("// header\n")
    info = models.list_models("models")[0]
    assert info["source_files"] == ["fun_Linear.cpp", "extra.h"]
    assert models.read_equations("linear", file="extra.h") == "// header\n"
    written = models.write_equations("linear", "// v1\n", file="extra.h")
    assert written["backup"] == "extra.h.bak"
    again = models.write_equations("linear", "// v2\n", file="extra.h")
    assert (linear / "extra.h.orig").read_text() == "// header\n"
    assert (linear / "extra.h.bak").read_text() == "// v1\n" and again["original"] == "extra.h.orig"
    assert models.write_equations("linear", "// new\n", file="new.hpp") == {"written": "new.hpp", "created": True}
    for bad in ("../x.h", "a/b.h", "notes.txt", ".hidden.h"):
        with pytest.raises(models.ModelError):
            models.read_equations("linear", file=bad)
    with pytest.raises(models.ModelError):
        models.read_equations("linear", file="missing.h")
    assert "extra.h.bak" not in models.list_models("models")[0]["source_files"]


def test_header_change_triggers_rebuild(linear, lsd_root):
    source = (linear / "fun_Linear.cpp").read_text()
    (linear / "local.h").write_text("#define OFFSET 7.0\n")
    models.write_equations("linear", source.replace("+ 7.0", "+ OFFSET").replace(
        '#include "fun_head_fast.h"', '#include "fun_head_fast.h"\n#include "local.h"'))
    assert build.compile_model(lsd_root, linear).ok
    assert build.compile_model(lsd_root, linear).cached
    models.write_equations("linear", "// changed\n#define OFFSET 8.0\n", file="local.h")
    assert not build.compile_model(lsd_root, linear).cached


def test_describe_object_and_names(linear):
    text = lsdfile.read_text(linear / "Linear.lsd")
    text = text.replace("Object: Unit C", "Object: Unit N")
    text = text.replace("Param: a 0 n + n n", "Param: a 0 n + d P").replace("Var: Z 1 s + n n", "Var: Z 1 S + n n")
    lsdfile.write_text(linear / "Linear.lsd", text)
    full = models.describe_configuration("linear", "Linear", object="Unit")
    assert [obj["object"] for obj in full["objects"]] == ["Unit"]
    unit = full["objects"][0]
    assert unit["computed"] is False
    elements = {element["name"]: element for element in unit["elements"]}
    assert elements["a"]["debug"] and elements["a"]["plot"] and elements["a"]["parallel"]
    assert elements["Z"]["saved"] and elements["Z"]["saved_separately"]
    assert "debug" not in elements["b"] and "saved_separately" not in elements["b"]
    names = models.describe_configuration("linear", "Linear", detail="names")
    root, unit = names["objects"]
    assert unit["elements"] == {"Param": ["a", "b", "c", "n"], "Var": ["Z"]}
    assert "computed" not in root
    with pytest.raises(models.ModelError):
        models.describe_configuration("linear", "Linear", object="Nope")
    with pytest.raises(models.ModelError):
        models.describe_configuration("linear", "Linear", detail="x")
