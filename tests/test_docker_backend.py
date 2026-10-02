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
