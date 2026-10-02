"""List, read, write and copy models; all path checks live here."""

import re
import shutil
import tempfile
from pathlib import Path

from . import build, config, lsdfile, lsdsource
from .runner import run

GROUPS = ("models", "examples")
# Files that are build or run output, not model source.
SKIP_SUFFIXES = (".o", ".exe", ".res", ".gz", ".tot", ".log", ".bak", ".orig", ".so",
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
        raise ModelError("%s must name a folder inside its group, not the group root (%r)"
                         % (what, relative))
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


def config_path(model_dir: Path, config_file: str, model: str = None) -> Path:
    path = model_dir / (config_name(config_file) + ".lsd")
    if not path.is_file():
        raise ModelError("no configuration %s.lsd in %s" % (config_name(config_file), model or model_dir.name))
    return path


def seed_problem(path: Path):
    """Message if LSD cannot load this configuration because SEED < 1, else None.

    LSD's loader (file.cpp) rejects any configuration whose seed is not > 0.
    """
    text = parse_seed(path)
    if text is not None and text < 1:
        return ("%s has SEED %d; LSD cannot load a configuration whose seed is below 1. "
                "Fix it with set_run_settings(seed=1)." % (path.name, text))
    return None


def parse_seed(path: Path):
    try:
        return int(lsdfile.parse(path).settings.get("SEED", ""))
    except ValueError:
        return None


def require_loadable(path: Path):
    problem = seed_problem(path)
    if problem:
        raise ModelError(problem)


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


def natural_key(text: str) -> list:
    """Sort key that puts SAbase_2 before SAbase_10."""
    key = []
    for part in re.split(r"(\d+)", text):
        key.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return key


def model_info(root: Path, folder: Path) -> dict:
    title = ""
    info = folder / "modelinfo.txt"
    if info.is_file():
        for line in info.read_text(errors="replace").splitlines():
            if line.strip():
                title = line.strip()
                break
    eq = build.equation_file(folder)
    configs = sorted((path.stem for path in folder.glob("*.lsd")), key=natural_key)
    return {
        "model": folder.relative_to(root).as_posix(),
        "title": title,
        "equation_file": eq.name if eq else None,
        "source_files": source_files(folder),
        "configurations": configs,
    }


def list_models(group: str = "models") -> list:
    root = group_root(group)
    result = []
    for folder in find_models(root):
        result.append(model_info(root, folder))
    return result


SOURCE_SUFFIXES = (".cpp", ".h", ".hpp")


def source_files(folder: Path) -> list:
    """C/C++ sources and headers in the model folder (not recursive); the
    equation file first, the rest alphabetical."""
    names = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            names.append(path.name)
    eq = build.equation_file(folder)
    if eq is not None and eq.name in names:
        names.remove(eq.name)
        names.insert(0, eq.name)
    return names


def source_path(folder: Path, file) -> Path:
    """The equation file, or a source file of the model folder by plain name."""
    if file is None:
        eq = build.equation_file(folder)
        if eq is None:
            raise ModelError("model has no equation file")
        return eq
    if "/" in file or "\\" in file or file.startswith(".") or not NAME_PATTERN.match(file):
        raise ModelError("file must be a plain file name inside the model folder: %r" % file)
    if not file.endswith(SOURCE_SUFFIXES):
        raise ModelError("file must end in %s: %r" % (", ".join(SOURCE_SUFFIXES), file))
    return folder / file


def read_equations(model: str, group: str = "models", file: str = None) -> str:
    folder = resolve_model(model, group)
    path = source_path(folder, file)
    if not path.is_file():
        raise ModelError("no file %s in %s; source files: %s"
                         % (path.name, model, ", ".join(source_files(folder))))
    return path.read_text(errors="replace")


def is_design_output(path: Path, root: Path) -> bool:
    """Files that sa_create_design / sa_analyze generate next to a configuration:
    numbered configurations, design tables and the <config>_sa/ results.
    The .sa file is kept. Only names built on an existing configuration count."""
    configs = set(p.stem for p in path.parent.glob("*.lsd"))
    for folder in path.parents:
        if folder == root:
            break
        if folder.name.endswith("_sa") and (folder.parent / (folder.name[:-3] + ".lsd")).is_file():
            return True
    for config in configs:
        escaped = re.escape(config)
        if re.match(r"^%s_\d+_\d+\.csv$" % escaped, path.name):
            return True
        if re.match(r"^%s_\d+\.lsd$" % escaped, path.name) and (path.parent / (config + ".sa")).is_file():
            return True
    return False


def copy_model(source: str, name: str, source_group: str = "examples") -> dict:
    src = resolve_model(source, source_group)
    if not is_model(src):
        message = "%s is not a model folder (no equation file / no configuration at its top level)" % source
        inner = find_models(src)
        if inner:
            names = [folder.relative_to(group_root(source_group)).as_posix() for folder in inner[:5]]
            message += "; it contains models, for example: " + ", ".join(names)
        raise ModelError(message)
    target_root = config.models_dir()
    target = target_root.joinpath(*_relative_parts(name, "name"))
    if not _inside(target, target_root):
        raise ModelError("target escapes the models folder")
    if _inside(target, src):
        raise ModelError("the destination %s lies inside the source folder %s" % (name, source))
    for ancestor in target.parents:
        if ancestor == target_root.resolve() or not _inside(ancestor, target_root):
            break
        if ancestor.is_dir() and is_model(ancestor):
            raise ModelError("the destination %s lies inside the model folder %s"
                             % (name, ancestor.relative_to(target_root).as_posix()))
    if target.exists():
        raise ModelError("%s already exists in the models folder" % name)
    copied = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name.endswith(SKIP_SUFFIXES) or is_design_output(path, src):
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


def write_equations(model: str, content: str, file: str = None) -> dict:
    folder = resolve_writable(model)
    path = source_path(folder, file)
    if not path.exists():
        if file is None:
            raise ModelError("model has no equation file")
        path.write_text(content)
        return {"written": path.name, "created": True}
    backup = path.with_name(path.name + ".bak")  # the version before the latest write
    original = path.with_name(path.name + ".orig")  # the version before the first write
    shutil.copy2(path, backup)
    if not original.exists():
        shutil.copy2(path, original)
    path.write_text(content)
    return {"written": path.name, "backup": backup.name, "original": original.name}


def describe_configuration(model: str, config_file: str, group: str = "models",
                           object: str = None, detail: str = "full") -> dict:
    if detail not in ("full", "names"):
        raise ModelError("detail must be 'full' or 'names', got %r" % detail)
    folder = resolve_model(model, group)
    parsed = lsdfile.parse(config_path(folder, config_file, model))
    if object is not None and object not in parsed.objects:
        raise ModelError("no object %r; objects: %s" % (object, ", ".join(parsed.objects)))
    objects = []
    for obj in parsed.objects.values():
        if object is not None and obj.name != object:
            continue
        entry = {"object": obj.name, "parent": obj.parent, "instances": obj.instances}
        if not obj.computed:
            entry["computed"] = False
        if detail == "names":
            groups = {}
            for element in obj.elements:
                groups.setdefault(element.kind, []).append(element.name)
            entry["elements"] = groups
        else:
            entry["elements"] = [_describe_element(element) for element in obj.elements]
        objects.append(entry)
    info = {"objects": objects, "run_settings": parsed.settings}
    problem = seed_problem(config_path(folder, config_file, model))
    if problem:
        info["warning"] = problem
    return info


def _describe_element(element) -> dict:
    entry = {
        "name": element.name,
        "type": element.kind,
        "lags": element.lags,
        "saved": element.saved,
    }
    if element.debug in "dWR":
        entry["debug"] = True
    if element.plot in "pP":
        entry["plot"] = True
    if element.plot in "NP":
        entry["parallel"] = True
    if element.flag == "S":
        entry["saved_separately"] = True
    if element.lags > 1 and len(element.values) % element.lags == 0:
        _describe_lags(entry, element)
        return entry
    values = [value for value in element.values if value == value]  # drop NaN
    if values:
        low, high = min(values), max(values)
        if low == high:
            entry["value"] = low
        else:
            entry["min"] = low
            entry["max"] = high
            entry["count"] = len(values)
    return entry


def _describe_lags(entry: dict, element):
    """Values of a variable with several lags. They are stored instance by
    instance, all lags of one instance together."""
    lags = element.lags
    by_lag = []
    for lag in range(lags):
        by_lag.append([v for v in element.values[lag::lags] if v == v])
    if not by_lag[0]:
        return
    if all(len(set(values)) == 1 for values in by_lag):
        firsts = [values[0] for values in by_lag]
        if len(set(firsts)) == 1:
            entry["value"] = firsts[0]
        else:
            entry["values_by_lag"] = firsts
        return
    entry["ranges_by_lag"] = [{"min": min(values), "max": max(values)} for values in by_lag if values]
    entry["count_per_lag"] = len(by_lag[0])


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
    path = config_path(folder, config_file, model)
    if not values:
        raise ModelError("values is empty")
    require_loadable(path)
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
    result = {"written": target.name, "set": values, "replaced": target.exists()}
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        shutil.copy2(target, backup)
        result["backup"] = backup.name
    lsdfile.write_text(target, text)
    return result


def set_run_settings(model: str, config_file: str, runs: int = None,
                     seed: int = None, steps: int = None) -> dict:
    folder = resolve_writable(model)
    path = config_path(folder, config_file, model)
    wanted = {}
    for key, label, value in (("SIM_NUM", "runs", runs), ("SEED", "seed", seed),
                              ("MAX_STEP", "steps", steps)):
        if value is None:
            continue
        if int(value) < 1:
            raise ModelError("%s must be at least 1 (LSD rejects %s below 1), got %s"
                             % (label, label, value))
        wanted[key] = int(value)
    if not wanted:
        raise ModelError("give at least one of runs, seed, steps")
    lsdfile.write_text(path, lsdfile.set_settings(lsdfile.read_text(path), **wanted))
    return {"run_settings": lsdfile.parse(path).settings}


def set_saved(model: str, config_file: str, names, saved: bool = True) -> dict:
    folder = resolve_writable(model)
    path = config_path(folder, config_file, model)
    names = list(names)
    if not names:
        raise ModelError("names is empty")
    _check_names(lsdfile.parse(path), names)
    text, changed = lsdfile.set_save_flags(lsdfile.read_text(path), names, saved)
    lsdfile.write_text(path, text)
    return {"saved" if saved else "not_saved": changed}
