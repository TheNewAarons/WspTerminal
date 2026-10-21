"""`wsp config`: muestra la configuración efectiva y su ruta."""

import sys
from typing import Any, TextIO

from wsp import paths
from wsp.daemon.config import Config, ConfigError, load_config


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(v)


def to_toml(data: dict[str, Any]) -> str:
    """TOML mínimo (escalares y una capa de secciones) de la config ya validada."""
    top = [f"{k} = {_scalar(v)}" for k, v in data.items() if not isinstance(v, dict)]
    out = list(top)
    for k, v in data.items():
        if isinstance(v, dict):
            out += ["", f"[{k}]", *(f"{a} = {_scalar(b)}" for a, b in v.items())]
    return "\n".join(out).strip() + "\n"


def show(out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    path = paths.config_path()
    try:
        cfg: Config = load_config(path)
    except ConfigError as e:
        err.write(f"wsp: {e}\n")
        return 1
    state = "" if path.exists() else " (no existe; valores por defecto)"
    out.write(f"# {path}{state}\n{to_toml(cfg.model_dump())}")
    return 0
