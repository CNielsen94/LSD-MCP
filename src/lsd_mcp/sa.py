"""Sensitivity analysis: design, batch run, R analysis.

Designs: Latin hypercube and uniform random points are sampled here and
written through LSD's lsd_confgen; near-orthogonal Latin hypercube (NOLH) and
elementary effects (EE) designs are made by LSD's own design code through
lsd_doe (doe.cpp). Analysis: a meta-model with Sobol indices, or elementary
effects, both by LSD's R package.

File layout follows what LSD's own interface writes (tests/data/doe_gui):
<config>.sa, the design tables <config>_1_S.csv and <config>_S+1_S+V.csv (EE
has only the first), and numbered configurations <config>_k.lsd. Result files
are <config>_k_<seed>.res.gz. <config>_design.json is ours: the method and its
parameters.
"""

import csv
import json
import os
import random
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import build, config, lsdfile, lsdsource, models
from . import run as results_reader
from .runner import run

R_SCRIPT = Path(__file__).with_name("sa_analysis.R")


def _parse_factors(parsed, factors: dict) -> list:
    """Return [(name, low, high, is_int)] after checking every entry."""
    if not factors:
        raise models.ModelError("factors is empty")
    found = []
    for name, spec in factors.items():
        element = parsed.element(name)
        if element is None:
            raise models.ModelError("unknown element %r" % name)
        if element.kind != "Param":
            raise models.ModelError("factor %r is a %s; only parameters can be factors" % (name, element.kind))
        if not isinstance(spec, (list, tuple)) or len(spec) not in (2, 3):
            raise models.ModelError("factor %r: give [min, max] or [min, max, \"int\"]" % name)
        low, high = float(spec[0]), float(spec[1])
        if not low < high:
            raise models.ModelError("factor %r: min must be below max" % name)
        is_int = len(spec) == 3
        if is_int and spec[2] != "int":
            raise models.ModelError("factor %r: third entry must be \"int\"" % name)
        found.append((name, low, high, is_int))
    return found


def _scale(unit: float, low: float, high: float, is_int: bool) -> float:
    value = low + unit * (high - low)
    if is_int:
        value = float(round(value))
    return round(value, 6)


def sample_points(factors, count: int, method: str, rng) -> list:
    """count points, each a list of values in factor order."""
    units = []  # units[f] = list of count numbers in [0, 1)
    for _ in factors:
        if method == "lhs":
            strata = list(range(count))
            rng.shuffle(strata)
            column = []
            for stratum in strata:
                column.append((stratum + rng.random()) / count)
        else:
            column = []
            for _ in range(count):
                column.append(rng.random())
        units.append(column)
    points = []
    for i in range(count):
        point = []
        for f, (name, low, high, is_int) in enumerate(factors):
            point.append(_scale(units[f][i], low, high, is_int))
        points.append(point)
    return points


def _design_files(folder: Path, name: str) -> dict:
    """Files of an existing design of this configuration.

    Returns {"definition": [...], "results": [...]}. Only definition files
    decide whether a design exists. Design point results carry one number
    more than the output of a plain run:
      design: <name>_<k>_<seed>.res.gz   <name>_<k>_<seed>_<seed>.tot.gz
      plain:  <name>_<seed>.res.gz       <name>_<first>_<last>.tot.gz
    so plain-run files are never matched here.
    """
    n = re.escape(name)
    definition = re.compile(r"^(%s\.sa|%s_design\.json|%s_\d+\.lsd|%s_\d+_\d+\.csv)$" % (n, n, n, n))
    results = re.compile(r"^(%s_\d+_\d+\.res(\.gz)?|%s_\d+_\d+_\d+\.tot(\.gz)?)$" % (n, n))
    found = {"definition": [], "results": []}
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if definition.match(path.name):
            found["definition"].append(path)
        elif results.match(path.name):
            found["results"].append(path)
    return found


def _format_csv(names, points) -> str:
    lines = [",".join(names)]
    for point in points:
        lines.append(",".join("%.6f" % value for value in point))
    return "\n".join(lines) + "\n"


METHODS = ("lhs", "random", "nolh", "ee")


def design_info_path(folder: Path, name: str) -> Path:
    return folder / (name + "_design.json")


def read_design_info(folder: Path, name: str):
    """The sidecar written by create_design, or None for a design made elsewhere."""
    path = design_info_path(folder, name)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        raise models.ModelError("%s is not valid JSON; delete it or recreate the design" % path.name)


def _check_ee_settings(trajectories, levels, jump, pool):
    """The checks LSD's interface makes before an elementary effects design."""
    if levels < 2 or levels % 2 != 0:
        raise models.ModelError("levels must be even and at least 2, got %s" % levels)
    if trajectories < 2:
        raise models.ModelError("trajectories must be at least 2, got %s" % trajectories)
    if pool < trajectories:
        raise models.ModelError("pool (%s) must be at least trajectories (%s)" % (pool, trajectories))
    if jump < 1:
        raise models.ModelError("jump must be at least 1, got %s" % jump)


def run_doe(folder: Path, name: str, *options) -> dict:
    """Run lsd_doe in the model folder on <name>.lsd and <name>.sa.

    Returns {"points": N, "first": F, "last": L}: LSD wrote N configurations
    numbered F to L and the design table <name>_F_L.csv.
    """
    exe = build.utilities(lsdsource.lsd_root())["lsd_doe"]
    result = run([exe, "-f", name + ".lsd", "-s", name + ".sa"] + list(options),
                 cwd=folder, timeout=600, separate_stderr=True)
    if not result.ok:
        raise models.ModelError("lsd_doe failed: " + (result.stderr or result.output).strip()[-600:])
    match = re.search(r"^points (\d+) first (\d+) last (\d+)$", result.output, re.M)
    if not match:
        raise models.ModelError("lsd_doe gave no summary line: " + result.output[-300:])
    return {"points": int(match.group(1)), "first": int(match.group(2)), "last": int(match.group(3))}


def _clear_design(folder: Path, name: str, overwrite: bool, keep_sa: bool):
    existing = _design_files(folder, name)
    old_files = existing["definition"]
    if keep_sa:
        old_files = [path for path in old_files if path.name != name + ".sa"]
    if old_files and not overwrite:
        raise models.ModelError("a design for %s already exists (%d files); pass overwrite=True to replace it"
                                % (name, len(old_files)))
    for old in old_files + existing["results"]:
        old.unlink()
    shutil.rmtree(folder / (name + "_sa"), ignore_errors=True)


def _write_runs_and_seeds(folder: Path, name: str, original: str, total: int,
                          runs_per_point: int, seed: int):
    """Every numbered configuration gets runs_per_point runs and its own seeds."""
    for k in range(1, total + 1):
        path = folder / ("%s_%d.lsd" % (name, k))
        text = lsdfile.read_text(path)
        text = lsdfile.restore_equation_line(text, original)
        text = lsdfile.set_settings(text, SIM_NUM=runs_per_point,
                                    SEED=seed + (k - 1) * runs_per_point)
        lsdfile.write_text(path, text)


def create_design(model, config_file, factors, samples=None, method="lhs",
                  validation_samples=10, runs_per_point=2, seed=1,
                  overwrite=False, extended=False, trajectories=10, levels=4,
                  jump=2, pool=100) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    name = models.config_name(config_file)
    if method not in METHODS:
        raise models.ModelError("method must be 'lhs', 'random', 'nolh' or 'ee'")
    if method in ("lhs", "random"):
        if samples is None:
            raise models.ModelError("samples is required for method %r" % method)
        if samples < 2:
            raise models.ModelError("samples must be at least 2")
    if method == "ee":
        _check_ee_settings(trajectories, levels, jump, pool)
    elif validation_samples < 1:
        raise models.ModelError("validation_samples must be at least 1 (the analysis needs them)")
    if runs_per_point < 2:
        raise models.ModelError("runs_per_point must be at least 2 (LSD's R package refuses fewer)")
    models.require_loadable(path)
    parsed = lsdfile.parse(path)
    spec = _parse_factors(parsed, factors)
    _clear_design(folder, name, overwrite, keep_sa=False)

    # .sa: name, lag, number of values, type (f: float, i: integer), min, max
    sa_lines = []
    for (fname, low, high, is_int) in spec:
        sa_lines.append("%s 0 2 %s %s %s" % (fname, "i:" if is_int else "f:",
                                            format(low, ".15g"), format(high, ".15g")))
    (folder / (name + ".sa")).write_text("\n".join(sa_lines) + "\n")

    if method in ("nolh", "ee"):
        result = design_from_sa(model, config_file, method, validation_samples, runs_per_point,
                                seed, overwrite=True, extended=extended, trajectories=trajectories,
                                levels=levels, jump=jump, pool=pool)
        if method == "nolh" and samples is not None:
            result["note"] = "samples is not used with nolh: LSD chooses the table from the number of factors"
        if method == "ee":
            result["note"] = ("ee has no out-of-sample set: validation_samples is ignored, and samples "
                              "is not used (the size is trajectories x (factors + 1))")
        warnings = coarse_integer_warnings(spec, result["points"]) if method == "nolh" else []
        if warnings:
            result["warning"] = " ".join(warnings)
        return result

    rng = random.Random(seed)
    design = sample_points(spec, samples, method, rng)
    validation = sample_points(spec, validation_samples, "random", rng)
    names = [item[0] for item in spec]
    total = samples + validation_samples
    design_csv = "%s_1_%d.csv" % (name, samples)
    valid_csv = "%s_%d_%d.csv" % (name, samples + 1, total)
    (folder / design_csv).write_text(_format_csv(names, design))
    (folder / valid_csv).write_text(_format_csv(names, validation))

    original = lsdfile.read_text(path)
    columns = design + validation
    texts = models.generate_configurations(lsdsource.lsd_root(), path, names, columns)
    for k, text in enumerate(texts, start=1):
        text = lsdfile.restore_equation_line(text, original)
        text = lsdfile.set_settings(text, SIM_NUM=runs_per_point,
                                    SEED=seed + (k - 1) * runs_per_point)
        lsdfile.write_text(folder / ("%s_%d.lsd" % (name, k)), text)
    design_info_path(folder, name).write_text(json.dumps(
        {"method": method, "points": samples, "validation_points": validation_samples,
         "runs_per_point": runs_per_point, "seed": seed}, indent=2) + "\n")
    result = {
        "sensitivity_file": name + ".sa",
        "design_table": design_csv,
        "validation_table": valid_csv,
        "configurations": "%s_1.lsd ... %s_%d.lsd" % (name, name, total),
        "points": samples,
        "validation_points": validation_samples,
        "runs_per_point": runs_per_point,
    }
    warnings = coarse_integer_warnings(spec, samples)
    if warnings:
        result["warning"] = " ".join(warnings)
    return result


def design_from_sa(model, config_file, method, validation_samples=10, runs_per_point=2,
                   seed=1, overwrite=False, extended=False, trajectories=10, levels=4,
                   jump=2, pool=100) -> dict:
    """A nolh or ee design from an existing <config>.sa file, by LSD's design code.

    create_design writes the .sa file from its factors and calls this; it can
    also be called with a .sa file made in LSD's interface (which may name
    initial values of variables as factors). The design is not seeded from
    `seed` unless it is given: lsd_doe -r sets the seed LSD's code starts from.
    """
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    name = models.config_name(config_file)
    if method not in ("nolh", "ee"):
        raise models.ModelError("method must be 'nolh' or 'ee'")
    if method == "ee":
        _check_ee_settings(trajectories, levels, jump, pool)
    elif validation_samples < 1:
        raise models.ModelError("validation_samples must be at least 1 (the analysis needs them)")
    if runs_per_point < 2:
        raise models.ModelError("runs_per_point must be at least 2 (LSD's R package refuses fewer)")
    models.require_loadable(path)
    if not (folder / (name + ".sa")).is_file():
        raise models.ModelError("%s.sa not found" % name)
    _clear_design(folder, name, overwrite, keep_sa=True)

    info = {"method": method}
    if method == "nolh":
        options = ["-m", "nolh", "-r", str(seed)]
        if extended:
            options.append("-x")
        made = run_doe(folder, name, *options)
        info["extended"] = bool(extended)
        points, validation_points = made["points"], validation_samples
        run_doe(folder, name, "-m", "mc", "-n", str(validation_samples), "-i", str(points + 1),
                "-r", str(seed))
    else:
        made = run_doe(folder, name, "-m", "ee", "-t", str(trajectories), "-p", str(pool),
                       "-l", str(levels), "-j", str(jump), "-r", str(seed))
        points, validation_points = made["points"], 0
        info.update({"levels": levels, "jump": jump, "trajectories": trajectories, "pool": pool})
    total = points + validation_points
    info.update({"points": points, "validation_points": validation_points,
                 "runs_per_point": runs_per_point, "seed": seed})
    _write_runs_and_seeds(folder, name, lsdfile.read_text(path), total, runs_per_point, seed)
    design_info_path(folder, name).write_text(json.dumps(info, indent=2) + "\n")

    result = {"sensitivity_file": name + ".sa",
              "design_table": "%s_1_%d.csv" % (name, points)}
    if method == "nolh":
        result["validation_table"] = "%s_%d_%d.csv" % (name, points + 1, total)
    result.update({"configurations": "%s_1.lsd ... %s_%d.lsd" % (name, name, total),
                   "method": method, "points": points, "validation_points": validation_points,
                   "runs_per_point": runs_per_point})
    if method == "ee":
        result.update({"levels": levels, "jump": jump, "trajectories": trajectories, "pool": pool})
    return result


def coarse_integer_warnings(spec, samples: int) -> list:
    """Integer factors with few levels collapse the Latin hypercube onto those
    levels (rule of thumb: fewer levels than samples / 4)."""
    warnings = []
    for name, low, high, is_int in spec:
        levels = int(high) - int(low) + 1
        if is_int and levels < samples / 4:
            warnings.append(
                "Integer factor %s has %d levels for %d samples: the Latin hypercube collapses "
                "onto those levels, which can make the Kriging fit fail (rule of thumb)."
                % (name, levels, samples))
    return warnings


def find_design(folder: Path, name: str) -> dict:
    """Numbering of an existing design from its csv file names.

    A meta-model design has two tables (design points and out-of-sample
    points); an elementary effects design has one, and "validation" is None.
    """
    pattern = re.compile(r"^%s_(\d+)_(\d+)\.csv$" % re.escape(name))
    ranges = []
    for path in folder.iterdir():
        match = pattern.match(path.name)
        if match:
            ranges.append((int(match.group(1)), int(match.group(2)), path))
    ranges.sort()
    info = read_design_info(folder, name)
    needs_two = info is not None and info.get("validation_points", 0) > 0
    if not ranges or (len(ranges) < 2 and needs_two):
        raise models.ModelError("no complete design for %s: run sa_create_design first" % name)
    if len(ranges) == 1:
        return {"design": ranges[0][2], "validation": None,
                "first": ranges[0][0], "last": ranges[0][1]}
    return {"design": ranges[0][2], "validation": ranges[1][2],
            "first": ranges[0][0], "last": ranges[1][1]}


def _expected_results(folder: Path, name: str, k: int) -> list:
    parsed = lsdfile.parse(folder / ("%s_%d.lsd" % (name, k)))
    runs = int(parsed.settings.get("SIM_NUM", "1"))
    seed = int(parsed.settings.get("SEED", "1"))
    names = []
    for j in range(runs):
        names.append("%s_%d_%d.res.gz" % (name, k, seed + j))
    return names


def _results_present(folder: Path, name: str, k: int) -> bool:
    for result in _expected_results(folder, name, k):
        if not (folder / result).is_file():
            return False
    return True


def run_design(model, config_file, threads=None, timeout_s=3600) -> dict:
    if timeout_s < 1:
        raise models.ModelError("timeout_s must be at least 1, got %s" % timeout_s)
    folder = models.resolve_writable(model)
    name = models.config_name(config_file)
    design = find_design(folder, name)
    built = build.compile_model(lsdsource.lsd_root(), folder)
    if not built.ok:
        return {"ok": False, "stage": "compile", "errors": built.errors}
    todo = []
    skipped = 0
    for k in range(design["first"], design["last"] + 1):
        if not (folder / ("%s_%d.lsd" % (name, k))).is_file():
            raise models.ModelError("missing configuration %s_%d.lsd" % (name, k))
        if _results_present(folder, name, k):
            skipped += 1
        else:
            todo.append(k)
    workers = threads or os.cpu_count() or 1
    deadline = time.time() + timeout_s
    failures = []
    not_started = []

    def one(k):
        remaining = deadline - time.time()
        if remaining <= 1:
            return k, None
        result = run([built.exe, "-f", "%s_%d.lsd" % (name, k)], cwd=folder, timeout=remaining)
        return k, result

    ran = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for k, result in pool.map(one, todo):
            if result is None:
                not_started.append(k)
            elif result.ok and _results_present(folder, name, k):
                ran += 1
            else:
                failures.append({"point": k, "tail": result.output[-300:],
                                 "timed_out": result.timed_out})
    summary = {"ok": not failures and not not_started,
               "points": design["last"] - design["first"] + 1,
               "already_done": skipped, "ran": ran,
               "failed": len(failures), "not_started": len(not_started)}
    if failures:
        summary["failures"] = failures[:5]
    return summary


def rscript_status() -> dict:
    """Is Rscript on the PATH and is LSDsensitivity installed?"""
    exe = config.rscript()
    if not shutil.which(exe):
        return {"rscript": False, "lsdsensitivity": False,
                "message": "Rscript not found (looked for %r; set LSD_MCP_RSCRIPT)" % exe}
    result = run([exe, "-e", 'cat(requireNamespace("LSDsensitivity", quietly = TRUE))'], timeout=120)
    if result.output.strip().endswith("TRUE"):
        return {"rscript": True, "lsdsensitivity": True, "message": ""}
    return {"rscript": True, "lsdsensitivity": False,
            "message": "R package LSDsensitivity is not installed (install.packages('LSDsensitivity'))"}


def _read_csv(path: Path) -> list:
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def design_factor_count(design: dict) -> int:
    with open(design["design"], newline="") as handle:
        return len(next(csv.reader(handle)))


def check_window(folder: Path, name: str, design: dict, ini_drop: int, n_keep: int):
    """ini_drop and n_keep must fit inside MAX_STEP of the design's configurations."""
    parsed = lsdfile.parse(folder / ("%s_%d.lsd" % (name, design["first"])))
    max_step = int(parsed.settings.get("MAX_STEP", "0"))
    if ini_drop < 0 or ini_drop >= max_step:
        raise models.ModelError("ini_drop must be at least 0 and below MAX_STEP (%d), got %s"
                                % (max_step, ini_drop))
    if n_keep != -1 and n_keep < 1:
        raise models.ModelError("n_keep must be -1 (all) or at least 1, got %s" % n_keep)
    if n_keep != -1 and ini_drop + n_keep > max_step:
        raise models.ModelError("ini_drop + n_keep (%d) exceeds MAX_STEP (%d)"
                                % (ini_drop + n_keep, max_step))


def _ee_settings(info, design: dict, levels, jump):
    """Levels and jump for the elementary effects analysis, from the sidecar
    or, for a design made in LSD's interface, from the caller."""
    if info is not None and info.get("method") != "ee":
        raise models.ModelError(
            "this design was made with method %r; elementary effects needs a design made "
            "with method 'ee'" % info.get("method"))
    if info is not None:
        for label, given in (("levels", levels), ("jump", jump)):
            if given is not None and given != info[label]:
                raise models.ModelError("%s=%s does not match the design (%s=%s, from the "
                                        "design file); omit it" % (label, given, label, info[label]))
        return info["levels"], info["jump"]
    if levels is None or jump is None:
        raise models.ModelError(
            "this design has no design file (it was not made by sa_create_design); pass levels "
            "and jump as set in LSD's Elementary Effects dialog (its defaults are 4 and 2)")
    if design["validation"] is not None:
        raise models.ModelError("this design has two design tables, so it is a meta-model "
                                "design, not an elementary effects design")
    if levels < 2 or levels % 2 != 0 or jump < 1:
        raise models.ModelError("levels must be even and at least 2, and jump at least 1")
    return levels, jump


def analyze(model, config_file, variable, metamodel=None, ini_drop=0,
            n_keep=-1, r_seed=1, levels=None, jump=None) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    name = models.config_name(config_file)
    info = read_design_info(folder, name)
    if metamodel is None:
        metamodel = "ee" if info is not None and info.get("method") == "ee" else "kriging"
    if metamodel not in ("kriging", "polynomial", "ee"):
        raise models.ModelError("metamodel must be 'kriging', 'polynomial' or 'ee'")
    element = lsdfile.parse(path).element(variable)
    if element is None:
        raise models.ModelError("unknown variable %r" % variable)
    if not element.saved:
        raise models.ModelError("%r is not saved; use set_saved first and rerun the design" % variable)
    design = find_design(folder, name)
    if metamodel == "ee":
        levels, jump = _ee_settings(info, design, levels, jump)
    else:
        if info is not None and info.get("method") == "ee":
            raise models.ModelError(
                "this is an elementary effects design; use metamodel='ee' (kriging and polynomial "
                "need a lhs, random or nolh design with an out-of-sample set)")
        if levels is not None or jump is not None:
            raise models.ModelError("levels and jump are only used with metamodel='ee'")
        if design["validation"] is None:
            raise models.ModelError("this design has no out-of-sample table, which the "
                                    "meta-model needs; create a lhs, random or nolh design")
    if metamodel == "polynomial" and design_factor_count(design) < 2:
        raise models.ModelError(
            "LSD's polynomial meta-model needs at least two factors (its package builds a "
            "broken formula for one); use metamodel='kriging'")
    check_window(folder, name, design, ini_drop, n_keep)
    missing = 0
    for k in range(design["first"], design["last"] + 1):
        if not _results_present(folder, name, k):
            missing += 1
    if missing:
        return {"ok": False, "message": "%d design points have no result files; run sa_run_design first" % missing}
    status = rscript_status()
    if not status["rscript"] or not status["lsdsensitivity"]:
        return {"ok": False, "message": status["message"]}

    # R writes into a scratch folder; <config>_sa/ is only touched on success.
    table_files = ("ee.csv",) if metamodel == "ee" else ("fit.csv", "sobol.csv")
    with tempfile.TemporaryDirectory(prefix="lsd-sa-") as scratch:
        scratch = Path(scratch)
        validation = design["validation"] or design["design"]
        command = [config.rscript(), R_SCRIPT, folder, name, variable, metamodel,
                   int(ini_drop), int(n_keep), design["design"], validation,
                   int(levels or 0), int(jump or 0), scratch, int(r_seed)]
        result = run(command, cwd=folder, timeout=3600)
        error_file = scratch / "error.txt"
        if error_file.is_file():
            return _r_failure(error_file.read_text().strip()[:1500])
        if not result.ok or not (scratch / table_files[-1]).is_file():
            return {"ok": False, "message": "R failed", "output_tail": result.output[-1500:]}
        out = folder / (name + "_sa") / analysis_folder(variable, metamodel)
        out.mkdir(parents=True, exist_ok=True)
        for filename in table_files:
            shutil.copyfile(scratch / filename, out / filename)

    relative = "%s_sa/%s" % (name, out.name)
    if metamodel == "ee":
        answer = {"ok": True, "metamodel": "ee", "levels": levels, "jump": jump,
                  "effects": ee_effects(out / "ee.csv"),
                  "files": "%s/ee.csv" % relative}
        note = column_note(folder, name, variable, design)
        if note:
            answer["note"] = note
        return answer

    fit = _read_csv(out / "fit.csv")[0]
    quality = float(fit["value"])
    sobol = []
    for row in _read_csv(out / "sobol.csv"):
        sobol.append({"factor": row["factor"], "direct": float(row["direct"]),
                      "interactions": float(row["interactions"])})
    answer = {"ok": True, "metamodel": metamodel, "fit": {fit["metric"]: quality},
              "sobol": sobol, "files": "%s/fit.csv, %s/sobol.csv" % (relative, relative)}
    warnings = []
    if quality < 0.5:
        warnings.append("%s is %.2f: the meta-model does not predict the out-of-sample "
                        "points well, so the Sobol indices are not reliable "
                        "(0.5 is a rule of thumb, not an LSD threshold)" % (fit["metric"], quality))
    if factors_not_separated(sobol):
        warnings.append("every factor has the same direct and interaction values: the "
                        "meta-model could not separate the factors, so the indices are "
                        "not meaningful")
    if warnings:
        answer["warning"] = "; ".join(warnings)
    note = column_note(folder, name, variable, design)
    if note:
        answer["note"] = note
    return answer


def ee_effects(path: Path) -> list:
    """Rows of ee.csv (sorted by mu.star, as LSD's table is); R writes NA for
    statistics it could not compute, which become None."""
    def number(text):
        try:
            value = float(text)
        except ValueError:
            return None
        return None if value != value else value

    rows = []
    for row in _read_csv(path):
        rows.append({"factor": row["factor"], "mu": number(row["mu"]),
                     "mu_star": number(row["mu.star"]), "sigma": number(row["sigma"]),
                     "se": number(row["se"]), "p_value": number(row["p.value"])})
    return rows


def analysis_folder(variable: str, metamodel: str) -> str:
    """Folder name <variable>-<metamodel>, safe for any file system."""
    return "%s-%s" % (re.sub(r"[^A-Za-z0-9_.-]", "_", variable), metamodel)


def factors_not_separated(sobol: list) -> bool:
    if len(sobol) < 2:
        return False
    for row in sobol[1:]:
        if abs(row["direct"] - sobol[0]["direct"]) > 1e-9:
            return False
        if abs(row["interactions"] - sobol[0]["interactions"]) > 1e-9:
            return False
    return True


def column_note(folder: Path, name: str, variable: str, design: dict):
    """LSD reuses labels for objects created during a run, so a variable can
    have several columns. LSDinterface's select.colnames.lsd (select.R lines
    65-69, instance = 1) takes the first column of that name in the file."""
    path = folder / _expected_results(folder, name, design["first"])[0]
    if not path.is_file():
        return None
    with results_reader.open_result(path) as handle:
        columns = results_reader.read_header(handle)
    matching = [column for column in columns if column[0] == variable]
    if len(matching) < 2:
        return None
    return ("analysed the first of %d columns named %s (LSD's R package takes the first "
            "column of that name in the file; in %s that is '%s')"
            % (len(matching), variable, path.name, results_reader.span_label(matching[0])))


def _r_failure(r_message: str) -> dict:
    if "leading minor" in r_message:
        return {"ok": False,
                "message": ("The Kriging fit failed numerically (the covariance matrix is not "
                            "positive definite). This usually means design points nearly "
                            "coincide (integer factors with few levels, very narrow ranges) or "
                            "the response is nearly constant or erratic. Try "
                            "metamodel='polynomial', more spread-out points (wider ranges, "
                            "more levels), or fewer points."),
                "r_message": r_message}
    if "does not vary over the design" in r_message:
        return {"ok": False, "message": r_message.split("failed: ", 1)[-1]}
    if "negative weights" in r_message:
        return {"ok": False,
                "message": ("LSD's polynomial meta-model weights each design point by "
                            "mean/SD of the response and stops when a point has a negative "
                            "mean. Use metamodel='kriging', or a response whose mean is "
                            "positive at every design point."),
                "r_message": r_message}
    if "Not enough data files" in r_message:
        return {"ok": False,
                "message": ("LSD's R package needs at least two runs per design point and found "
                            "fewer result files. A design made in LSD's interface with one run "
                            "per configuration has to be rerun with SIM_NUM 2 or more in every "
                            "numbered configuration; a design made by sa_create_design may not "
                            "have been run completely (see sa_run_design)."),
                "r_message": r_message}
    return {"ok": False, "message": r_message}
