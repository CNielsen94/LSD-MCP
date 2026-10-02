"""Sensitivity analysis by the meta-model route: design, batch run, R analysis.

File layout follows what LSD's own interface writes (compare
tests/data/doe_gui in the lsdsim project): <config>.sa, the design tables
<config>_1_S.csv and <config>_S+1_S+V.csv, and numbered configurations
<config>_k.lsd. Result files are <config>_k_<seed>.res.gz.
"""

import csv
import os
import random
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import build, config, lsdfile, lsdsource, models
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


def _design_files(folder: Path, name: str) -> list:
    """Files that belong to an existing design of this configuration."""
    escaped = re.escape(name)
    pattern = re.compile(
        r"^(%s\.sa|%s_\d+\.lsd|%s_\d+_\d+\.csv|%s_\d+_\d+\.(res|tot)(\.gz)?)$"
        % (escaped, escaped, escaped, escaped))
    found = []
    for path in sorted(folder.iterdir()):
        if pattern.match(path.name):
            found.append(path)
    return found


def _format_csv(names, points) -> str:
    lines = [",".join(names)]
    for point in points:
        lines.append(",".join("%.6f" % value for value in point))
    return "\n".join(lines) + "\n"


def create_design(model, config_file, factors, samples, method="lhs",
                  validation_samples=10, runs_per_point=2, seed=1,
                  overwrite=False) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file)
    name = models.config_name(config_file)
    if method not in ("lhs", "random"):
        raise models.ModelError("method must be 'lhs' or 'random'")
    if samples < 2:
        raise models.ModelError("samples must be at least 2")
    if validation_samples < 1:
        raise models.ModelError("validation_samples must be at least 1 (the analysis needs them)")
    if runs_per_point < 2:
        raise models.ModelError("runs_per_point must be at least 2 (LSD's R package refuses fewer)")
    parsed = lsdfile.parse(path)
    spec = _parse_factors(parsed, factors)
    existing = _design_files(folder, name)
    if existing and not overwrite:
        raise models.ModelError("a design for %s already exists (%d files); pass overwrite=True to replace it"
                                % (name, len(existing)))
    for old in existing:
        old.unlink()

    rng = random.Random(seed)
    design = sample_points(spec, samples, method, rng)
    validation = sample_points(spec, validation_samples, "random", rng)
    names = [item[0] for item in spec]
    total = samples + validation_samples

    # .sa: name, lag, number of values, type (f: float, i: integer), min, max
    sa_lines = []
    for (fname, low, high, is_int) in spec:
        sa_lines.append("%s 0 2 %s %s %s" % (fname, "i:" if is_int else "f:",
                                            format(low, ".15g"), format(high, ".15g")))
    (folder / (name + ".sa")).write_text("\n".join(sa_lines) + "\n")
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
    return {
        "sensitivity_file": name + ".sa",
        "design_table": design_csv,
        "validation_table": valid_csv,
        "configurations": "%s_1.lsd ... %s_%d.lsd" % (name, name, total),
        "points": samples,
        "validation_points": validation_samples,
        "runs_per_point": runs_per_point,
    }


def find_design(folder: Path, name: str) -> dict:
    """Numbering of an existing design from its csv file names."""
    pattern = re.compile(r"^%s_(\d+)_(\d+)\.csv$" % re.escape(name))
    ranges = []
    for path in folder.iterdir():
        match = pattern.match(path.name)
        if match:
            ranges.append((int(match.group(1)), int(match.group(2)), path))
    ranges.sort()
    if len(ranges) < 2:
        raise models.ModelError("no complete design for %s: run sa_create_design first" % name)
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


def analyze(model, config_file, variable, metamodel="kriging", ini_drop=0, n_keep=-1) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file)
    name = models.config_name(config_file)
    if metamodel not in ("kriging", "polynomial"):
        raise models.ModelError("metamodel must be 'kriging' or 'polynomial'")
    element = lsdfile.parse(path).element(variable)
    if element is None:
        raise models.ModelError("unknown variable %r" % variable)
    if not element.saved:
        raise models.ModelError("%r is not saved; use set_saved first and rerun the design" % variable)
    design = find_design(folder, name)
    missing = 0
    for k in range(design["first"], design["last"] + 1):
        if not _results_present(folder, name, k):
            missing += 1
    if missing:
        return {"ok": False, "message": "%d design points have no result files; run sa_run_design first" % missing}
    status = rscript_status()
    if not status["rscript"] or not status["lsdsensitivity"]:
        return {"ok": False, "message": status["message"]}

    out = folder / (name + "_sa")
    if out.exists():
        shutil.rmtree(out)
    command = [config.rscript(), R_SCRIPT, folder, name, variable, metamodel,
               int(ini_drop), int(n_keep), design["design"], design["validation"], out]
    result = run(command, cwd=folder, timeout=3600)
    error_file = out / "error.txt"
    if error_file.is_file():
        return {"ok": False, "message": error_file.read_text().strip()[:1500]}
    if not result.ok or not (out / "sobol.csv").is_file():
        return {"ok": False, "message": "R failed", "output_tail": result.output[-1500:]}
    fit = _read_csv(out / "fit.csv")[0]
    sobol = []
    for row in _read_csv(out / "sobol.csv"):
        sobol.append({"factor": row["factor"], "direct": float(row["direct"]),
                      "interactions": float(row["interactions"])})
    return {"ok": True, "metamodel": metamodel, "fit": {fit["metric"]: float(fit["value"])},
            "sobol": sobol, "files": "%s_sa/fit.csv, %s_sa/sobol.csv" % (name, name)}
