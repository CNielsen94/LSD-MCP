"""Editing a model's structure and creating a model, by LSD's own code.

Both go through lsd_edit (structure.cpp): it loads a configuration with LSD's
loader (or starts from an empty Root), applies the operations in memory with
the functions LSD's interface calls, and writes the result with LSD's
save_configuration into a scratch folder. This module checks the operations,
puts the result in place and keeps the .bak copy, as set_values does.
"""

import re
import shutil
import tempfile
import time
from pathlib import Path

from . import build, lsdfile, lsdsource, models
from .runner import run

# operation -> (required fields, optional fields)
OPERATIONS = {
    "add_object": (("parent", "name"), ("instances",)),
    "add_parameter": (("object", "name"), ("value",)),
    "add_variable": (("object", "name"), ("lags", "initial", "saved")),
    "add_function": (("object", "name"), ()),
    "rename": (("name", "new_name"), ()),
    "delete": (("name",), ("force",)),
    "set_instances": (("object", "instances"), ()),
    "set_instance_values": (("name", "values"), ("lag",)),
    "describe": (("name", "text"), ()),
}
STRINGS = ("parent", "object", "name", "new_name", "text")
WHOLE_NUMBERS = ("instances", "lags", "lag")
FLAGS = ("saved", "force")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and abs(value) != float("inf")


def _check_field(position: int, op: str, key: str, value):
    where = "operation %d (%s): %s" % (position, op, key)
    if key in STRINGS and not isinstance(value, str):
        raise models.ModelError("%s must be a string" % where)
    if key in WHOLE_NUMBERS and (not isinstance(value, int) or isinstance(value, bool)):
        raise models.ModelError("%s must be a whole number" % where)
    if key in FLAGS and not isinstance(value, bool):
        raise models.ModelError("%s must be true or false" % where)
    if key == "value" and not _is_number(value):
        raise models.ModelError("%s must be a number" % where)
    if key == "values":
        if not isinstance(value, list) or not value or not all(_is_number(item) for item in value):
            raise models.ModelError("%s must be a non-empty list of numbers" % where)
    if key == "initial":
        items = value if isinstance(value, list) else [value]
        if not items or not all(_is_number(item) for item in items):
            raise models.ModelError("%s must be a number or a non-empty list of numbers" % where)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def _text(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, list):
        return ",".join(_text(item) for item in value)
    return _escape(str(value))


def operation_lines(operations) -> list:
    """One tab-separated line per operation, checked for known names and types."""
    if not isinstance(operations, list):
        raise models.ModelError("operations must be a list of objects")
    lines = []
    for position, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
            raise models.ModelError("operation %d: give an object with an \"op\" field" % position)
        op = operation["op"]
        if op not in OPERATIONS:
            raise models.ModelError("operation %d: unknown op %r (known: %s)"
                                    % (position, op, ", ".join(OPERATIONS)))
        required, optional = OPERATIONS[op]
        for key in required:
            if key not in operation:
                raise models.ModelError("operation %d (%s): missing %s" % (position, op, key))
        for key, value in operation.items():
            if key != "op" and key not in required + optional:
                raise models.ModelError("operation %d (%s): unknown field %r" % (position, op, key))
            if key != "op":
                _check_field(position, op, key, value)
        fields = [op]
        for key, value in operation.items():
            if key != "op":
                fields.append("%s=%s" % (key, _text(value)))
        lines.append("\t".join(fields))
    return lines


def run_edit(exec_folder: Path, operations, source: Path = None, out_name: str = "out") -> str:
    """Text of the configuration LSD saves after the operations. source is the
    configuration to load; None starts from an empty Root. out_name is the
    base name LSD saves under (a new configuration's report is named after it)."""
    exe = build.utilities(lsdsource.lsd_root())["lsd_edit"]
    lines = operation_lines(operations)
    with tempfile.TemporaryDirectory(prefix="lsd-edit-") as tmp:
        tmp = Path(tmp)
        (tmp / "operations.txt").write_text("".join(line + "\n" for line in lines))
        command = [exe, "-o", out_name, "-e", exec_folder, "-x", tmp / "operations.txt"]
        command += ["-f", source] if source is not None else ["-n"]
        result = run(command, cwd=tmp, timeout=300, separate_stderr=True)
        if not result.ok:
            message = (result.stderr or result.output).strip()[-600:]
            raise models.ModelError(message or "lsd_edit failed")
        return lsdfile.read_text(tmp / (out_name + ".lsd"))


def edit_structure(model, config_file, operations, new_config=None) -> dict:
    folder = models.resolve_writable(model)
    path = models.config_path(folder, config_file, model)
    if not operations:
        raise models.ModelError("operations is empty")
    models.require_loadable(path)
    original = lsdfile.read_text(path)
    text = run_edit(folder, operations, path)
    # LSD blanks the EQUATION name when the file is not in the model folder
    text = lsdfile.restore_equation_line(text, original)
    target = path if new_config is None else folder / (models.config_name(new_config) + ".lsd")
    result = {"written": target.name, "operations": len(operations), "replaced": target.exists()}
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        shutil.copy2(target, backup)
        result["backup"] = backup.name
    lsdfile.write_text(target, text)
    return result


# --- new models ------------------------------------------------------------------

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
DATE_FORMAT = "%d %B, %Y"  # DATE_FMT in LSD's common.h
# LMM's model info file (common.cpp, update_model_info): name, version, date, the
# window positions (none yet, "#"), and the last configuration and object
INFO_TAIL = ["#"] * 9 + ["Root", "1", "0", "0"]


def _model_options(equation_name: str) -> str:
    """What LMM writes for a new model (check_option_files in common.cpp)."""
    return ("# LSD options\nTARGET=LSD\nFUN=%s\n\n# Additional model files\nFUN_EXTRA=\n\n"
            "# Compiler options\nSWITCH_CC=-O0 -ggdb3\nSWITCH_CC_LNK=\n" % equation_name)


def create_model(name: str, title: str = "", description: str = "") -> dict:
    target = models.new_model_destination(name)
    last = name.rstrip("/").split("/")[-1]
    if not NAME_PATTERN.match(last):
        raise models.ModelError("a model's folder name may contain only letters, digits and "
                                "underscores (it names the equation file fun_<name>.cpp): %r" % last)
    if not isinstance(title, str) or not isinstance(description, str) or "\n" in title:
        raise models.ModelError("title must be one line of text and description a string")
    base = lsdsource.lsd_root() / "src" / "fun_base.cpp"
    equation = "fun_%s" % last
    config_text = run_edit(target.parent, [], None, "Sim1")  # an empty Root: lsd_edit -n
    existed = target.parent.is_dir()
    target.mkdir(parents=True)
    try:
        shutil.copyfile(base, target / (equation + ".cpp"))
        (target / "model_options.txt").write_text(_model_options(equation))
        info = [title or last, "1.0", time.strftime(DATE_FORMAT)] + INFO_TAIL
        (target / "modelinfo.txt").write_text("\n".join(info) + "\n")
        (target / "description.txt").write_text("%s\n\n%s\n" % (title or last, description.strip()))
        text = lsdfile.restore_equation_line(config_text, "EQUATION %s.cpp\n" % equation)
        lsdfile.write_text(target / "Sim1.lsd", text)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        if not existed:
            shutil.rmtree(target.parent, ignore_errors=True)
        raise
    return {"model": name, "equation_file": equation + ".cpp", "configuration": "Sim1",
            "files": sorted(path.name for path in target.iterdir())}
