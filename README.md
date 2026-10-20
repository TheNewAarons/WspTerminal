# wsp

Cliente de WhatsApp minimalista para la terminal de macOS. Interfaz de línea, tiempo real, un solo usuario.

- `wspd`: daemon con la sesión de WhatsApp (neonize) y persistencia en SQLite.
- `wsp`: REPL que se conecta al daemon por socket Unix.

## Desarrollo

```
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy wsp
```

Rutas: datos `~/.local/share/wsp`, estado/socket `~/.local/state/wsp`, config `~/.config/wsp` (`WSP_HOME` las redirige en tests).

Fases y estado: `TASK_PLAN.md`. Convenciones: `CLAUDE.md`.
