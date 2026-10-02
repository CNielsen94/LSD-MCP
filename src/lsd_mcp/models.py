"""List, read, write and copy models; all path checks live here."""

import re
import shutil
import tempfile
from pathlib import Path

from . import build, config, lsdfile, lsdsource
from .runner import run

GROUPS = ("models", "examples")
# Files that are build or run output, not model source.
SKIP_SUFFIXES = (".o", ".exe", ".res", ".gz", ".tot", ".log", ".bak", ".so",
                 ".dll", ".dylib", ".a", ".zip", ".RData")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]+$")


class ModelError(Exception):
    pass


def group_root(group: str) -> Path:
    if group == "models":
        return config.models_dir()
    if group == "examples":
        return lsdsource.lsd_root() / "Example"
    raise ModelError("group must be 'models' or 'examples', got %r" % group)


def _relative_parts(relative: str, what: str) -> list:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise ModelError("%s must be a relative path inside its folder: %r" % (what, relative))
    parts = []
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or not NAME_PATTERN.match(part):
            raise ModelError("%s has an invalid path component %r" % (what, part))
        parts.append(part)
    if not parts:
        raise ModelError("%s is empty" % what)
    return parts


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_model(model: str, group: str = "models") -> Path:
    """Folder of an existing model; refuses anything that escapes the group root."""
    root = group_root(group)
    path = root.joinpath(*_relative_parts(model, "model"))
    if not _inside(path, root):
        raise ModelError("model path escapes %s" % root)
    if not path.is_dir():
        raise ModelError("no such model folder in %s: %s" % (group, model))
    return path.resolve()


def resolve_writable(model: str) -> Path:
    """Model folder inside LSD_MODELS; never anything in LSD's own folders.

    The models folder may itself sit inside LSDROOT (the Docker setup uses
    LSDROOT/Work), so only src, Example and Rpkg are protected.
    """
    path = resolve_model(model, "models")
    root = lsdsource_root_or_none()
    for protected in ("src", "Example", "Rpkg"):
        if _inside(path, root / protected):
            raise ModelError("refusing to write under the LSD source tree")
    return path


def lsdsource_root_or_none() -> Path:
    try:
        return lsdsource.lsd_root()
    except lsdsource.LsdSourceError:
        return Path("/nonexistent-lsd-root")


def config_name(config_file: str) -> str:
    """Base name of a configuration, accepting 'Single' or 'Single.lsd'."""
    name = config_file[:-4] if config_file.endswith(".lsd") else config_file
    if not name or not NAME_PATTERN.match(name) or name.startswith("."):
        raise ModelError("invalid configuration name %r" % config_file)
    return name


def config_path(model_dir: Path, config_file: str) -> Path:
    path = model_dir / (config_name(config_file) + ".lsd")
    if not path.is_file():
        raise ModelError("no configuration %s.lsd in %s" % (config_name(config_file), model_dir.name))
    return path


def is_model(folder: Path) -> bool:
    if build.equation_file(folder) is None:
        return False
    return len(list(folder.glob("*.lsd"))) > 0


def find_models(root: Path) -> list:
    found = []
    for folder in sorted(set(path.parent for path in root.rglob("*.lsd"))):
        if is_model(folder):
            found.append(folder)
    return found


def model_info(root: Path, folder: Path) -> dict:
    title = ""
    info = folder / "modelinfo.txt"
    if info.is_file():
        for line in info.read_text(errors="replace").splitlines():
            if line.strip():
                title = line.strip()
                break
    eq = build.equation_file(folder)
    configs = sorted(path.stem for path in folder.glob("*.lsd"))
    return {
        "model": folder.relative_to(root).as_posix(),
        "title": title,
        "equation_file": eq.name if eq else None,
        "configurations": configs,
    }


def list_models(group: str = "models") -> list:
    root = group_root(group)
    result = []
    for folder in find_models(root):
        result.append(model_info(root, folder))
    return result


def read_equations(model: str, group: str = "models") -> str:
    folder = resolve_model(model, group)
    eq = build.equation_file(folder)
    if eq is None:
        raise ModelError("model has no equation file")
    return eq.read_text(errors="replace")


def copy_model(source: str, name: str, source_group: str = "examples") -> dict:
    src = resolve_model(source, source_group)
    target_root = config.models_dir()
    target = target_root.joinpath(*_relative_parts(name, "name"))
    if not _inside(target, target_root):
        raise ModelError("target escapes the models folder")
    if target.exists():
        raise ModelError("%s already exists in the models folder" % name)
    copied = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name.endswith(SKIP_SUFFIXES):
            continue
        relative = path.relative_to(src)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied.append(relative.as_posix())
    # LMM (LSD's model manager) only lists folders that have a modelinfo.txt
    if not (target / "modelinfo.txt").exists():
        (target / "modelinfo.txt").write_text(name.split("/")[-1] + "\n")
        copied.append("modelinfo.txt")
    return {"model": name, "files": copied}


def write_equations(model: str, content: str) -> dict:
    folder = resolve_writable(model)
    eq = build.equation_file(folder)
    if eq is None:
        raise ModelError("model has no equation file")
    backup = eq.with_name(eq.name + ".bak")
    shutil.copy2(eq, backup)
    eq.write_text(content)
    return {"written": eq.name, "backup": backup.name}


def describe_configuration(model: str, config_file: str, group: str = "models") -> dict:
    folder = resolve_model(model, group)
    parsed = lsdfile.parse(config_path(folder, config_file))
    objects = []
    for obj in parsed.objects.values():
        elements = []
        for element in obj.elements:
            elements.append(_describe_element(element))
        objects.append({
            "object": obj.name,
            "parent": obj.parent,
            "instances": obj.instances,
            "elements": elements,
        })
    return {"objects": objects, "run_settings": parsed.settings}


def _describe_element(element) -> dict:
    entry = {
        "name": element.name,
        "type": element.kind,
        "lags": element.lags,
        "saved": element.saved,
    }
    values = element.values
    if values:
        low, high = min(values), max(values)
        if low == high:
            entry["value"] = low
        else:
            entry["min"] = low
            entry["max"] = high
            entry["count"] = len(values)
    return entry


# --- editing configurations ---------------------------------------------------

def _number_text(value: float) -> str:
    return format(float(value), ".15g")


def generate_configurations(root: Path, base: Path, names, columns) -> list:
    """Run lsd_confgen; return the text of each generated configuration.

    names: element names (one CSV row each). columns: one list of values per
    configuration, in the order of names.

    LSD 8.1-stable-5 can only generate as many configurations per call as the
    CSV has rows (change_configuration() in confgen.cpp compares the
    configuration number with the number of rows), so the work is split into
    calls of at most len(names) columns.
    """
    exes = build.utilities(root)
    texts = []
    with tempfile.TemporaryDirectory(prefix="lsd-confgen-") as tmp:
        tmp_path = Path(tmp)
        for start in range(0, len(columns), len(names)):
            chunk = columns[start:start + len(names)]
            rows = ["name," + ",".join("c%d" % (k + 1) for k in range(len(chunk)))]
            for index, name in enumerate(names):
                cells = [_number_text(column[index]) for column in chunk]
                rows.append(name + "," + ",".join(cells))
            csv_file = tmp_path / "config.csv"
            csv_file.write_text("\n".join(rows) + "\n")
            result = run([exes["lsd_confgen"], "-f", base, "-c", csv_file,
                          "-o", tmp_path / "gen"], cwd=tmp_path, timeout=300)
            if not result.ok:
                raise ModelError("lsd_confgen failed: " + result.output.strip()[-600:])
            for k in range(len(chunk)):
                # one column gives gen.lsd, several give gen_1.lsd, gen_2.lsd ...
                name = "gen.lsd" if len(chunk) == 1 else "gen_%d.lsd" % (k + 1)
                texts.append(lsdfile.read_text(tmp_path / name))
                (tmp_path / name).unlink()
    return texts


def _check_names(parsed, names, what="element"):
    unknown = []
    for name in names:
        if parsed.element(name) is None:
            unknown.append(name)
    if unknown:
        raise ModelError("unknown %s(s): %s" % (what, ", ".join(unknown)))


def set_values(model: str, config_file: str, values: dict, new_config: str = None) -> dict:
    folder = resolve_writable(model)
    path = config_path(folder, config_file)
    if not values:
        raise ModelError("values is empty")
    parsed = lsdfile.parse(path)
    _check_names(parsed, values)
    names = list(values)
    numbers = [values[name] for name in names]
    original = lsdfile.read_text(path)
    root = lsdsource.lsd_root()
    text = generate_configurations(root, path, names, [numbers])[0]
    text = lsdfile.restore_equation_line(text, original)
    text = lsdfile.restore_tail(text, original)
    target = path if new_config is None else folder / (config_name(new_config) + ".lsd")
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    lsdfile.write_text(target, text)
    return {"written": target.name, "set": values}


def set_run_settings(model: str, config_file: str, runs: int = None,
                     seed: int = None, steps: int = None) -> dict:
    folder = resolve_writable(model)
    path = config_path(folder, config_file)
    wanted = {}
    if runs is not None:
        wanted["SIM_NUM"] = int(runs)
    if seed is not None:
        wanted["SEED"] = int(seed)
    if steps is not None:
        wanted["MAX_STEP"] = int(steps)
    if not wanted:
        raise ModelError("give at least one of runs, seed, steps")
    lsdfile.write_text(path, lsdfile.set_settings(lsdfile.read_text(path), **wanted))
    return {"run_settings": lsdfile.parse(path).settings}


def set_saved(model: str, config_file: str, names, saved: bool = True) -> dict:
    folder = resolve_writable(model)
    path = config_path(folder, config_file)
    names = list(names)
    if not names:
        raise ModelError("names is empty")
    _check_names(lsdfile.parse(path), names)
    text, changed = lsdfile.set_save_flags(lsdfile.read_text(path), names, saved)
    lsdfile.write_text(path, text)
    return {"saved" if saved else "not_saved": changed}
