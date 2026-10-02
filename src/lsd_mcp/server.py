"""MCP server: thin FastMCP wrappers around the lsd_mcp modules."""

from mcp.server.mcpserver import MCPServer

from . import backend

mcp = MCPServer(
    "lsd-mcp",
    instructions=(
        "Work with LSD (Laboratory for Simulation Development) models. "
        "Models you edit live in the LSD_MODELS folder; copy an example with "
        "copy_model first. Typical flow: list_models, describe_configuration, "
        "set_values / set_run_settings / set_saved, run_configuration, "
        "read_results. Sensitivity analysis: sa_create_design, sa_run_design, "
        "sa_analyze."))

# --- inspect ------------------------------------------------------------------

@mcp.tool()
def lsd_status() -> dict:
    """Show the setup: LSD source root and tag, models folder, whether a C++
    compiler was found, whether LSD's command-line utilities are built yet
    (they are built automatically on first use), and
    whether Rscript and the R package LSDsensitivity are available."""
    return backend.call("lsd_status", {})


@mcp.tool()
def list_models(group: str = "models") -> list | dict:
    """List models. group='models' is the user's LSD_MODELS folder;
    group='examples' is the Example folder of the LSD distribution (read-only).
    A model is a folder with an equation file (fun_*.cpp) and at least one .lsd
    configuration. Returns relative path, title, equation file and
    configuration names for each."""
    return backend.call("list_models", locals())


@mcp.tool()
def read_equations(model: str, group: str = "models") -> str | dict:
    """Return the text of the model's equation file (the C++ source with the
    EQUATION(...) blocks)."""
    return backend.call("read_equations", locals())


@mcp.tool()
def describe_configuration(model: str, config: str, group: str = "models") -> dict:
    """Describe a .lsd configuration: the object tree with instance counts,
    and for each element (variable, parameter or function) its type, number of
    lags, whether it is saved to the result files, and its value (a single
    value if all instances are equal, otherwise min, max and count), plus the
    run settings SIM_NUM, SEED, MAX_STEP and EQUATION. `config` is the file name
    with or without .lsd."""
    return backend.call("describe_configuration", locals())


# --- edit ---------------------------------------------------------------------

@mcp.tool()
def copy_model(source: str, name: str, source_group: str = "examples") -> dict:
    """Copy a model folder (source files only, no binaries or results) into the
    models folder as `name`, so it can be edited. Fails if `name` exists."""
    return backend.call("copy_model", locals())


@mcp.tool()
def write_equations(model: str, content: str) -> dict:
    """Replace the model's equation file with `content` (complete C++ source).
    The previous version is kept as <file>.bak. Only models in the models
    folder can be written."""
    return backend.call("write_equations", locals())


@mcp.tool()
def set_values(model: str, config: str, values: dict[str, float],
               new_config: str | None = None) -> dict:
    """Set element values in a configuration, using LSD's own lsd_confgen.
    `values` maps element name to a number; every instance of the element gets
    the value (for variables, the value at lag -1). Writes new_config.lsd, or
    replaces config.lsd (the old file is kept as .bak)."""
    return backend.call("set_values", locals())


@mcp.tool()
def set_run_settings(model: str, config: str, runs: int | None = None,
                     seed: int | None = None, steps: int | None = None) -> dict:
    """Edit SIM_NUM (runs), SEED and MAX_STEP (time steps) of a configuration."""
    return backend.call("set_run_settings", locals())


@mcp.tool()
def set_saved(model: str, config: str, names: list[str], saved: bool = True) -> dict:
    """Mark the named variables or parameters as saved (or not saved) to the
    result files. Only saved elements appear in results."""
    return backend.call("set_saved", locals())


# --- compile and run ------------------------------------------------------------

@mcp.tool()
def compile_model(model: str, group: str = "models") -> dict:
    """Compile the model's equation file into a headless LSD program (built
    into the cache, not the model folder). Returns ok and build time, or the
    first compiler errors as file:line: message. Nothing is rebuilt if the
    equation file is unchanged."""
    return backend.call("compile_model", locals())


@mcp.tool()
def run_configuration(model: str, config: str, seed: int | None = None,
                      runs: int | None = None, threads: int | None = None,
                      timeout_s: int = 600) -> dict:
    """Compile if needed and run a configuration of a model in the models
    folder. seed and runs override SEED and SIM_NUM of the file. threads: with
    runs > 1, the number of runs executed in parallel; otherwise threads for
    models that use parallel objects. Results are written next to the
    configuration as <config>_<seed>.res.gz. Returns the files written and, for
    the first run, the last value and mean of each saved series (at most 50
    series). With runs > 1 (sequential) LSD also writes <config>_<first>_<last>.tot.gz;
    with threads set the runs are parallel and no totals file is written.
    Row 0 of a result file holds initial values. On failure returns
    the tail of LSD's output."""
    return backend.call("run_configuration", locals())


@mcp.tool()
def read_results(model: str, results_file: str, variables: list[str] | None = None,
                 start: int | None = None, end: int | None = None,
                 max_points: int = 200) -> dict:
    """Read time series from one result file (.res.gz) in the model folder.
    variables filters by name ('Mean') or name with instance ('Mean 1');
    start/end select time steps; the series are thinned evenly to max_points.
    At most 50 series are returned."""
    return backend.call("read_results", locals())


# --- sensitivity analysis -------------------------------------------------------

@mcp.tool()
def sa_create_design(model: str, config: str, factors: dict[str, list[float | str]],
                     samples: int, method: str = "lhs", validation_samples: int = 10,
                     runs_per_point: int = 2, seed: int = 1,
                     overwrite: bool = False) -> dict:
    """Create a design of experiments for a meta-model sensitivity analysis.
    factors maps a parameter name to [min, max], or [min, max, "int"] for
    integers. Samples `samples` points (method 'lhs' Latin hypercube or
    'random') plus `validation_samples` uniform out-of-sample points, and
    writes LSD's own file layout in the model folder: <config>.sa, the two
    design tables <config>_1_S.csv and <config>_S+1_S+V.csv, and numbered
    configurations <config>_1.lsd ... Each point runs runs_per_point times
    (at least 2) with its own seeds. Refuses to replace an existing design
    unless overwrite=True. Variables whose results will be analysed must be
    saved (set_saved)."""
    return backend.call("sa_create_design", locals())


@mcp.tool()
def sa_run_design(model: str, config: str, threads: int | None = None,
                  timeout_s: int = 3600) -> dict:
    """Run every numbered configuration of the design in parallel processes
    (default: one per CPU). Points whose result files already exist are skipped.
    Returns counts of points done, run, failed and not started."""
    return backend.call("sa_run_design", locals())


@mcp.tool()
def sa_analyze(model: str, config: str, variable: str, metamodel: str = "kriging",
               ini_drop: int = 0, n_keep: int = -1) -> dict:
    """Fit a meta-model ('kriging' or 'polynomial') to the design results for
    one saved variable and compute its Sobol decomposition, using LSD's R
    package LSDsensitivity. Returns the fit quality (Q2 for kriging, R2 for
    polynomial) and a table with direct effects and interactions per factor.
    ini_drop drops initial time steps, n_keep keeps that many (-1 = all). The
    response is the mean of the variable over the kept steps, averaged over
    runs. The polynomial meta-model fails when a design point has a negative
    mean response (LSD weights points by mean/SD); use kriging then. Needs
    Rscript and LSDsensitivity; says so if they are missing."""
    return backend.call("sa_analyze", locals())


def main():
    mcp.run()


if __name__ == "__main__":
    main()
