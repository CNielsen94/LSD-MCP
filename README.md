# lsd-mcp

An MCP server that lets an agent work with [LSD](https://github.com/marcov64/Lsd)
(Laboratory for Simulation Development, Valente and Pereira): inspect and edit
models, compile and run them headless, and run a meta-model sensitivity
analysis. It uses LSD's own engine and command-line utilities (`lsd_confgen`,
`lsd_getsaved`, ...) and LSD's R package `LSDsensitivity`; it does not
re-implement them. Built against release tag `8.1-stable-5`.

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
Edit: `copy_model`, `write_equations`, `set_values`, `set_run_settings`, `set_saved`.
Compile and run: `compile_model`, `run_configuration`, `read_results`.
Sensitivity analysis: `sa_create_design`, `sa_run_design`, `sa_analyze`.

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

| Variable | Default | Meaning |
|---|---|---|
| `LSD_MCP_BACKEND` | `local` | `docker` forwards tool calls into the container. |
| `LSD_CONTAINER` | `lsd` | Name of the running container. |
| `LSD_MCP_CONTAINER_LSDROOT` | `/home/lsd/LSD` | LSD folder inside the container. |
| `LSD_MCP_CONTAINER_MODELS` | `/home/lsd/LSD/Work` | Models folder inside the container. |

```
claude mcp add lsd-docker -e LSD_MCP_BACKEND=docker -- uv run --project /path/to/lsd-mcp lsd-mcp
```

Start the container with `./run.sh` in the Docker_LSD_setup folder first. Two
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
- Version 1. Sensitivity analysis covers Latin hypercube and uniform random
  designs with a Kriging or polynomial meta-model. Elementary effects and LSD's
  NOLH tables come in a later version.
- Without the Docker backend the compiler and utilities run on the host.
- Models are compiled headless (`-D_NW_`). Eight of the 45 example models use
  GUI-only features (Tcl calls, the debugger variables) and do not compile this way.
- `lsd_confgen` in 8.1-stable-5 generates at most as many configurations per
  call as the CSV has rows; `sa_create_design` splits the work accordingly.
- `lsd_confgen` drops everything after `MODELREPORT` (descriptions, embedded
  equations). `set_values` appends it again from the original file; the numbered
  design configurations are left as LSD writes them.
- `sa_analyze` is tested on Linux with R 4.3.3 and LSDsensitivity 1.2.3 (the test is skipped where R is missing).
