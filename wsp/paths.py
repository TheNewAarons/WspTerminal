"""Rutas centralizadas de wsp. `WSP_HOME` las redirige (tests)."""

import os
from pathlib import Path

_DIR_MODE = 0o700


def _root() -> Path | None:
    home = os.environ.get("WSP_HOME")
    return Path(home).expanduser() if home else None


def data_dir() -> Path:
    """Datos: DB y sesión de neonize."""
    root = _root()
    return root / "data" if root else Path.home() / ".local" / "share" / "wsp"


def state_dir() -> Path:
    """Estado: socket y logs."""
    root = _root()
    return root / "state" if root else Path.home() / ".local" / "state" / "wsp"


def config_dir() -> Path:
    """Config: config.toml."""
    root = _root()
    return root / "config" if root else Path.home() / ".config" / "wsp"


def db_path() -> Path:
    return data_dir() / "wsp.db"


def session_path() -> Path:
    return data_dir() / "session.db"


def socket_path() -> Path:
    return state_dir() / "wspd.sock"


def config_path() -> Path:
    return config_dir() / "config.toml"


def ensure_dirs() -> None:
    """Crea los directorios con permisos 700."""
    for d in (data_dir(), state_dir(), config_dir()):
        d.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        d.chmod(_DIR_MODE)
