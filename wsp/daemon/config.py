"""Configuración de `wspd` desde `~/.config/wsp/config.toml` (todo opcional)."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wsp import paths


class ConfigError(Exception):
    """config.toml ilegible o inválido; el mensaje no incluye valores."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OutboxConfig(_Section):
    rate_per_sec: float = Field(default=1.0, gt=0, le=50)  # mensajes por segundo sostenidos
    burst: int = Field(default=5, ge=1, le=50)  # ráfaga máxima del token bucket
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base: float = Field(default=1.0, gt=0, le=60)  # backoff entre reintentos de envío


class ReconnectConfig(_Section):
    base: float = Field(default=1.0, gt=0, le=60)
    cap: float = Field(default=300.0, gt=0, le=3600)
    jitter: float = Field(default=0.3, ge=0, le=1)  # fracción del retardo


class LogConfig(_Section):
    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")


class Config(_Section):
    outbox: OutboxConfig = OutboxConfig()
    reconnect: ReconnectConfig = ReconnectConfig()
    log: LogConfig = LogConfig()
    retention_days: int = Field(default=365, ge=1, le=36500)


def load_config(path: Path | None = None) -> Config:
    """Carga el TOML; si no existe, defaults. Secciones o claves desconocidas fallan."""
    target = path or paths.config_path()
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return Config()
    except OSError:
        raise ConfigError("no se pudo leer config.toml") from None
    try:
        return Config.model_validate(tomllib.loads(raw.decode("utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigError("config.toml no es TOML válido") from None
    except ValidationError as exc:
        where = "; ".join(".".join(str(p) for p in e["loc"]) or "-" for e in exc.errors()[:5])
        raise ConfigError(f"config.toml inválido en: {where}") from None
