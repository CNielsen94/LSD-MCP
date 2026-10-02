"""edit_structure and create_model (LSD's own code through lsd_edit)."""

import difflib
import glob
import gzip
import re
from pathlib import Path

import pytest

from lsd_mcp import build, lsdfile, lsdsource, models, run, structure
from lsd_mcp.runner import run as run_command

ROOT_OBJECT = {"op": "add_object", "parent": "Root", "name": "Pop"}
POP_R = {"op": "add_parameter", "object": "Pop", "name": "r", "value": 0.5}
POP_X = {"op": "add_variable", "object": "Pop", "name": "X", "lags": 1, "initial": 100, "saved": True}
EQUATION = 'EQUATION( "X" )\nRESULT( V( "r" ) * VL( "X", 1 ) )\n'


def fill_equations(folder: Path, equations: str):
    """Replace what is between MODELBEGIN and MODELEND (through write_equations)."""
    path = next(folder.glob("fun_*.cpp"))
    text = re.sub(r"(^MODELBEGIN\n).*?(^MODELEND\n)", lambda m: m.group(1) + "\n" + equations + "\n" + m.group(2),
                  path.read_text(), flags=re.S | re.M)
    models.write_equations(folder.name, text)


def series(folder: Path, name: str, column="X"):
    """Values of one saved variable in a result file, as numbers."""
    lines = gzip.open(folder / name, "rt").read().splitlines()
    names = [title.split()[0] for title in lines[0].split("\t") if title.strip()]
    index = names.index(column)
    cells = [line.split("\t")[index] for line in lines[1:]]
    return [None if cell == "NA" else float(cell) for cell in cells]  # NA: no value at step 0


def line_diff(before: str, after: str):
    """(removed, added) lines of a unified diff, without the file headers, blank
    lines and END_DESCRIPTION lines (each description block ends with one)."""
    removed, added = [], []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=0, lineterm=""):
        if line.startswith(("---", "+++", "@@")) or line[1:] in ("", "END_DESCRIPTION"):
            continue
        (removed if line[0] == "-" else added).append(line[1:])
    return removed, added


@pytest.fixture
def pop(models_dir, lsd_root):
    """A model made by create_model with Pop (parameter r = 0.5, variable X)."""
    structure.create_model("pop", "Population", "test")
    structure.edit_structure("pop", "Sim1", [ROOT_OBJECT, POP_R, POP_X])
    return models_dir / "pop"


def edit(operations, new_config=None):
    return structure.edit_structure("pop", "Sim1", operations, new_config)


def element(folder: Path, name: str, config="Sim1"):
    return lsdfile.parse(folder / (config + ".lsd")).element(name)


# --- create_model ---------------------------------------------------------------

def test_create_model_makes_what_lmm_makes(models_dir, lsd_root):
    info = structure.create_model("fresh", "Fresh model", "A short description.")
    folder = models_dir / "fresh"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Sim1.lsd", "description.txt", "fun_fresh.cpp", "model_options.txt", "modelinfo.txt"]
    assert info["files"] == sorted(p.name for p in folder.iterdir())
    template = (lsd_root / "src" / "fun_base.cpp").read_text()
    assert (folder / "fun_fresh.cpp").read_text() == template
    options = (folder / "model_options.txt").read_text()
    assert "FUN=fun_fresh\n" in options and options.startswith("# LSD options\nTARGET=LSD\n")
    lines = (folder / "modelinfo.txt").read_text().splitlines()
    assert len(lines) == 16 and lines[0] == "Fresh model" and lines[1] == "1.0"
    assert re.match(r"^\d\d \w+, \d{4}$", lines[2]) and lines[-4:] == ["Root", "1", "0", "0"]
    assert (folder / "description.txt").read_text() == "Fresh model\n\nA short description.\n"
    parsed = lsdfile.parse(folder / "Sim1.lsd")
    assert list(parsed.objects) == ["Root"] and parsed.settings["EQUATION"] == "fun_fresh.cpp"
    assert "MODELREPORT report_Sim1.html" in (folder / "Sim1.lsd").read_text()
    assert [m["model"] for m in models.list_models()] == ["fresh"]  # listed: it has modelinfo.txt


def test_empty_model_compiles_and_runs_saving_nothing(models_dir, lsd_root):
    structure.create_model("empty")
    built = build.compile_model(lsd_root, models_dir / "empty")
    assert built.ok, built.errors
    result = run.run_configuration("empty", "Sim1", seed=1)
    assert result["ok"] and result["result_files"] == []
    assert "Nothing to save" in result["output_tail"]
    assert (models_dir / "empty" / "modelinfo.txt").read_text().splitlines()[0] == "empty"


def test_create_model_destination_rules(models_dir, lsd_root):
    structure.create_model("a")
    for bad in ("a", "a/inner", "../x", "/abs", "with space", "dots.in.name"):
        with pytest.raises(models.ModelError):
            structure.create_model(bad)
    structure.create_model("group/b")  # a plain group folder is fine
    assert (models_dir / "group" / "b" / "fun_b.cpp").is_file()
    with pytest.raises(models.ModelError):
        structure.create_model("c", title="two\nlines")
    assert not (models_dir / "c").exists()


# --- end to end -----------------------------------------------------------------

def test_from_nothing_to_a_running_model(models_dir, lsd_root):
    structure.create_model("pop")
    folder = models_dir / "pop"
    done = structure.edit_structure("pop", "Sim1", [ROOT_OBJECT, POP_R, POP_X])
    assert done == {"written": "Sim1.lsd", "operations": 3, "replaced": True, "backup": "Sim1.lsd.bak"}
    fill_equations(folder, EQUATION)
    models.set_run_settings("pop", "Sim1", steps=5)
    assert run.run_configuration("pop", "Sim1", seed=1)["ok"]
    assert series(folder, "Sim1_1.res.gz") == [100, 50, 25, 12.5, 6.25, 3.125]

    # second pass: three Agents under Pop, each with its own w, summed by an equation
    structure.edit_structure("pop", "Sim1", [
        {"op": "add_object", "parent": "Pop", "name": "Agent", "instances": 3},
        {"op": "add_parameter", "object": "Agent", "name": "w"},
        {"op": "set_instance_values", "name": "w", "values": [1, 2, 3]},
        {"op": "add_variable", "object": "Pop", "name": "Total", "saved": True}])
    fill_equations(folder, EQUATION + 'EQUATION( "Total" )\nRESULT( SUM( "w" ) )\n')
    assert run.run_configuration("pop", "Sim1", seed=1)["ok"]
    assert set(series(folder, "Sim1_1.res.gz", "Total")[1:]) == {6.0}
    assert element(folder, "w").values == [1, 2, 3]


# --- each operation: LSD loads the file, describe shows it, the diff is minimal ----

def getsaved(folder: Path, config="Sim1", everything=True) -> str:
    """LSD's own list of the elements (all, or only the saved ones)."""
    exe = build.utilities(lsdsource.lsd_root())["lsd_getsaved"]
    result = run_command([exe] + (["-a"] if everything else []) + ["-f", config + ".lsd"],
                         cwd=folder, timeout=60)
    assert result.ok, result.output
    return result.output


def test_add_parameter_and_variable_touch_only_their_lines(pop):
    before = (pop / "Sim1.lsd").read_text()
    edit([{"op": "add_parameter", "object": "Pop", "name": "alpha", "value": 0.25},
          {"op": "add_variable", "object": "Pop", "name": "K", "lags": 2, "initial": [1, 2]},
          {"op": "add_function", "object": "Pop", "name": "f"}])
    removed, added = line_diff(before, (pop / "Sim1.lsd").read_text())
    assert removed == []
    assert [line for line in added if line.startswith(("Param:", "Var:", "Func:"))] == [
        "Param: alpha 0 n + n n\t0.25", "Var: K 2 n + n n\t1\t2", "Func: f 0 n + n n"]
    assert [line.strip() for line in added if line.startswith("\t\t")] == [
        "Param: alpha", "Var: K", "Func: f"]
    assert [line for line in added if re.match(r"^(Parameter|Variable|Function)_", line)] == [
        "Parameter_alpha", "Variable_K", "Function_f"]
    info = models.describe_configuration("pop", "Sim1")
    text = str(info)
    for name in ("alpha", "K", "'f'"):
        assert name in text
    assert "alpha" in getsaved(pop) and "X" in getsaved(pop)
    assert element(pop, "K").values == [1, 2] and element(pop, "alpha").values == [0.25]


def test_saved_flag_reaches_the_result_files(pop):
    fill_equations(pop, EQUATION)
    models.set_run_settings("pop", "Sim1", steps=3)
    assert "X" in getsaved(pop, everything=False)
    edit([{"op": "add_variable", "object": "Pop", "name": "Y", "lags": 1, "initial": 7}])
    assert "Y" in getsaved(pop) and "Y" not in getsaved(pop, everything=False)
    assert element(pop, "Y").saved is False and element(pop, "X").saved is True


def test_one_initial_number_fills_every_lag(pop):
    edit([{"op": "add_variable", "object": "Pop", "name": "L", "lags": 3, "initial": 2.5}])
    assert element(pop, "L").values == [2.5, 2.5, 2.5]


def test_rename_element_and_object_change_only_their_names(pop):
    before = (pop / "Sim1.lsd").read_text()
    edit([{"op": "rename", "name": "r", "new_name": "rate"}])
    removed, added = line_diff(before, (pop / "Sim1.lsd").read_text())
    assert sorted(removed) == ["\t\tParam: r", "Param: r 0 n + n n\t0.5", "Parameter_r"]
    assert sorted(added) == ["\t\tParam: rate", "Param: rate 0 n + n n\t0.5", "Parameter_rate"]
    before = (pop / "Sim1.lsd").read_text()
    edit([{"op": "rename", "name": "Pop", "new_name": "People"}])
    removed, added = line_diff(before, (pop / "Sim1.lsd").read_text())
    assert sorted(removed) == ["\tLabel Pop", "\tSon: Pop", "Object: Pop C\t1", "Object_Pop"]
    assert sorted(added) == ["\tLabel People", "\tSon: People", "Object: People C\t1", "Object_People"]
    assert "People" in str(models.describe_configuration("pop", "Sim1"))


def test_rename_reaches_every_instance(pop):
    edit([{"op": "set_instances", "object": "Pop", "instances": 3},
          {"op": "set_instance_values", "name": "r", "values": [0.1, 0.2, 0.3]},
          {"op": "rename", "name": "r", "new_name": "rate"}])
    assert element(pop, "rate").values == [0.1, 0.2, 0.3]
    assert element(pop, "r") is None


def test_delete_element_and_object(pop):
    edit([{"op": "add_object", "parent": "Pop", "name": "Agent"},
          {"op": "add_parameter", "object": "Agent", "name": "w"}])
    before = (pop / "Sim1.lsd").read_text()
    edit([{"op": "delete", "name": "r"}])
    removed, added = line_diff(before, (pop / "Sim1.lsd").read_text())
    assert added == [] and sorted(removed) == ["\t\tParam: r", "Param: r 0 n + n n\t0.5", "Parameter_r"]
    with pytest.raises(models.ModelError) as err:
        edit([{"op": "delete", "name": "Agent"}])
    assert "force" in str(err.value) and "1 element" in str(err.value)
    edit([{"op": "delete", "name": "Agent", "force": True}])
    assert "Agent" not in (pop / "Sim1.lsd").read_text() and "Parameter_w" not in (pop / "Sim1.lsd").read_text()
    assert list(lsdfile.parse(pop / "Sim1.lsd").objects) == ["Root", "Pop"]
    edit([{"op": "add_object", "parent": "Pop", "name": "Empty"},
          {"op": "delete", "name": "Empty"}])  # an object with nothing in it needs no force
    assert "Empty" not in (pop / "Sim1.lsd").read_text()


def test_delete_object_with_children_and_several_instances(pop):
    edit([{"op": "set_instances", "object": "Pop", "instances": 2},
          {"op": "add_object", "parent": "Pop", "name": "Agent", "instances": 3},
          {"op": "add_object", "parent": "Agent", "name": "Part", "instances": 2},
          {"op": "delete", "name": "Pop", "force": True}])
    parsed = lsdfile.parse(pop / "Sim1.lsd")
    assert list(parsed.objects) == ["Root"]
    text = (pop / "Sim1.lsd").read_text()
    for gone in ("Pop", "Agent", "Part", "Object_X", "Variable_X"):
        assert gone not in text


def test_instance_counts_and_what_new_instances_copy(pop):
    edit([{"op": "add_object", "parent": "Pop", "name": "Agent", "instances": 2},
          {"op": "add_parameter", "object": "Agent", "name": "w"},
          {"op": "set_instance_values", "name": "w", "values": [1, 2]}])
    assert re.search(r"^Object: Agent C\t2$", (pop / "Sim1.lsd").read_text(), re.M)
    edit([{"op": "set_instances", "object": "Agent", "instances": 4}])
    assert element(pop, "w").values == [1, 2, 1, 1]  # new instances copy the first one
    edit([{"op": "set_instances", "object": "Agent", "instances": 1}])
    assert element(pop, "w").values == [1]  # extra ones are removed from the end


def test_set_instances_applies_under_every_parent_instance(pop):
    edit([{"op": "set_instances", "object": "Pop", "instances": 2},
          {"op": "add_object", "parent": "Pop", "name": "Agent", "instances": 3},
          {"op": "add_parameter", "object": "Agent", "name": "w"}])
    assert re.search(r"^Object: Agent C\t3\t3$", (pop / "Sim1.lsd").read_text(), re.M)
    assert len(element(pop, "w").values) == 6
    edit([{"op": "set_instances", "object": "Agent", "instances": 2},
          {"op": "set_instance_values", "name": "w", "values": [1, 2, 3, 4]}])
    assert re.search(r"^Object: Agent C\t2\t2$", (pop / "Sim1.lsd").read_text(), re.M)
    assert element(pop, "w").values == [1, 2, 3, 4]  # file order: parent 1's agents, then parent 2's


def test_set_instance_values_for_a_lag_and_a_new_element_in_every_instance(pop):
    edit([{"op": "set_instances", "object": "Pop", "instances": 2},
          {"op": "add_variable", "object": "Pop", "name": "V", "lags": 3, "initial": 0},
          {"op": "set_instance_values", "name": "V", "values": [5, 6], "lag": 2}])
    assert element(pop, "V").values == [0, 5, 0, 0, 6, 0]  # all lags of an instance together
    assert len(element(pop, "X").values) == 2  # an element added after the instances exist
    info = models.describe_configuration("pop", "Sim1")
    assert "ranges_by_lag" in str(info) or "values_by_lag" in str(info)


def test_describe_sets_the_description_as_lsd_stores_it(pop):
    edit([{"op": "describe", "name": "r", "text": "adjustment speed\nsecond line"},
          {"op": "describe", "name": "Pop", "text": "the population"}])
    text = (pop / "Sim1.lsd").read_text()
    assert "Parameter_r\nadjustment speed\nsecond line\nEND_DESCRIPTION" in text
    assert "Object_Pop\nthe population\nEND_DESCRIPTION" in text


def test_new_config_leaves_the_original(pop):
    original = (pop / "Sim1.lsd").read_text()
    done = edit([{"op": "add_parameter", "object": "Pop", "name": "z"}], new_config="Other")
    assert done == {"written": "Other.lsd", "operations": 1, "replaced": False}
    assert (pop / "Sim1.lsd").read_text() == original
    assert "Param: z" in (pop / "Other.lsd").read_text()
    again = edit([{"op": "add_parameter", "object": "Pop", "name": "z2"}], new_config="Other")
    assert again["replaced"] and (pop / "Other.lsd.bak").is_file()


# --- errors ----------------------------------------------------------------------

def test_errors_name_the_operation_and_change_nothing(pop):
    before = (pop / "Sim1.lsd").read_text()
    bad = [({"op": "add_parameter", "object": "Pop", "name": "r"}, "already exists"),
           ({"op": "add_variable", "object": "Pop", "name": "Pop"}, "already exists"),
           ({"op": "add_object", "parent": "Root", "name": "r"}, "already exists"),
           ({"op": "add_parameter", "object": "Pop", "name": "bad name"}, "invalid name"),
           ({"op": "add_parameter", "object": "Pop", "name": "1x"}, "invalid name"),
           ({"op": "add_parameter", "object": "Pop", "name": "a-b"}, "invalid name"),
           ({"op": "add_parameter", "object": "Nope", "name": "p"}, "unknown object 'Nope'"),
           ({"op": "add_object", "parent": "Nope", "name": "p"}, "unknown object"),
           ({"op": "rename", "name": "r", "new_name": "X"}, "already exists"),
           ({"op": "rename", "name": "Root", "new_name": "Top"}, "Root cannot be renamed"),
           ({"op": "rename", "name": "zzz", "new_name": "y"}, "unknown object or element"),
           ({"op": "delete", "name": "Root"}, "Root cannot be deleted"),
           ({"op": "delete", "name": "zzz"}, "unknown object or element"),
           ({"op": "delete", "name": "Pop"}, "force"),
           ({"op": "set_instances", "object": "Pop", "instances": 0}, "at least 1"),
           ({"op": "set_instances", "object": "Root", "instances": 2}, "Root"),
           ({"op": "set_instance_values", "name": "r", "values": [1, 2]}, "1 instance(s) but 2 value(s)"),
           ({"op": "set_instance_values", "name": "r", "values": [1], "lag": 2}, "no lags"),
           ({"op": "set_instance_values", "name": "X", "values": [1], "lag": 2}, "has 1 lag"),
           ({"op": "add_variable", "object": "Pop", "name": "N", "initial": 1}, "no initial value"),
           ({"op": "add_variable", "object": "Pop", "name": "N", "lags": 2, "initial": [1, 2, 3]}, "one per lag"),
           ({"op": "describe", "name": "zzz", "text": "t"}, "unknown object or element")]
    for operation, message in bad:
        with pytest.raises(models.ModelError) as err:
            edit([operation])
        assert message in str(err.value), (operation, str(err.value))
        assert str(err.value).startswith("operation 1 (%s)" % operation["op"])
    assert (pop / "Sim1.lsd").read_text() == before


def test_a_failing_third_operation_leaves_the_file_untouched(pop):
    before = (pop / "Sim1.lsd").read_bytes()
    backups = sorted(p.name for p in pop.iterdir())
    with pytest.raises(models.ModelError) as err:
        edit([{"op": "add_parameter", "object": "Pop", "name": "one"},
              {"op": "add_parameter", "object": "Pop", "name": "two"},
              {"op": "add_parameter", "object": "Pop", "name": "r"}])
    assert str(err.value).startswith("operation 3 (add_parameter)")
    assert (pop / "Sim1.lsd").read_bytes() == before
    assert sorted(p.name for p in pop.iterdir()) == backups


def test_malformed_operations_are_refused_before_lsd_runs(pop):
    for operations in ([], "x", [1], [{"name": "a"}], [{"op": "nope"}], [{"op": "add_object", "name": "A"}],
                       [{"op": "add_object", "parent": "Root", "name": "A", "color": 1}],
                       [{"op": "add_object", "parent": "Root", "name": "A", "instances": "2"}],
                       [{"op": "add_parameter", "object": "Pop", "name": "a", "value": "1"}],
                       [{"op": "add_variable", "object": "Pop", "name": "a", "saved": "yes"}],
                       [{"op": "set_instance_values", "name": "r", "values": []}],
                       [{"op": "describe", "name": "r", "text": 5}]):
        with pytest.raises(models.ModelError):
            edit(operations)


def test_seed_zero_and_paths(models_dir, lsd_root):
    models.copy_model("Literature/Richardson_2", "rich")
    with pytest.raises(models.ModelError) as err:
        structure.edit_structure("rich", "Sim1", [{"op": "add_object", "parent": "Root", "name": "Q"}])
    assert "SEED" in str(err.value)
    for bad in ("../x", "/etc"):
        with pytest.raises(models.ModelError):
            structure.edit_structure(bad, "Sim1", [ROOT_OBJECT])
    with pytest.raises(models.ModelError):
        structure.edit_structure("rich", "Missing", [ROOT_OBJECT])


def test_equation_name_is_kept_when_lsd_cannot_find_the_file(models_dir, lsd_root):
    structure.create_model("eq")
    (models_dir / "eq" / "fun_eq.cpp").rename(models_dir / "eq" / "fun_renamed.cpp")
    structure.edit_structure("eq", "Sim1", [ROOT_OBJECT])
    assert lsdfile.parse(models_dir / "eq" / "Sim1.lsd").settings["EQUATION"] == "fun_eq.cpp"


# --- no-op round trip over the shipped examples -------------------------------------

def snapshot(parsed):
    """Everything about a configuration that is data: objects, instance counts,
    elements (kind, lags, flags) and their values."""
    items = []
    for name, obj in parsed.objects.items():
        items.append((name, obj.parent, obj.instances, obj.blocks, obj.computed))
        for e in obj.elements:
            items.append((name, e.name, e.kind, e.lags, e.flag, repr(e.values)))
    return items, sorted(parsed.settings.items())


def test_noop_round_trip_loses_no_data_on_the_shipped_examples(lsd_root, tmp_path):
    files = sorted(glob.glob(str(lsd_root / "Example" / "**" / "*.lsd"), recursive=True))
    identical = unloadable = 0
    for name in files:
        path = Path(name)
        if models.seed_problem(path):
            unloadable += 1  # LSD itself cannot load a configuration with SEED 0
            continue
        text = structure.run_edit(path.parent, [], path)
        out = tmp_path / "out.lsd"
        lsdfile.write_text(out, text)
        before, after = snapshot(lsdfile.parse(path)), snapshot(lsdfile.parse(out))
        if path.parent.joinpath(lsdfile.parse(path).settings.get("EQUATION", "-")).exists():
            assert before == after, name
        else:  # the equation file is not beside the configuration: LSD blanks its name
            assert before[0] == after[0], name
        identical += lsdfile.read_text(path) == text
    assert unloadable == 27 and len(files) == 178
    assert identical >= 30  # the rest differ only in layout (see the README)
