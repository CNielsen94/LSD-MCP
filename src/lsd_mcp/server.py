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
    configuration. Returns relative path, title, equation file, source files
    (*.cpp, *.h, *.hpp in the folder) and configuration names for each."""
    return backend.call("list_models", locals())


@mcp.tool()
def read_equations(model: str, group: str = "models",
                   file: str | None = None) -> str | dict:
    """Return the text of the model's equation file (the C++ source with the
    EQUATION(...) blocks). Models whose equations are spread over several files
    list them as source_files in list_models; pass such a plain file name
    (.cpp, .h or .hpp, no path) as `file` to read it instead."""
    return backend.call("read_equations", locals())


@mcp.tool()
def describe_configuration(model: str, config: str, group: str = "models",
                           object: str | None = None, detail: str = "full") -> dict:
    """Describe a .lsd configuration: the object tree with instance counts,
    and for each element (variable, parameter or function) its type, number of
    lags, whether it is saved to the result files, and its value (a single
    value if all instances are equal, otherwise min, max and count), plus the
    run settings SIM_NUM, SEED, MAX_STEP and EQUATION. `config` is the file name
    with or without .lsd. Set only when they apply: computed=false on objects
    (not computed), and debug, plot, parallel, saved_separately on elements.
    For big models pass object='Name' to describe one object, or
    detail='names' for the tree with instance counts and just the element
    names grouped by type."""
    return backend.call("describe_configuration", locals())


# --- edit ---------------------------------------------------------------------

@mcp.tool()
def copy_model(source: str, name: str, source_group: str = "examples") -> dict:
    """Copy a model folder (source files only: no binaries, results, backups,
    numbered design configurations, design tables or <config>_sa folders) into
    the models folder as `name`, so it can be edited. Fails if `name` exists."""
    return backend.call("copy_model", locals())


@mcp.tool()
def write_equations(model: str, content: str, file: str | None = None) -> dict:
    """Replace the model's equation file with `content` (complete C++ source),
    or the source file named by `file` (plain name, .cpp, .h or .hpp; a new
    file is created if it does not exist). The version before the latest write
    is kept as <file>.bak and the one before the first write as <file>.orig.
    Only models in the models folder can be written."""
    return backend.call("write_equations", locals())


@mcp.tool()
def set_values(model: str, config: str, values: dict[str, float],
               new_config: str | None = None) -> dict:
    """Set element values in a configuration, using LSD's own lsd_confgen.
    `values` maps element name to a number; every instance of the element gets
    the value (for variables, the value at lag -1). Writes new_config.lsd, or
    replaces config.lsd (the old file is kept as .bak). The result says
    whether an existing file was replaced (replaced, backup)."""
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
    series, those whose name has few instances first; the rest are counted). With runs > 1 (sequential) LSD also writes <config>_<first>_<last>.tot.gz;
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
    start/end select time steps (the row number in the file is the time step;
    row 0 holds initial values; start must not exceed end; max_points >= 1); the series are thinned evenly to max_points.
    At most 50 series are returned."""
    return backend.call("read_results", locals())


# --- sensitivity analysis -------------------------------------------------------

@mcp.tool()
def sa_create_design(model: str, config: str, factors: dict[str, list[float | str]],
                     samples: int | None = None, method: str = "lhs",
                     validation_samples: int = 10, runs_per_point: int = 2,
                     seed: int = 1, overwrite: bool = False, extended: bool = False,
                     trajectories: int = 10, levels: int = 4, jump: int = 2,
                     pool: int = 100) -> dict:
    """Create a design of experiments for a sensitivity analysis and write it in
    LSD's own file layout in the model folder: <config>.sa, the design table
    <config>_1_N.csv (and for meta-model designs the out-of-sample table
    <config>_N+1_N+V.csv), numbered configurations <config>_1.lsd ..., and
    <config>_design.json (our file: method and parameters).
    factors maps a parameter name to [min, max], or [min, max, "int"] for
    integers. method:
    'lhs' (Latin hypercube) or 'random': `samples` points (required, at least 2)
    plus validation_samples uniform out-of-sample points, for a Kriging or
    polynomial meta-model.
    'nolh': near-orthogonal Latin hypercube made by LSD's own NOLH tables. LSD
    chooses the table from the number of factors (17 points for 1-7 factors,
    33 for 8-11, 65 for 12-16, 129 for 17-22, 257 for 23-29, 512 for 30-100;
    extended=True uses LSD's extended size for the table: 33, 65, 129, 257,
    257, 512), so `samples` is not used; the result says how
    many points LSD produced. Plus validation_samples out-of-sample points by
    LSD's Monte Carlo range sampling. For meta-model analysis.
    'ee': elementary effects (Morris) design made by LSD's own code:
    trajectories (default 10) each of factors + 1 points, chosen from a pool of
    `pool` random trajectories (default 100) on `levels` levels (even, default
    4) with `jump` (default 2). No out-of-sample set; validation_samples and
    samples are ignored. An ee design made on macOS differs from one made on
    Linux for the same seed (the C++ library's shuffle differs); both are valid
    designs. Analyse it with sa_analyze (metamodel 'ee').
    Each point runs runs_per_point times (at least 2) with its own seeds.
    seed seeds the sampling. Refuses to replace an existing design unless
    overwrite=True. A warning is returned for integer factors with few levels
    (a hypercube collapses onto them). Variables whose results will be analysed
    must be saved (set_saved)."""
    return backend.call("sa_create_design", locals())


@mcp.tool()
def sa_run_design(model: str, config: str, threads: int | None = None,
                  timeout_s: int = 3600) -> dict:
    """Run every numbered configuration of the design in parallel processes
    (default: one per CPU). Points whose result files already exist are skipped.
    Returns counts of points done, run, failed and not started."""
    return backend.call("sa_run_design", locals())


@mcp.tool()
def sa_analyze(model: str, config: str, variable: str, metamodel: str | None = None,
               ini_drop: int = 0, n_keep: int = -1, r_seed: int = 1,
               levels: int | None = None, jump: int | None = None) -> dict:
    """Analyse the design results for one saved variable with LSD's R package
    LSDsensitivity. metamodel 'kriging' (default for lhs, random and nolh
    designs) or 'polynomial' fits a meta-model and computes its Sobol
    decomposition: returns the fit quality (Q2 for kriging, R2 for polynomial)
    and a table with direct effects and interactions per factor. For a design
    made with method 'ee' the analysis is elementary effects (metamodel 'ee',
    chosen automatically): returns per factor mu, mu_star, sigma, se and
    p_value (parameters scaled to [0, 1]; mu_star is the overall effect, sigma
    non-linear or interaction effects, p_value tests mu_star = 0), sorted by
    mu_star. Kriging or polynomial on an ee design, or ee on another design,
    is an error. For an ee design made in LSD's own interface (no design file)
    pass metamodel='ee' with its levels and jump.
    ini_drop drops initial time steps, n_keep keeps that many (-1 = all). The
    response is the mean of the variable over the kept steps, averaged over
    runs. Variables whose name starts with '_' work; with several instances
    only the first instance is analysed. ini_drop must be below MAX_STEP and
    ini_drop + n_keep at most MAX_STEP. r_seed seeds R's random
    numbers, so identical calls give identical results. A warning is added when
    a meta-model fit is below 0.5. The polynomial meta-model fails when a design
    point has a negative mean response (LSD weights points by mean/SD) and needs
    at least two factors; use kriging then. Kriging can fail numerically when
    design points nearly coincide (the message says so; try polynomial). Needs
    Rscript and LSDsensitivity; says so if they are missing."""
    return backend.call("sa_analyze", locals())


def main():
    mcp.run()


if __name__ == "__main__":
    main()
