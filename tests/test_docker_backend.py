"""Tests for the Docker backend, against a throwaway container.

Never touches the container named 'lsd'. Skipped when docker or the image is
missing.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from lsd_mcp import backend

IMAGE = "lsd-desktop:8.1-stable-5"
DATA = Path(__file__).parent / "data"


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    done = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return done.returncode == 0


pytestmark = pytest.mark.skipif(not docker_ready(), reason="docker or the LSD image is not available")


@pytest.fixture(scope="module")
def container(tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    work.chmod(0o777)
    name = "lsd-mcp-test-" + uuid.uuid4().hex[:8]
    started = subprocess.run(
        ["docker", "run", "-d", "--name", name, "-v", "%s:/home/lsd/LSD/Work" % work, IMAGE],
        capture_output=True, text=True)
    assert started.returncode == 0, started.stderr
    patch = pytest.MonkeyPatch()
    patch.setenv("LSD_MCP_BACKEND", "docker")
    patch.setenv("LSD_CONTAINER", name)
    try:
        yield name, work
    finally:
        patch.undo()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def call(tool, **arguments):
    result = backend.call(tool, arguments)
    assert not (isinstance(result, dict) and "error" in result), result["error"]
    return result


def test_status_reports_docker_and_r(container):
    name, _ = container
    info = call("lsd_status")
    assert info["backend"] == "docker" and info["container"] == name
    assert info["rscript"] and info["lsdsensitivity"]
    assert info["models_folder"] == "/home/lsd/LSD/Work"


def test_list_examples(container):
    names = [item["model"] for item in call("list_models", group="examples")]
    assert "Literature/Coordination" in names


def test_copy_set_run_read(container):
    _, work = container
    info = call("copy_model", source="Literature/Coordination", name="coor")
    assert "modelinfo.txt" in info["files"]
    assert (work / "coor" / "Single.lsd").is_file()
    assert (work / "coor" / "modelinfo.txt").is_file()
    assert call("set_run_settings", model="coor", config="Single", steps=50)["run_settings"]["MAX_STEP"] == "50"
    assert call("set_values", model="coor", config="Single", values={"c1": 0.9})["set"] == {"c1": 0.9}
    result = call("run_configuration", model="coor", config="Single", seed=3)
    assert result["ok"], result
    assert (work / "coor" / "Single_3.res.gz").is_file()
    series = call("read_results", model="coor", results_file="Single_3.res.gz",
                  variables=["Mean"], max_points=5)
    assert len(series["steps"]) == 5 and series["steps"][-1] == 50


def test_compile_error_has_line_number(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "broken")
    source = (work / "broken" / "fun_Linear.cpp").read_text().replace("RESULT( ( ( 2.0", "RESULT( ( ( 2.0 +* ", 1)
    (work / "broken" / "fun_Linear.cpp").write_text(source)
    line = [i for i, text in enumerate(source.splitlines(), 1) if "+*" in text][0]
    result = call("compile_model", model="broken")
    assert result["ok"] is False
    assert any(error.startswith("fun_Linear.cpp:%d:" % line) for error in result["errors"]), result


def test_linear_sensitivity_flow(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "linear")
    assert call("set_saved", model="linear", config="Linear", names=["Z"])["saved"] == ["Z"]
    design = call("sa_create_design", model="linear", config="Linear",
                  factors={"a": [0, 1], "b": [2, 3], "c": [-1, 1]},
                  samples=30, validation_samples=10, seed=1)
    outcome = call("sa_run_design", model="linear", config="Linear")
    assert outcome["ok"], outcome
    result = call("sa_analyze", model="linear", config="Linear", variable="Z")
    assert result["ok"], result
    direct = {row["factor"]: row["direct"] for row in result["sobol"]}
    assert direct["a"] == pytest.approx(4 / 13, abs=0.03)
    assert direct["b"] == pytest.approx(9 / 13, abs=0.03)
    assert abs(direct["c"]) < 0.03


def stamp_in_container(name):
    done = subprocess.run(["docker", "exec", name, "cat", backend.CONTAINER_STAMP],
                          capture_output=True, text=True)
    return done.stdout.strip()


def test_code_is_resynced_when_the_hash_changes(container, monkeypatch):
    name, _ = container
    call("lsd_status")
    assert stamp_in_container(name) == backend.package_hash()
    monkeypatch.setattr(backend, "package_hash", lambda: "changed")
    call("lsd_status")
    assert stamp_in_container(name) == "changed"
    monkeypatch.undo()
    call("lsd_status")
    assert stamp_in_container(name) == backend.package_hash()
    # a missing stamp (recreated container) also triggers a copy
    subprocess.run(["docker", "exec", "-u", "root", name, "rm", "-f", backend.CONTAINER_STAMP])
    call("lsd_status")
    assert stamp_in_container(name) == backend.package_hash()


def test_missing_container_gives_clear_error(monkeypatch):
    monkeypatch.setenv("LSD_MCP_BACKEND", "docker")
    monkeypatch.setenv("LSD_CONTAINER", "lsd-mcp-no-such-container")
    result = backend.call("lsd_status", {})
    assert "does not exist" in result["error"] and "run.sh" in result["error"]


def test_polynomial_negative_means_message_and_kriging_still_works(container):
    _, work = container
    shutil.copytree(DATA / "noisy", work / "noisy")
    call("set_saved", model="noisy", config="Noisy", names=["Y"])
    call("sa_create_design", model="noisy", config="Noisy",
         factors={"a": [0, 1], "b": [2, 3]}, samples=20, validation_samples=8, seed=1)
    assert call("sa_run_design", model="noisy", config="Noisy")["ok"]
    poly = call("sa_analyze", model="noisy", config="Noisy", variable="Y", metamodel="polynomial")
    assert poly["ok"] is False
    assert "negative mean" in poly["message"] and "negative weights" in poly["r_message"]
    kriging = call("sa_analyze", model="noisy", config="Noisy", variable="Y", metamodel="kriging")
    assert kriging["ok"], kriging
    direct = {row["factor"]: row["direct"] for row in kriging["sobol"]}
    assert direct["a"] == pytest.approx(0.5, abs=0.1)
    assert direct["b"] == pytest.approx(0.5, abs=0.1)


def test_analysis_is_reproducible_and_failures_keep_old_results(container):
    _, work = container
    shutil.copytree(DATA / "noisy", work / "noisy2")
    call("set_saved", model="noisy2", config="Noisy", names=["Y"])
    call("sa_create_design", model="noisy2", config="Noisy",
         factors={"a": [0, 1], "b": [2, 3]}, samples=20, validation_samples=8, seed=1)
    assert call("sa_run_design", model="noisy2", config="Noisy")["ok"]
    first = call("sa_analyze", model="noisy2", config="Noisy", variable="Y")
    again = call("sa_analyze", model="noisy2", config="Noisy", variable="Y")
    assert first["ok"] and first["sobol"] == again["sobol"] and first["fit"] == again["fit"]
    saved = (work / "noisy2" / "Noisy_sa" / "Y-kriging" / "sobol.csv").read_text()
    failed = call("sa_analyze", model="noisy2", config="Noisy", variable="Y", metamodel="polynomial")
    assert failed["ok"] is False
    assert (work / "noisy2" / "Noisy_sa" / "Y-kriging" / "sobol.csv").read_text() == saved
    assert not list((work / "noisy2").rglob("error.txt"))


def test_gcc_reports_equation_location_for_macro_error(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "macro")
    bad = ('#include "fun_head.h"\n\nMODELBEGIN\n\nEQUATION( "Z" )\nv[0] = V( "a" )\n'
           'RESULT( v[0] )\n\nMODELEND\n\nvoid close_sim( void )\n{\n}\n')
    (work / "macro" / "fun_Linear.cpp").write_text(bad)
    result = call("compile_model", model="macro")
    assert result["ok"] is False
    assert "fun_head.h:187" in result["errors"][0]
    # g++ may print typographic quotes depending on the container's locale
    assert "[in equation file: fun_Linear.cpp:7, in expansion of macro" in result["errors"][0]
    assert "RESULT" in result["errors"][0]


def test_variable_starting_with_underscore_can_be_analysed(container):
    _, work = container
    assert call("copy_model", source="SantAnna/Industry", name="ind")["model"] == "ind"
    call("set_run_settings", model="ind", config="MarkI-Beta", steps=60)
    call("sa_create_design", model="ind", config="MarkI-Beta",
         factors={"Mu": [0.02, 0.1], "MuMax": [0.1, 0.3], "BetaBeta": [3, 8]},
         samples=12, validation_samples=4)
    assert call("sa_run_design", model="ind", config="MarkI-Beta")["ok"]
    result = call("sa_analyze", model="ind", config="MarkI-Beta", variable="_s")
    assert result["ok"], result
    assert sorted(row["factor"] for row in result["sobol"]) == ["BetaBeta", "Mu", "MuMax"]
    assert result["note"].startswith("analysed the first of ") and "_s" in result["note"]
    assert "Linear" not in result["files"] and "_s-kriging" in result["files"]


def test_constant_response_is_reported(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "const")
    call("set_saved", model="const", config="Linear", names=["Z"])
    # Z = 2a - 3b + 7 does not depend on c or n
    call("sa_create_design", model="const", config="Linear",
         factors={"c": [-1, 1], "n": [1, 9, "int"]}, samples=10, validation_samples=4)
    assert call("sa_run_design", model="const", config="Linear")["ok"]
    result = backend.call("sa_analyze", dict(model="const", config="Linear", variable="Z"))
    assert result["ok"] is False and "does not vary over the design" in result["message"]


def test_kriging_numerical_failure_is_explained(container):
    """Industry with two binary integer factors and 24 LHS points: the points fall
    on four lines and the Kriging covariance matrix is not positive definite."""
    _, work = container
    call("copy_model", source="SantAnna/Industry", name="bin")
    call("set_run_settings", model="bin", config="MarkI-Beta", steps=40)
    design = call("sa_create_design", model="bin", config="MarkI-Beta",
                  factors={"EntrReg": [1, 2, "int"], "MktReg": [0, 1, "int"], "Mu": [0.04, 0.06]},
                  samples=24, validation_samples=4)
    assert "EntrReg has 2 levels for 24 samples" in design["warning"]
    assert call("sa_run_design", model="bin", config="MarkI-Beta")["ok"]
    result = call("sa_analyze", model="bin", config="MarkI-Beta", variable="HHI")
    assert result["ok"] is False, result
    assert "not positive definite" in result["message"] and "leading minor" in result["r_message"]


# --- NOLH and elementary effects (LSD's design code, on Linux) ------------------

GUI = DATA / "doe_gui"
CONTAINER_WORK = "/home/lsd/LSD/Work"


def python_in_container(name, code):
    """Run Python with the copied lsd_mcp package inside the container."""
    done = subprocess.run(
        ["docker", "exec", "-e", "PYTHONPATH=" + backend.CONTAINER_PKG, "-e", "PYTHONDONTWRITEBYTECODE=1",
         "-e", "LSDROOT=/home/lsd/LSD", "-e", "LSD_MCP_HOME=/home/lsd/.cache/lsd-mcp",
         name, "python3", "-c", code],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def make_in_container(name, folder, *modes):
    """Run lsd_doe once per mode (a list of its options) in a model folder."""
    code = ("from pathlib import Path\nfrom lsd_mcp import sa\n"
            "for options in %r:\n    sa.run_doe(Path(%r), 'Linear', *options)\n"
            % (list(modes), CONTAINER_WORK + "/" + folder))
    python_in_container(name, code)


def same_as_reference(made, reference):
    files = sorted(path.name for path in reference.iterdir())
    assert files
    for filename in files:
        assert (made / filename).read_bytes() == (reference / filename).read_bytes(), filename


def test_designs_are_byte_identical_to_the_interface_on_linux(container):
    name, work = container
    call("lsd_status")  # copies the package into the container
    for folder, modes, reference in (
            ("ref_nolh", [["-m", "nolh"], ["-m", "mc", "-n", "10", "-i", "18"]], "nolh_append"),
            ("ref_mc", [["-m", "mc", "-n", "10"]], "mc"),
            ("ref_ee", [["-m", "ee"]], "ee")):
        shutil.copytree(GUI / "baseline", work / folder)
        make_in_container(name, folder, *modes)
        same_as_reference(work / folder, GUI / reference)
    same_as_reference(work / "ref_nolh", GUI / "nolh")


def test_ee_design_analysis_gives_the_model_coefficients(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "lin_ee")
    call("set_saved", model="lin_ee", config="Linear", names=["Z"])
    design = call("sa_create_design", model="lin_ee", config="Linear",
                  factors={"a": [0, 1], "b": [2, 3], "c": [-1, 1]}, method="ee", seed=3)
    assert (design["points"], design["validation_points"]) == (40, 0)
    assert call("sa_run_design", model="lin_ee", config="Linear")["ok"]
    result = call("sa_analyze", model="lin_ee", config="Linear", variable="Z")
    assert result["ok"] and result["metamodel"] == "ee", result
    assert (result["levels"], result["jump"]) == (4, 2)
    mu = {row["factor"]: row["mu"] for row in result["effects"]}
    assert mu["a"] == pytest.approx(2, abs=1e-3)
    assert mu["b"] == pytest.approx(-3, abs=1e-3)
    assert mu["c"] == pytest.approx(0, abs=1e-3)
    star = {row["factor"]: row["mu_star"] for row in result["effects"]}
    assert star["b"] == pytest.approx(3, abs=1e-3)
    assert [row["factor"] for row in result["effects"]] == ["b", "a", "c"]
    assert set(result["effects"][0]) == {"factor", "mu", "mu_star", "sigma", "se", "p_value"}
    assert (work / "lin_ee" / "Linear_sa" / "Z-ee" / "ee.csv").is_file()
    bad = backend.call("sa_analyze", dict(model="lin_ee", config="Linear", variable="Z", metamodel="kriging"))
    assert "elementary effects design" in bad["error"]
    # the same analysis for a design without our design file, as made in LSD's interface
    (work / "lin_ee" / "Linear_design.json").unlink()
    again = call("sa_analyze", model="lin_ee", config="Linear", variable="Z", metamodel="ee",
                 levels=4, jump=2)
    assert again["effects"] == result["effects"]


def test_nolh_design_kriging_gives_the_direct_effects(container):
    _, work = container
    shutil.copytree(DATA / "linear", work / "lin_nolh")
    call("set_saved", model="lin_nolh", config="Linear", names=["Z"])
    design = call("sa_create_design", model="lin_nolh", config="Linear",
                  factors={"a": [0, 1], "b": [2, 3], "c": [-1, 1]}, method="nolh",
                  validation_samples=20, seed=1)
    assert design["points"] == 17
    assert (work / "lin_nolh" / "Linear_design.json").is_file()
    assert call("sa_run_design", model="lin_nolh", config="Linear")["ok"]
    result = call("sa_analyze", model="lin_nolh", config="Linear", variable="Z")
    assert result["ok"] and result["metamodel"] == "kriging", result
    direct = {row["factor"]: row["direct"] for row in result["sobol"]}
    assert direct["a"] == pytest.approx(4 / 13, abs=0.03)
    assert direct["b"] == pytest.approx(9 / 13, abs=0.03)
    bad = backend.call("sa_analyze", dict(model="lin_nolh", config="Linear", variable="Z", metamodel="ee"))
    assert "method 'nolh'" in bad["error"]
