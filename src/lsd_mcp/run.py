"""Run configurations with the compiled model program and read result files."""

import gzip
import math
import re
import time
from pathlib import Path

from . import build, lsdsource, models
from .runner import run

# "Mean 1 (0 2000)", "_s 1_1 (133 200)", "InnoShock R (1 200)": name, instance
# (a string: nested objects give paths such as 1_1), first and last step with data
CELL = re.compile(r"^(\S+) (\S+) \((-?\d+) (-?\d+)\)$")
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
    """Columns of a .res file: (name, instance, first step, last step).

    The row number in the file is the time step (row 0 holds initial values);
    first and last only say where a series has data. They are None when the
    header cell has no range.
    """
    cells = handle.readline().rstrip("\r\n").split("\t")
    columns = []
    for cell in cells:
        cell = cell.strip()
        if not cell:
            continue
        match = CELL.match(cell)
        if match:
            columns.append((match.group(1), match.group(2),
                            int(match.group(3)), int(match.group(4))))
        else:
            columns.append((cell, "", None, None))
    return columns


def parse_row(line: str, width: int) -> list:
    cells = line.rstrip("\r\n").split("\t")
    if cells and cells[-1].strip() == "":
        cells.pop()  # LSD ends every row with a tab
    values = []
    for cell in cells:
        try:
            values.append(float(cell))
        except ValueError:
            values.append(math.nan)  # NA
    return values[:width]


def span_label(column) -> str:
    if column[2] is None:
        return label(column)
    return "%s (%d %d)" % (label(column), column[2], column[3])


def column_keys(columns) -> list:
    """One unique key per column.

    LSD reuses instance labels for objects created during a run, so the same
    label can name several columns, each with its own span. A label that
    occurs once keeps its plain form; a repeated label gets its span; if that
    still repeats, " #2", " #3" ... are appended.
    """
    labels = {}
    for column in columns:
        labels[label(column)] = labels.get(label(column), 0) + 1
    keys = []
    seen = {}
    for column in columns:
        key = label(column) if labels[label(column)] == 1 else span_label(column)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = "%s #%d" % (key, seen[key])
        keys.append(key)
    return keys


def matching_columns(columns, keys, wanted: str) -> list:
    """Columns a variables entry names, trying the most specific form first:
    label with span (all columns with that span), full key (picks one column
    of repeated spans, "... #2"), label (all columns with it), bare name."""
    tests = (lambda i: span_label(columns[i]) == wanted,
             lambda i: keys[i] == wanted,
             lambda i: label(columns[i]) == wanted,
             lambda i: columns[i][0] == wanted)
    for test in tests:
        found = [i for i in range(len(columns)) if test(i)]
        if found:
            return found
    return []


def in_range(column, step: int) -> bool:
    return column[2] is None or column[2] <= step <= column[3]


def finite(value):
    """JSON has no NaN; report missing values as null."""
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value


def label(column) -> str:
    return ("%s %s" % (column[0], column[1])).strip()


def summarise(path: Path) -> dict:
    """Last value and mean per series of one result file, capped at 50 series.

    NA cells and cells outside a series' own step range are ignored. Series
    whose name has few instances come first, so the cap does not hide them.
    """
    with open_result(path) as handle:
        columns = read_header(handle)
        total = [0.0] * len(columns)
        count = [0] * len(columns)
        last = [math.nan] * len(columns)
        rows = 0
        for line in handle:
            values = parse_row(line, len(columns))
            for index, value in enumerate(values):
                if math.isnan(value) or not in_range(columns[index], rows):
                    continue
                last[index] = value
                total[index] += value
                count[index] += 1
            rows += 1
    keys = column_keys(columns)
    instances = {}
    for column in columns:
        instances[column[0]] = instances.get(column[0], 0) + 1
    order = sorted(range(len(columns)), key=lambda index: instances[columns[index][0]])
    series = []
    for index in order[:MAX_SERIES]:
        mean = total[index] / count[index] if count[index] else math.nan
        series.append({"series": keys[index], "last": finite(last[index]),
                       "mean": finite(mean)})
    info = {"file": path.name, "steps": rows, "series": series}
    if len(columns) > MAX_SERIES:
        info["note"] = ("%d of %d series omitted (limit %d); names with few instances come "
                        "first; use read_results with variables for the others"
                        % (len(columns) - MAX_SERIES, len(columns), MAX_SERIES))
    return info


def run_configuration(model: str, config_file: str, seed=None, runs=None,
                      threads=None, timeout_s=600) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    if seed is not None and int(seed) < 1:
        raise models.ModelError("seed must be at least 1 (LSD ignores -s 0), got %s" % seed)
    if runs is not None and int(runs) < 1:
        raise models.ModelError("runs must be at least 1, got %s" % runs)
    if timeout_s < 1:
        raise models.ModelError("timeout_s must be at least 1, got %s" % timeout_s)
    models.require_loadable(path)
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
    if not any(".res" in name for name in written):
        info["note"] = ("LSD wrote no result file (the model may have stopped itself, or "
                        "no variable is saved); see output_tail for why")
        info["output_tail"] = result.output[-1500:]
    if threads and runs and int(runs) > 1:
        parallel = "runs in parallel: LSD writes no totals file in this mode"
        info["note"] = (info.get("note", "") + "; " + parallel).lstrip("; ")
    return info


def read_results(model: str, results_file: str, variables=None, start=None,
                 end=None, max_points=200) -> dict:
    folder = models.resolve_model(model, "models")
    if "/" in results_file or "\\" in results_file or results_file.startswith("."):
        raise models.ModelError("results_file must be a file name inside the model folder")
    path = folder / results_file
    if not path.is_file():
        raise models.ModelError("no result file %s in the model folder" % results_file)
    if max_points < 1:
        raise models.ModelError("max_points must be at least 1, got %s" % max_points)
    if start is not None and end is not None and start > end:
        raise models.ModelError("start (%s) is after end (%s)" % (start, end))
    if ".tot" in path.name:
        raise models.ModelError(totals_message(folder, path.name))
    if ".res" not in path.name:
        raise models.ModelError("%s is not a result file (.res or .res.gz)" % results_file)
    with open_result(path) as handle:
        columns = read_header(handle)
        keys = column_keys(columns)
        chosen = []
        not_found = []
        if variables:
            for wanted in variables:
                found = matching_columns(columns, keys, wanted)
                if not found:
                    not_found.append(wanted)
                for index in found:
                    if index not in chosen:
                        chosen.append(index)
            chosen.sort()
        else:
            chosen = list(range(len(columns)))
        if variables and not chosen:
            available = keys[:MAX_SERIES]
            raise models.ModelError(
                "no series matched %s; available series (first %d of %d): %s"
                % (list(variables), len(available), len(columns), ", ".join(available)))
        omitted = max(0, len(chosen) - MAX_SERIES)
        chosen = chosen[:MAX_SERIES]
        steps = []
        data = []
        for index in chosen:
            data.append([])
        row_number = 0
        for line in handle:
            step = row_number  # the row number is the time step
            row_number += 1
            if start is not None and step < start:
                continue
            if end is not None and step > end:
                break
            values = parse_row(line, len(columns))
            steps.append(step)
            for slot, index in enumerate(chosen):
                value = values[index] if index < len(values) else math.nan
                data[slot].append(value if in_range(columns[index], step) else math.nan)
    keep = thin_indices(len(steps), max_points)
    out = {"steps": [steps[i] for i in keep], "series": {}}
    for slot, index in enumerate(chosen):
        out["series"][keys[index]] = [finite(data[slot][i]) for i in keep]
    out["total_steps_in_range"] = len(steps)
    if not_found:
        out["not_found"] = not_found
    if omitted:
        hint = ("narrow variables (a full key such as '_s 1_1 (133 200)' picks one column)"
                if variables else "pass variables to choose")
        out["note"] = "%d more series omitted (limit %d); %s" % (omitted, MAX_SERIES, hint)
    return out


def thin_indices(n: int, limit: int) -> list:
    if n == 0:
        return []
    if limit == 1:
        return [n - 1]  # the last point
    if n <= limit:
        return list(range(n))
    picked = []
    for i in range(limit):
        picked.append(round(i * (n - 1) / (limit - 1)))
    return picked


def totals_message(folder: Path, name: str) -> str:
    """Totals files (.tot) have no header and one row per run, so they are not read here."""
    message = ("%s is a totals file: no header, one row per run holding the last value "
               "of each saved series. Totals files are not supported" % name)
    match = re.match(r"^(.*)_(\d+)_(\d+)\.tot(\.gz)?$", name)
    if match:
        prefix, first, last = match.group(1), int(match.group(2)), int(match.group(3))
        candidates = []
        for seed in range(first, min(last, first + 200) + 1):
            for suffix in (".res.gz", ".res"):
                if (folder / ("%s_%d%s" % (prefix, seed, suffix))).is_file():
                    candidates.append("%s_%d%s" % (prefix, seed, suffix))
        if candidates:
            message += "; read these result files instead: " + ", ".join(candidates[:20])
    return message
