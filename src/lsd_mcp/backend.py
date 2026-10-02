"""Where a tool call runs: in this process (local) or inside a Docker container.

The Docker backend copies this package into the container and runs the same
standard-library code there with `python3 -m lsd_mcp.call TOOL`, so paths mean
the same thing to the tool and to the files it touches.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .runner import run

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_FILES = ("*.py", "shim.cpp", "doe.cpp", "sa_analysis.R")
CONTAINER_PKG = "/home/lsd/.lsd-mcp/pkg"
CONTAINER_STAMP = CONTAINER_PKG + "/lsd_mcp.stamp"
START_HINT = "start it with ./run.sh in the Docker_LSD_setup folder"


def backend_name() -> str:
    return "docker" if os.environ.get("LSD_MCP_BACKEND", "").lower() == "docker" else "local"


def container_name() -> str:
    return os.environ.get("LSD_CONTAINER") or "lsd"


def call(tool: str, arguments: dict):
    if backend_name() == "docker":
        result = call_docker(tool, arguments)
        label = {"backend": "docker", "container": container_name()}
    else:
        from . import tools
        result = tools.TOOLS[tool](**arguments)
        label = {"backend": "local"}
    if tool == "lsd_status" and isinstance(result, dict) and "error" not in result:
        result.update(label)
    return result


# --- Docker ------------------------------------------------------------------

def _docker(args, timeout=120, input=None):
    return run(["docker"] + list(args), timeout=timeout, input=input, separate_stderr=True)


def check_container():
    """Return an error message, or None if the container is running."""
    name = container_name()
    result = _docker(["inspect", "-f", "{{.State.Running}}", name], timeout=60)
    if result.returncode == 127:
        return "docker is not installed or not on PATH"
    text = (result.output + result.stderr).lower()
    if "cannot connect" in text or ("daemon" in text and not result.ok):
        return "the Docker daemon is not running; start Docker Desktop"
    if not result.ok:
        return "container %r does not exist; %s" % (name, START_HINT)
    if result.output.strip() != "true":
        return "container %r is not running; %s" % (name, START_HINT)
    return None


def package_hash() -> str:
    digest = hashlib.sha256()
    files = []
    for pattern in PACKAGE_FILES:
        files.extend(PACKAGE_DIR.glob(pattern))
    for path in sorted(files):
        digest.update(path.name.encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def sync_code():
    """Copy the package into the container unless the stamp matches. Returns an error or None."""
    name = container_name()
    wanted = package_hash()
    current = _docker(["exec", name, "cat", CONTAINER_STAMP], timeout=60)
    if current.ok and current.output.strip() == wanted:
        return None
    prepare = _docker(["exec", "-u", "root", name, "sh", "-c",
                       "rm -rf %s && mkdir -p %s" % (CONTAINER_PKG, CONTAINER_PKG)], timeout=60)
    if not prepare.ok:
        return "cannot prepare the code folder in the container: " + (prepare.output + prepare.stderr)[-300:]
    with tempfile.TemporaryDirectory(prefix="lsd-mcp-pkg-") as tmp:
        stage = Path(tmp) / "lsd_mcp"
        stage.mkdir()
        for pattern in PACKAGE_FILES:
            for path in PACKAGE_DIR.glob(pattern):
                shutil.copy2(path, stage / path.name)
        copied = _docker(["cp", str(stage), "%s:%s/" % (name, CONTAINER_PKG)], timeout=120)
    if not copied.ok:
        return "docker cp failed: " + (copied.output + copied.stderr)[-300:]
    stamped = _docker(["exec", "-i", "-u", "root", name, "tee", CONTAINER_STAMP],
                      timeout=60, input=wanted)
    if not stamped.ok:
        return "cannot write the stamp file: " + (stamped.output + stamped.stderr)[-300:]
    return None


def call_docker(tool: str, arguments: dict):
    problem = check_container()
    if problem:
        return {"error": problem}
    problem = sync_code()
    if problem:
        return {"error": problem}
    timeout_s = arguments.get("timeout_s")
    timeout = (timeout_s + 60) if isinstance(timeout_s, (int, float)) else 600
    command = [
        "exec", "-i",
        "-e", "PYTHONPATH=" + CONTAINER_PKG,
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "LSDROOT=" + os.environ.get("LSD_MCP_CONTAINER_LSDROOT", "/home/lsd/LSD"),
        "-e", "LSD_MODELS=" + os.environ.get("LSD_MCP_CONTAINER_MODELS", "/home/lsd/LSD/Work"),
        "-e", "LSD_MCP_HOME=/home/lsd/.cache/lsd-mcp",
        container_name(), "python3", "-m", "lsd_mcp.call", tool,
    ]
    result = _docker(command, timeout=timeout, input=json.dumps(arguments))
    if result.timed_out:
        return {"error": "timed out after %d s waiting for the container" % timeout}
    try:
        return json.loads(result.output)
    except ValueError:
        tail = (result.output + result.stderr)[-500:]
        return {"error": "container did not return JSON (exit %d): %s" % (result.returncode, tail)}
