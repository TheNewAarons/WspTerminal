"""Rutas centralizadas de wsp. `WSP_HOME` las redirige (tests)."""

import os
from pathlib import Path

_DIR_MODE = 0o700
_SOCKET_MAX = 104  # límite de AF_UNIX en macOS


def _root() -> Path | None:
    home = os.environ.get("WSP_HOME")
    return Path(home).expanduser().resolve() if home else None


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
    """Crea los directorios con permisos 700; rechaza symlinks y dueños ajenos."""
    for d in (data_dir(), state_dir(), config_dir()):
        if d.is_symlink():
            raise PermissionError("directorio de wsp no puede ser un symlink")
        d.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        if d.stat().st_uid != os.getuid():
            raise PermissionError("directorio de wsp pertenece a otro usuario")
        d.chmod(_DIR_MODE)
    if len(os.fsencode(socket_path())) >= _SOCKET_MAX:
        raise OSError("ruta del socket excede el límite de AF_UNIX (104 bytes)")
