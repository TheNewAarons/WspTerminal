# wsp — Cliente de WhatsApp para la terminal de macOS

Interfaz de LÍNEA (sin curses/paneles/Textual), tiempo real, un solo usuario. Todo en español salvo código.

## Stack (cerrado; nada fuera de esta lista sin preguntar)
Python 3.12+ con uv · neonize (whatsmeow) · SQLite stdlib WAL + migraciones SQL propias (`wsp/store/migrations/NNNN_*.sql`, según `PRAGMA user_version`) · pydantic v2 · prompt_toolkit (PromptSession + patch_stdout) · keyring (Llavero) · launchd · pytest, ruff, mypy --strict, pre-commit + detect-secrets.

## Arquitectura
- `wsp/daemon/` → `wspd`: ÚNICO proceso con la sesión de WhatsApp (LaunchAgent). Persiste en SQLite y emite a clientes por socket Unix `~/.local/state/wsp/wspd.sock` (600), JSON por líneas versionado; comandos con `request_id` idempotente.
- `wsp/cli/` → `wsp`: REPL en vivo; al reconectar pide `since=<ts>` y el daemon rellena desde SQLite.
- `wsp/store/`: SQLite + migraciones (solo el daemon escribe).
- `wsp/protocol/`: modelos pydantic compartidos.
- Datos: `~/.local/share/wsp/` (DB + sesión, 600). Config: `~/.config/wsp/config.toml`.

## Funcionalidad CLI
`wsp pair [--phone]`, `wsp daemon install|uninstall|status|logs`, `wsp` (REPL), `wsp send <chat> <texto>`, `wsp tail [chat]`. Formato `[14:02] Juan › hola`, `[14:02] Familia / Ana › …`. Prompt `› ` / `Juan › `. Comandos: /chats [n], /open, /back, /r <n> <texto>, /react <n> <emoji>, /search, /mute <chat> <dur>, /history [n], /help, /quit. Estado ✓ ✓✓ ✗(motivo). Notificación osascript (desactivable). Respeta NO_COLOR/--no-color. Daemon: backoff exponencial + jitter, token bucket de envíos, logs sin contenido ni números completos.

## Comandos
`uv run pytest` · `uv run ruff check` · `uv run ruff format --check` · `uv run mypy wsp`. Todos en verde para cerrar fase.

## Reglas
- Zero Fluff: sin intro/despedida, explicación ≤2 líneas. Commits Conventional (`feat:`, `fix:`, …). Funciones exportadas con tipo de retorno explícito.
- Anti-bucle: 2 intentos consecutivos fallidos → revertir y replantear en `TASK_PLAN.md`.
- SQL siempre parametrizado. Sin PII/contenido en logs. Sesión y DB fuera de git.
- Sin CI/GitHub Actions. Ramas por fase (`phase-N-<tema>`), un PR por fase vía `gh pr create`.

## Orquestación
Yo implemento en la sesión principal; delego así (contexto mínimo: archivos, objetivo, criterio) y verifico corriendo los comandos yo mismo:
- Tiers: 1 `claude` (arquitectura, concurrencia, seguridad) · 2 `claude-ds`/`claude-qwen` (features) · 3 `claude-kimi` (bulk). Tarea >3 archivos o >200 líneas → delegar a Tier 2/3.
- `planner` (3): crea/mantiene `TASK_PLAN.md`; nunca toca código; al inicio y cierre de cada fase.
- `bulk-agent` (3): tests, docstrings, type hints, README/man, fixtures, arreglos ruff/mypy.
- `refactor-agent` (2): al cerrar fases 4 y 6; duplicación, async bloqueante, cancelación, latencia p50/p95 antes/después; sin cambiar comportamiento.
- `database-agent` (2): TODO cambio de esquema; índices (chat+timestamp, wa_message_id), FTS5, upserts idempotentes, EXPLAIN con 100k mensajes, retención.
- `security-reviewer` (1): antes de CADA PR sobre `git diff main...HEAD`; bloqueante/alto/medio/bajo con archivo:línea y fix. Bloqueantes → no merge.
- Nunca dos agentes editan los mismos archivos a la vez.

## Fases
0 Bootstrap · 1 Store/migraciones · 2 Protocolo + socket · 3 Daemon neonize · 4 LaunchAgent + `wsp daemon` (+refactor) · 5 REPL en vivo · 6 Comandos, notificaciones, send/tail (+refactor). Cierre: tests verdes → security-reviewer → PR → planner actualiza `TASK_PLAN.md`.
