"""Run configurations with the compiled model program and read result files."""

import gzip
import math
import re
import time
from pathlib import Path

from . import build, lsdsource, models
from .runner import run

CELL = re.compile(r"^(.+?) (\d+) \((-?\d+) (-?\d+)\)$")
MAX_SERIES = 50


def result_snapshot(folder: Path) -> dict:
    snapshot = {}
    for path in folder.iterdir():
        if ".res" in path.name or ".tot" in path.name:
            snapshot[path.name] = path.stat().st_mtime_ns
    return snapshot


def changed_results(folder: Path, before: dict) -> list:
    names = []
    for name, mtime in result_snapshot(folder).items():
        if before.get(name) != mtime:
            names.append(name)
    return sorted(names)


def open_result(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def read_header(handle) -> list:
    """Columns of a .res file: (name, instance, first step, last step)."""
    cells = handle.readline().rstrip("\r\n").split("\t")
    columns = []
    for cell in cells:
        cell = cell.strip()
        if not cell:
            continue
        match = CELL.match(cell)
        if match:
            columns.append((match.group(1), int(match.group(2)),
                            int(match.group(3)), int(match.group(4))))
        else:
            columns.append((cell, 0, 0, 0))
    return columns


def parse_row(line: str, width: int) -> list:
    values = []
    for cell in line.rstrip("\r\n").split("\t"):
        if cell.strip() == "":
            continue
        try:
            values.append(float(cell))
        except ValueError:
            values.append(math.nan)
    return values[:width]


def finite(value):
    """JSON has no NaN; report missing values as null."""
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value


def label(column) -> str:
    return "%s %d" % (column[0], column[1])


def summarise(path: Path) -> dict:
    """Last value and mean per series of one result file, capped at 50 series."""
    with open_result(path) as handle:
        columns = read_header(handle)
        total = [0.0] * len(columns)
        count = [0] * len(columns)
        last = [math.nan] * len(columns)
        rows = 0
        for line in handle:
            values = parse_row(line, len(columns))
            rows += 1
            for index, value in enumerate(values):
                last[index] = value
                if not math.isnan(value):
                    total[index] += value
                    count[index] += 1
    series = []
    for index, column in enumerate(columns[:MAX_SERIES]):
        mean = total[index] / count[index] if count[index] else math.nan
        series.append({"series": label(column), "last": finite(last[index]), "mean": finite(mean)})
    info = {"file": path.name, "steps": rows, "series": series}
    if len(columns) > MAX_SERIES:
        info["note"] = "showing the first %d of %d series" % (MAX_SERIES, len(columns))
    return info


def run_configuration(model: str, config_file: str, seed=None, runs=None,
                      threads=None, timeout_s=600) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    root = lsdsource.lsd_root()
    built = build.compile_model(root, folder)
    if not built.ok:
        return {"ok": False, "stage": "compile", "errors": built.errors}
    command = [built.exe, "-f", path.name]
    if seed is not None:
        command += ["-s", int(seed)]
    if runs is not None:
        command += ["-e", int(runs)]
    if threads:
        if runs and int(runs) > 1:
            command += ["-c", "1:%d" % int(threads)]  # parallel runs
        else:
            command += ["-c", str(int(threads))]
    before = result_snapshot(folder)
    started = time.time()
    result = run(command, cwd=folder, timeout=timeout_s)
    seconds = round(time.time() - started, 2)
    written = changed_results(folder, before)
    if not result.ok:
        reason = "timed out after %s s" % timeout_s if result.timed_out else "exit status %d" % result.returncode
        return {"ok": False, "stage": "run", "reason": reason,
                "output_tail": result.output[-2000:]}
    reports = [name for name in written if ".res" in name]
    summary = None
    if reports:
        reports.sort(key=lambda name: (len(name), name))
        summary = summarise(folder / reports[0])
    info = {"ok": True, "seconds": seconds, "result_files": written, "first_run": summary}
    if threads and runs and int(runs) > 1:
        info["note"] = "runs in parallel: LSD writes no totals file in this mode"
    return info


def read_results(model: str, results_file: str, variables=None, start=None,
                 end=None, max_points=200) -> dict:
    folder = models.resolve_model(model, "models")
    if "/" in results_file or "\\" in results_file or results_file.startswith("."):
        raise models.ModelError("results_file must be a file name inside the model folder")
    path = folder / results_file
    if not path.is_file() or ".res" not in path.name:
        raise models.ModelError("no result file %s in the model folder" % results_file)
    with open_result(path) as handle:
        columns = read_header(handle)
        chosen = []
        for index, column in enumerate(columns):
            if variables and column[0] not in variables and label(column) not in variables:
                continue
            chosen.append(index)
        if variables and not chosen:
            available = [label(column) for column in columns[:MAX_SERIES]]
            raise models.ModelError(
                "no series matched %s; available series (first %d of %d): %s"
                % (list(variables), len(available), len(columns), ", ".join(available)))
        omitted = max(0, len(chosen) - MAX_SERIES)
        chosen = chosen[:MAX_SERIES]
        offset = columns[0][2] if columns else 0
        steps = []
        data = []
        for index in chosen:
            data.append([])
        row_number = 0
        for line in handle:
            step = offset + row_number
            row_number += 1
            if start is not None and step < start:
                continue
            if end is not None and step > end:
                break
            values = parse_row(line, len(columns))
            steps.append(step)
            for slot, index in enumerate(chosen):
                data[slot].append(values[index] if index < len(values) else math.nan)
    keep = thin_indices(len(steps), max_points)
    out = {"steps": [steps[i] for i in keep], "series": {}}
    for slot, index in enumerate(chosen):
        out["series"][label(columns[index])] = [finite(data[slot][i]) for i in keep]
    out["total_steps_in_range"] = len(steps)
    if omitted:
        out["note"] = "%d more series omitted (limit %d); pass variables to choose" % (omitted, MAX_SERIES)
    return out


def thin_indices(n: int, limit: int) -> list:
    if limit < 2 or n <= limit:
        return list(range(n))
    picked = []
    for i in range(limit):
        picked.append(round(i * (n - 1) / (limit - 1)))
    return picked
