"""One-call summary of the setup, for lsd_status."""

from . import build, config, lsdsource, sa


def status() -> dict:
    info = {"lsd_tag": config.lsd_tag(), "models_folder": str(config.models_dir()),
            "cache_folder": str(config.home())}
    if lsdsource.uses_fetched_source():
        root = config.home() / ("Lsd-" + config.lsd_tag())
        info["lsd_source"] = "fetched"
        if not (root / "src" / "lsdmain.cpp").is_file():
            info["lsd_root"] = None
            info["note"] = "LSD source not fetched yet; it is fetched on first use"
            root = None
    else:
        info["lsd_source"] = "LSDROOT"
        try:
            root = lsdsource.lsd_root()
        except lsdsource.LsdSourceError as err:
            info["note"] = str(err)
            root = None
    if root is not None:
        info["lsd_root"] = str(root)
    cc = build.compiler()
    info["compiler"] = cc or None
    info["utilities_built"] = bool(root) and build.utilities_built(root)
    info.update(sa.rscript_status())
    return info
