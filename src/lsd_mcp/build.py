"""Compile LSD engine objects, the command-line utilities and model programs."""

import hashlib
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .runner import run

SHIM = Path(__file__).with_name("shim.cpp")
ENGINE = ["common", "lsdmain", "file", "nets", "object", "util", "variab"]
UTIL_ENGINE = ["common", "file", "nets", "object", "util", "variab"]
# utility -> (source file, engine objects to link, needs the shim)
UTILITIES = {
    "lsd_confgen": ("confgen", UTIL_ENGINE, True),
    "lsd_getsaved": ("getsaved", UTIL_ENGINE, True),
    "lsd_getlimits": ("getlimits", UTIL_ENGINE, True),
    "lsd_mcstats": ("mcstats", ["common"], False),
}

_lock = threading.Lock()


class BuildError(Exception):
    pass


@dataclass
class ModelBuild:
    ok: bool
    seconds: float = 0.0
    exe: Path = None
    cached: bool = False
    errors: list = field(default_factory=list)


def compiler() -> str:
    for name in ("c++", "g++", "clang++"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _need_compiler() -> str:
    cc = compiler()
    if not cc:
        raise BuildError("no C++ compiler found (looked for c++, g++, clang++)")
    return cc


def _key(root: Path) -> str:
    return hashlib.sha1(str(root).encode()).hexdigest()[:10]


def build_dir(root: Path) -> Path:
    return config.home() / "build" / _key(root)


def _compile_many(jobs, cwd):
    """jobs: list of (command, output path). Raises BuildError on failure."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: run(job[0], cwd=cwd, timeout=600), jobs))
    for job, result in zip(jobs, results):
        if not result.ok:
            raise BuildError("compiling %s failed:\n%s" % (job[1].name, result.output[-1500:]))


def _flags(root: Path, nw_only: bool, optimise: str) -> list:
    flags = ["-std=c++14", "-w", "-D_NW_"]
    if not nw_only:
        flags.append("-D_NP_")
    return flags + [optimise, "-I" + str(root / "src")]


def engine_objects(root: Path) -> Path:
    """Build the seven model-independent engine objects once per LSD root."""
    out = build_dir(root) / "engine"
    with _lock:
        missing = [n for n in ENGINE if not (out / (n + ".o")).is_file()]
        if not missing:
            return out
        cc = _need_compiler()
        out.mkdir(parents=True, exist_ok=True)
        jobs = []
        for name in missing:
            target = out / (name + ".o")
            command = [cc] + _flags(root, True, "-O3")
            command += ["-c", root / "src" / (name + ".cpp"), "-o", target]
            jobs.append((command, target))
        _compile_many(jobs, out)
    return out


def utilities(root: Path) -> dict:
    """Build the command-line utilities; return name -> executable path."""
    out = build_dir(root) / "util"
    exes = {}
    for name in UTILITIES:
        exes[name] = out / name
    with _lock:
        missing = [n for n in UTILITIES if not exes[n].is_file()]
        if not missing:
            return exes
        cc = _need_compiler()
        out.mkdir(parents=True, exist_ok=True)
        flags = _flags(root, False, "-O2")
        # Utility objects need their own engine objects built with -D_NP_.
        jobs = []
        needed = set()
        for name in missing:
            needed.update(UTILITIES[name][1])
        for obj in sorted(needed):
            target = out / (obj + ".o")
            if not target.is_file():
                jobs.append(([cc] + flags + ["-c", root / "src" / (obj + ".cpp"), "-o", target], target))
        for source in ["shim.cpp"] + [UTILITIES[n][0] + ".cpp" for n in missing]:
            base = SHIM if source == "shim.cpp" else root / "src" / source
            target = out / (Path(source).stem + ".o")
            jobs.append(([cc] + flags + ["-c", base, "-o", target], target))
        _compile_many(jobs, out)
        for name in missing:
            source, objs, needs_shim = UTILITIES[name]
            command = [cc, out / (source + ".o")]
            if needs_shim:
                command.append(out / "shim.o")
            for obj in objs:
                command.append(out / (obj + ".o"))
            command += ["-lz", "-lpthread", "-o", exes[name]]
            result = run(command, cwd=out, timeout=300)
            if not result.ok:
                raise BuildError("linking %s failed:\n%s" % (name, result.output[-1500:]))
    return exes


def utilities_built(root: Path) -> bool:
    out = build_dir(root) / "util"
    for name in UTILITIES:
        if not (out / name).is_file():
            return False
    return True


# --- model programs -------------------------------------------------------

def read_model_options(model_dir: Path) -> dict:
    options = {}
    path = model_dir / "model_options.txt"
    if not path.is_file():
        return options
    for line in path.read_text(errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            options[key.strip()] = value.strip()
    return options


def equation_file(model_dir: Path):
    """Path of the equation file, or None if the folder has none."""
    fun = read_model_options(model_dir).get("FUN")
    if fun:
        candidate = model_dir / (fun if fun.endswith(".cpp") else fun + ".cpp")
        if candidate.is_file():
            return candidate
    found = sorted(model_dir.glob("fun_*.cpp"))
    if len(found) >= 1:
        return found[0]
    return None


def _model_inputs(model_dir: Path, eq: Path) -> list:
    """Everything a change in which must trigger a rebuild: the equation file,
    FUN_EXTRA, and every source or header in the folder (the equation file may
    #include them)."""
    files = [eq]
    for name in read_model_options(model_dir).get("FUN_EXTRA", "").split():
        extra = model_dir / name
        if extra.is_file() and extra not in files:
            files.append(extra)
    for path in sorted(model_dir.iterdir()):
        if path.is_file() and path.suffix in (".cpp", ".h", ".hpp") and path not in files:
            files.append(path)
    return files


def _extra_flags(model_dir: Path) -> list:
    flags = []
    for flag in read_model_options(model_dir).get("SWITCH_CC", "").split():
        if flag != "-g":
            flags.append(flag)
    return flags


def _signature(inputs, flags) -> str:
    digest = hashlib.sha1()
    for path in inputs:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(" ".join(flags).encode())
    return digest.hexdigest()


def model_build_dir(root: Path, model_dir: Path) -> Path:
    key = hashlib.sha1(str(model_dir.resolve()).encode()).hexdigest()[:12]
    return build_dir(root) / "models" / (model_dir.name + "-" + key)


ERROR_LINE = re.compile(r"^(.+?):(\d+)(?::\d+)?: (?:fatal )?error: (.*)$")
NOTE_LINE = re.compile(r"^(.+?):(\d+)(?::\d+)?: note: (.*)$")


def _under(path: str, folder: Path, base: Path) -> bool:
    try:
        (base / path).resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _equation_location(lines, start, src_dir, base):
    """First note after an error that points outside LSD's own headers.

    g++ writes "file:line:col: note: in expansion of macro 'X'" for the place
    in the equation file; clang writes "note: expanded from macro 'X'" for the
    macro and a plain error line at the use site, so it rarely needs this.
    """
    for line in lines[start + 1:]:
        line = line.strip()
        if ERROR_LINE.match(line):
            break
        note = NOTE_LINE.match(line)
        if note and not _under(note.group(1), src_dir, base):
            return "%s:%s, %s" % (Path(note.group(1)).name, note.group(2), note.group(3))
    return None


def parse_errors(text: str, limit: int = 30, src_dir: Path = None, base: Path = None) -> list:
    """Pick compiler diagnostics (file:line: message) out of the output.

    Errors located in LSD's headers (src_dir) get the equation-file location
    from the compiler's notes, or a hint if the compiler gives none.
    """
    lines = text.splitlines()
    errors = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        match = ERROR_LINE.match(line)
        if match:
            entry = "%s:%s: %s" % (Path(match.group(1)).name, match.group(2), match.group(3))
            if src_dir is not None and _under(match.group(1), src_dir, base or Path(".")):
                where = _equation_location(lines, index, src_dir, base or Path("."))
                if where:
                    entry += " [in equation file: %s]" % where
                else:
                    entry += (" [inside LSD's macros; usually a syntax error just before "
                              "the reported EQUATION or RESULT]")
            errors.append(entry)
        elif "undefined reference" in line or "ld: " in line or "Undefined symbols" in line:
            errors.append(line)
        if len(errors) >= limit:
            break
    if not errors and text.strip():
        errors = text.strip().splitlines()[:limit]
    return errors


def compile_model(root: Path, model_dir: Path) -> ModelBuild:
    eq = equation_file(model_dir)
    if eq is None:
        return ModelBuild(False, errors=["no equation file (fun_*.cpp) in %s" % model_dir])
    cc = _need_compiler()
    objects = engine_objects(root)
    out = model_build_dir(root, model_dir)
    out.mkdir(parents=True, exist_ok=True)
    exe = out / "lsdNW"
    flags = _extra_flags(model_dir)
    signature = _signature(_model_inputs(model_dir, eq), flags)
    stamp = out / "signature"
    if exe.is_file() and stamp.is_file() and stamp.read_text() == signature:
        return ModelBuild(True, exe=exe, cached=True)

    started = time.time()
    if stamp.exists():
        stamp.unlink()
    model_obj = out / "model.o"
    command = [cc, "-std=c++14", "-w", "-D_NW_", "-O3", "-I" + str(root / "src")]
    command += flags + ["-c", eq, "-o", model_obj]
    result = run(command, cwd=model_dir, timeout=900)
    if not result.ok:
        return ModelBuild(False, time.time() - started, errors=parse_errors(result.output, 30, root / "src", model_dir))
    link = [cc, model_obj]
    for name in ENGINE:
        link.append(objects / (name + ".o"))
    link += ["-lz", "-lpthread", "-o", exe]
    result = run(link, cwd=out, timeout=300)
    if not result.ok:
        return ModelBuild(False, time.time() - started, errors=parse_errors(result.output))
    stamp.write_text(signature)
    return ModelBuild(True, time.time() - started, exe=exe)
