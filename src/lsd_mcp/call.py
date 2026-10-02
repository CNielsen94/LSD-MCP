"""Run one tool: python3 -m lsd_mcp.call TOOL, arguments as JSON on stdin.

Prints exactly one JSON document to stdout. Anything the tool itself prints
is sent to stderr.
"""

import json
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # stray prints must not corrupt the JSON answer
    try:
        from . import tools
        if len(argv) != 1 or argv[0] not in tools.TOOLS:
            raise ValueError("usage: python3 -m lsd_mcp.call TOOL (one of %s)"
                             % ", ".join(sorted(tools.TOOLS)))
        text = sys.stdin.read()
        arguments = json.loads(text) if text.strip() else {}
        result = tools.TOOLS[argv[0]](**arguments)
    except Exception as err:
        result = {"error": "%s: %s" % (type(err).__name__, err)}
    sys.stdout = real_stdout
    real_stdout.write(json.dumps(result) + "\n")
    real_stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
