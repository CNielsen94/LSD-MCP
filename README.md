# lsd-mcp

An MCP server that lets an agent work with [LSD](https://github.com/marcov64/Lsd)
(Laboratory for Simulation Development, Valente and Pereira): inspect and edit
models, compile and run them headless, and run sensitivity analyses
(meta-models with Sobol indices, elementary effects). It uses LSD's own engine,
command-line utilities (`lsd_confgen`, `lsd_getsaved`, ...), design code
and R package `LSDsensitivity`; it does not re-implement them. Built against release tag `8.1-stable-5`.

It runs LSD either on the host or, with the Docker backend below, inside the
container of [Docker_LSD_setup](https://github.com/CNielsen94/Docker_LSD_setup).

## Install

Needs Python 3.10+, `uv`, a C++ compiler (`c++`, `g++` or `clang++`), `zlib`,
and `git` (to fetch the LSD source on first use). `Rscript` with the R package
`LSDsensitivity` is only needed for `sa_analyze`.

```
uv sync
uv run pytest -q
```

## Environment variables (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `LSDROOT` | unset | An LSD folder containing `src/`. If unset, the tag is fetched (sparse clone of `src`, `Example`, `Rpkg`). |
| `LSD_TAG` | `8.1-stable-5` | Tag to fetch. |
| `LSD_MCP_HOME` | `~/.cache/lsd-mcp` | Fetched source and all build output. |
| `LSD_MODELS` | `~/lsd-models` | Your models. The only place the edit and run tools write. |
| `LSD_MCP_RSCRIPT` | `Rscript` | R interpreter for `sa_analyze`. |

## Tools

Inspect: `lsd_status`, `list_models`, `read_equations`, `describe_configuration`.
Edit: `copy_model`, `create_model`, `write_equations`, `edit_structure`, `set_values`, `set_run_settings`, `set_saved`.
Compile and run: `compile_model`, `run_configuration`, `read_results`.
Sensitivity analysis: `sa_create_design`, `sa_run_design`, `sa_analyze`.
`set_values` and the factors of `sa_create_design` take `"Name -k"` for the k-th lag of a variable.

`create_model` makes a new model folder as LSD's model manager (LMM) does: the
equation file `fun_<name>.cpp` from LSD's template, `model_options.txt`,
`modelinfo.txt`, `description.txt` and a `Sim1.lsd` holding only Root.
`edit_structure` applies a list of operations (add object, parameter, variable
or function, rename, delete, set the number of instances, set the value of each
instance, set a description) to a configuration with LSD's own functions,
through `lsd_edit` (`src/lsd_mcp/structure.cpp`), and saves with LSD's
`save_configuration`; if one operation fails nothing is written. With these, a
model can be built from nothing: `create_model`, `edit_structure`,
`write_equations`, `run_configuration`.

`sa_create_design` makes one of four designs. `lhs` and `random` are sampled in
Python and written through `lsd_confgen`. `nolh` (near-orthogonal Latin
hypercube, LSD's own tables, optionally extended) and `ee` (elementary effects,
Morris; trajectories, levels, jump, pool) are made by LSD's own design code
(`design` and `sensitivity_doe` in `set_all.cpp`) through `lsd_doe`, a small
program of ours (`src/lsd_mcp/doe.cpp`) that includes LSD's file unmodified and
makes the calls the interface makes. A `nolh` design gets an out-of-sample set
from LSD's Monte Carlo range sampling, as the interface offers after NOLH; an
`ee` design has none. Every design also gets `<config>_design.json` (method and
parameters). `sa_analyze` fits a Kriging or polynomial meta-model with Sobol
indices, or, for an `ee` design, runs LSD's elementary effects analysis and
returns mu, mu_star, sigma, se and p_value per factor. For an `ee` design made in
LSD's interface (no design file) pass `metamodel="ee"` with its `levels` and
`jump`.

## Register with Claude Code

```
claude mcp add lsd -- uv run --project /path/to/lsd-mcp lsd-mcp
```

Add `-e LSD_MODELS=/path/to/models` to change the models folder.

## Docker backend

With `LSD_MCP_BACKEND=docker` the server still runs on the host, but every tool
call is executed inside a running LSD container by the same standard-library
code, which `lsd-mcp` copies into the container (`docker cp`, re-copied when the
source changes). It never starts, stops or removes a container.

The container comes from [Docker_LSD_setup](https://github.com/CNielsen94/Docker_LSD_setup),
a separate repository that runs LSD in Docker with a browser desktop, R and
LSD's R packages. This backend needs nothing installed on the host besides
Docker and `uv`: the compiler, LSD and R are the container's.

| Variable | Default | Meaning |
|---|---|---|
| `LSD_MCP_BACKEND` | `local` | `docker` forwards tool calls into the container. |
| `LSD_CONTAINER` | `lsd` | Name of the running container. |
| `LSD_MCP_CONTAINER_LSDROOT` | `/home/lsd/LSD` | LSD folder inside the container. |
| `LSD_MCP_CONTAINER_MODELS` | `/home/lsd/LSD/Work` | Models folder inside the container. |

```
claude mcp add lsd-docker -e LSD_MCP_BACKEND=docker -- uv run --project /path/to/lsd-mcp lsd-mcp
```

Start the container with `./run.sh` in your Docker_LSD_setup folder first. Two
things to know: models live in the container's Work folder, which is shared
with the host and shown in LMM as "Work in Progress" (`copy_model` adds a
`modelinfo.txt` so LMM lists the copy); and the build cache inside the
container is lost when the container is recreated, so the next call rebuilds
it.

## Limits

- Kriging can fail with "the leading minor ... is not positive" (covariance matrix
  not positive definite), typically when design points nearly coincide, for example
  integer factors with few levels. `sa_analyze` explains this; try the polynomial
  meta-model or more spread-out points. `sa_create_design` warns about integer
  factors with fewer levels than samples / 4.
- LSD's polynomial meta-model weights design points by mean/SD of the response
  and fails when a point has a negative mean (a bug in LSDsensitivity, not worked
  around here). `sa_analyze` says so; use `metamodel="kriging"`.
- LSD's polynomial meta-model also needs at least two factors (its package builds
  a broken formula for one); `sa_analyze` refuses it for a single factor.
- LSD cannot load a configuration with `SEED` below 1 (27 shipped examples have
  `SEED 0`); the tools say so and `set_run_settings(seed=1)` fixes it.
- Totals files (`.tot.gz`) have no header and one row per run; `read_results`
  does not read them and names the `.res.gz` files instead.
- Sensitivity analysis covers Latin hypercube, uniform random and NOLH designs
  with a Kriging or polynomial meta-model, and elementary effects designs.
- Factors are parameters or the initial values of variables (`"X"` is the first
  lag, `"X -2"` the second). One element can be one factor only: LSD's design
  table names a factor by its label alone. In `lsd_confgen`'s own files a negative lag
  always means the first lag (`confgen.cpp`, `change_configuration`), so the tools
  send it the positive number it does honour.
- An elementary effects design made on macOS differs from one made on Linux
  (including in Docker) for the same seed, because LSD shuffles trajectories with
  the C++ standard library's `shuffle`, which differs between libc++ and
  libstdc++. Both are valid designs. On Linux the design is byte-identical to
  what LSD's interface writes for the same settings and seed; NOLH and Monte Carlo
  range designs are identical on both platforms (`tests/data/doe_gui` holds the
  interface's files, `tests/test_doe.py` and the Docker tests compare them).
- `edit_structure` saves with LSD's `save_configuration`, so the file takes LSD's
  current layout. Of the 151 shipped example configurations LSD can load, 32 come
  back byte-identical from an empty edit. The rest differ only in layout (no data):
  descriptions without text were written as "(no description available)" in older
  files and are now empty, `MODELREPORT` lost a leading space, an empty `EQ_FILE`
  section is added, and the note LSD generates about the initial values of a
  variable with no lags is dropped (LSD's `save_description`). If the equation
  file is not in the model folder LSD would blank the `EQUATION` name; the tool
  puts it back. New instances copy the object's first instance. Objects always
  keep at least one instance.
- Models made by `create_model` use LMM's compiler options (`SWITCH_CC=-O0 -ggdb3`),
  so they compile without optimisation; edit `model_options.txt` for speed.
- NOLH tables cover 1 to 100 factors (17 to 512 points); `samples` does not apply.
- Without the Docker backend the compiler and utilities run on the host.
- Models are compiled headless (`-D_NW_`). Eight of the 45 example models use
  GUI-only features (Tcl calls, the debugger variables) and do not compile this way.
- `lsd_confgen` in 8.1-stable-5 generates at most as many configurations per
  call as the CSV has rows; `sa_create_design` splits the work accordingly.
- `lsd_confgen` drops everything after `MODELREPORT` (descriptions, embedded
  equations). `set_values` appends it again from the original file; the numbered
  design configurations are left as LSD writes them.
- `sa_analyze` is tested on Linux with R 4.3.3 and LSDsensitivity 1.2.3 (the tests are skipped where R is missing).
