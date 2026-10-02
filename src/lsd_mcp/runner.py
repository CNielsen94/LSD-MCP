"""The one place where subprocesses are started.

Every other module goes through run(), so the Docker backend only has to add
its own commands here, not new ways of starting processes.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    returncode: int
    output: str  # stdout, and stderr too unless separate_stderr was asked
    timed_out: bool = False
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def run(command, cwd=None, timeout=None, input=None, separate_stderr=False) -> Result:
    """input: text for stdin (None means no stdin)."""
    command = [str(part) for part in command]
    cwd = str(cwd) if isinstance(cwd, Path) else cwd
    options = {"cwd": cwd, "stdout": subprocess.PIPE, "timeout": timeout,
               "stderr": subprocess.PIPE if separate_stderr else subprocess.STDOUT}
    if input is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = input.encode()
    try:
        done = subprocess.run(command, **options)
    except subprocess.TimeoutExpired as err:
        text = (err.stdout or b"").decode("utf-8", "replace")
        return Result(-1, text, timed_out=True)
    except FileNotFoundError as err:
        return Result(127, "command not found: %s" % err.filename)
    return Result(done.returncode, done.stdout.decode("utf-8", "replace"),
                  stderr=(done.stderr or b"").decode("utf-8", "replace"))
