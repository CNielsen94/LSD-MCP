"""The 15 tools as plain functions (standard library only).

server.py wraps each one for MCP; call.py runs one inside a Docker container.
Expected failures become {"error": message} instead of an exception.
"""

import functools

from . import build, lsdfile, lsdsource, models, run, sa, status

EXPECTED_ERRORS = (models.ModelError, build.BuildError, lsdsource.LsdSourceError,
                   lsdfile.LsdFileError)


def guarded(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except EXPECTED_ERRORS as err:
            return {"error": str(err)}
    return wrapper


@guarded
def lsd_status() -> dict:
    return status.status()


@guarded
def list_models(group: str = "models"):
    return models.list_models(group)


@guarded
def read_equations(model: str, group: str = "models"):
    return models.read_equations(model, group)


@guarded
def describe_configuration(model: str, config: str, group: str = "models") -> dict:
    return models.describe_configuration(model, config, group)


@guarded
def copy_model(source: str, name: str, source_group: str = "examples") -> dict:
    return models.copy_model(source, name, source_group)


@guarded
def write_equations(model: str, content: str) -> dict:
    return models.write_equations(model, content)


@guarded
def set_values(model: str, config: str, values: dict, new_config: str = None) -> dict:
    return models.set_values(model, config, values, new_config)


@guarded
def set_run_settings(model: str, config: str, runs: int = None,
                     seed: int = None, steps: int = None) -> dict:
    return models.set_run_settings(model, config, runs, seed, steps)


@guarded
def set_saved(model: str, config: str, names: list, saved: bool = True) -> dict:
    return models.set_saved(model, config, names, saved)


@guarded
def compile_model(model: str, group: str = "models") -> dict:
    folder = models.resolve_model(model, group)
    built = build.compile_model(lsdsource.lsd_root(), folder)
    if built.ok:
        return {"ok": True, "seconds": round(built.seconds, 2), "cached": built.cached}
    return {"ok": False, "errors": built.errors[:30]}


@guarded
def run_configuration(model: str, config: str, seed: int = None, runs: int = None,
                      threads: int = None, timeout_s: int = 600) -> dict:
    return run.run_configuration(model, config, seed, runs, threads, timeout_s)


@guarded
def read_results(model: str, results_file: str, variables: list = None,
                 start: int = None, end: int = None, max_points: int = 200) -> dict:
    return run.read_results(model, results_file, variables, start, end, max_points)


@guarded
def sa_create_design(model: str, config: str, factors: dict, samples: int,
                     method: str = "lhs", validation_samples: int = 10,
                     runs_per_point: int = 2, seed: int = 1,
                     overwrite: bool = False) -> dict:
    return sa.create_design(model, config, factors, samples, method,
                            validation_samples, runs_per_point, seed, overwrite)


@guarded
def sa_run_design(model: str, config: str, threads: int = None,
                  timeout_s: int = 3600) -> dict:
    return sa.run_design(model, config, threads, timeout_s)


@guarded
def sa_analyze(model: str, config: str, variable: str, metamodel: str = "kriging",
               ini_drop: int = 0, n_keep: int = -1) -> dict:
    return sa.analyze(model, config, variable, metamodel, ini_drop, n_keep)


TOOLS = {
    "lsd_status": lsd_status,
    "list_models": list_models,
    "read_equations": read_equations,
    "describe_configuration": describe_configuration,
    "copy_model": copy_model,
    "write_equations": write_equations,
    "set_values": set_values,
    "set_run_settings": set_run_settings,
    "set_saved": set_saved,
    "compile_model": compile_model,
    "run_configuration": run_configuration,
    "read_results": read_results,
    "sa_create_design": sa_create_design,
    "sa_run_design": sa_run_design,
    "sa_analyze": sa_analyze,
}
