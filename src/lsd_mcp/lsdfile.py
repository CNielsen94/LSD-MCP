"""Read LSD 8.1 configuration (.lsd) files; minimal text edits for a few tokens.

Layout of a file: the object tree (Label X { Son: Y ... Var: A Param: B }),
then a DATA section with one block per object instance group, then the run
settings (SIM_NUM, SEED, MAX_STEP, EQUATION ...), then descriptions.
"""

import re
from dataclasses import dataclass, field

ELEMENT_KINDS = ("Var", "Param", "Func")
SETTINGS = ("SIM_NUM", "SEED", "MAX_STEP")


class LsdFileError(Exception):
    pass


@dataclass
class Element:
    name: str
    kind: str  # Var, Param or Func
    obj: str  # name of the object that owns it
    lags: int = 0
    flag: str = "n"  # s saved, n not saved; upper case = separate file
    values: list = field(default_factory=list)

    @property
    def saved(self) -> bool:
        return self.flag.lower() == "s"


@dataclass
class LsdObject:
    name: str
    parent: str = None
    instances: int = 0  # summed over all parent instances
    blocks: int = 0  # number of parent instances (one count each)
    computed: bool = True  # False when the Object line says N instead of C
    elements: list = field(default_factory=list)


@dataclass
class Configuration:
    objects: dict = field(default_factory=dict)  # name -> LsdObject, file order
    settings: dict = field(default_factory=dict)  # SIM_NUM, SEED, MAX_STEP, EQUATION

    def element(self, name: str):
        for obj in self.objects.values():
            for element in obj.elements:
                if element.name == name:
                    return element
        return None

    def elements(self) -> list:
        found = []
        for obj in self.objects.values():
            found.extend(obj.elements)
        return found


def _parse_tree(lines, config):
    """Fill objects and their element names from the tree before DATA."""
    stack = []
    pending = None
    for line in lines:
        words = line.split()
        if not words:
            continue
        if words[0] == "Label" and len(words) > 1:
            parent = stack[-1] if stack else None
            config.objects[words[1]] = LsdObject(words[1], parent)
            pending = words[1]
        elif words[0] == "{":
            stack.append(pending)
        elif words[0] == "}":
            if stack:
                stack.pop()
        elif words[0].rstrip(":") in ELEMENT_KINDS and len(words) > 1 and stack:
            owner = config.objects[stack[-1]]
            owner.elements.append(Element(words[1], words[0].rstrip(":"), owner.name))


def _number(text):
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _parse_data(lines, config):
    current = None
    for line in lines:
        if line.startswith("Object:"):
            fields = line.rstrip("\r\n").split("\t")
            head = fields[0].split()
            name = head[1]
            # one count per instance of the parent object
            counts = []
            for text in fields[1:]:
                if text.strip():
                    counts.append(int(text))
            current = config.objects.get(name)
            if current is None:
                current = LsdObject(name)
                config.objects[name] = current
            if len(head) > 2 and head[2] == "N":
                current.computed = False
            current.instances += sum(counts)
            current.blocks += len(counts)
            continue
        match = re.match(r"^(Var|Param|Func):\s+(\S+)\s+(-?\d+)\s+(\S)\s", line)
        if not match or current is None:
            continue
        kind, name, lags, flag = match.groups()
        element = None
        for candidate in current.elements:
            if candidate.name == name:
                element = candidate
        if element is None:
            element = Element(name, kind, current.name)
            current.elements.append(element)
        element.lags = int(lags)
        element.flag = flag
        head = line.split("\t")[0].split()
        if len(head) >= 7:  # name lags save init debug plot
            element.debug, element.plot = head[5], head[6]
        fields = line.rstrip("\r\n").split("\t")[1:]
        for text in fields:
            if text != "" and not text.startswith("<"):  # skip "<upd: ...>" update data
                element.values.append(_number(text))


def parse_text(text: str) -> Configuration:
    lines = text.splitlines()
    try:
        data_at = lines.index("DATA")
    except ValueError:
        raise LsdFileError("not an LSD configuration: no DATA line")
    end = len(lines)
    for index in range(data_at, len(lines)):
        if lines[index].startswith("SIM_NUM"):
            end = index
            break
    config = Configuration()
    _parse_tree(lines[:data_at], config)
    _parse_data(lines[data_at + 1:end], config)
    for line in lines[end:]:
        words = line.split(None, 1)
        if len(words) == 2 and (words[0] in SETTINGS or words[0] == "EQUATION"):
            if words[0] not in config.settings:
                config.settings[words[0]] = words[1].strip()
        if line.startswith("DESCRIPTION"):
            break
    return config


def parse(path) -> Configuration:
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        return parse_text(handle.read())


# --- text edits -------------------------------------------------------------

def read_text(path) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as handle:
        return handle.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write(text)


def set_settings(text: str, **values) -> str:
    """Replace the value of SIM_NUM / SEED / MAX_STEP (keys: SIM_NUM=..)."""
    lines = text.splitlines(keepends=True)
    done = set()
    for index, line in enumerate(lines):
        for key, value in values.items():
            if key in done or not line.startswith(key + " "):
                continue
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = "%s %d%s" % (key, value, ending)
            done.add(key)
    for key in values:
        if key not in done:
            raise LsdFileError("%s line not found" % key)
    return "".join(lines)


def set_save_flags(text: str, names, saved: bool):
    """Flip the save flag of the named elements. Returns (text, names changed)."""
    wanted = set(names)
    lines = text.splitlines(keepends=True)
    in_data = False
    changed = set()
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == "DATA":
            in_data = True
            continue
        if line.startswith("SIM_NUM"):
            break
        if not in_data:
            continue
        match = re.match(r"^((?:Var|Param|Func):\s+)(\S+)(\s+-?\d+\s+)(\S)(\s.*)$", line, re.S)
        if not match or match.group(2) not in wanted:
            continue
        flag = match.group(4)
        if saved:
            flag = "S" if flag.isupper() else "s"
        else:
            flag = "N" if flag.isupper() else "n"
        lines[index] = match.group(1) + match.group(2) + match.group(3) + flag + match.group(5)
        changed.add(match.group(2))
    return "".join(lines), sorted(changed)


def restore_equation_line(text: str, original: str) -> str:
    """lsd_confgen turns 'EQUATION fun_x.cpp' into a bare 'EQUATION'; put it back."""
    wanted = None
    for line in original.splitlines():
        if line.startswith("EQUATION "):
            wanted = line
            break
    if wanted is None:
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == "EQUATION":
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = wanted.rstrip("\r\n") + ending
            break
    return "".join(lines)


def restore_tail(text: str, original: str) -> str:
    """lsd_confgen drops everything after MODELREPORT (descriptions, embedded
    equations). Append that part of the original again."""
    if "\nDESCRIPTION\n" in text or text.startswith("DESCRIPTION\n"):
        return text
    lines = original.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == "DESCRIPTION":
            start = index
            break
    if start is None:
        return text
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    if not text.endswith("\n"):
        text += "\n"
    return text + "".join(lines[start:])
