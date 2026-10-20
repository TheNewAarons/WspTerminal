# TASK_PLAN — wsp

Estados: `[ ]` pendiente · `[~]` en curso · `[x]` hecho.
Ids: `F<fase>.<n>`. Anti-bucle: 2 fallos consecutivos en una tarea → revertir y replantear aquí.
Ramas: F0 `feat/f0-bootstrap`; F3 `feat/f3-daemon`; resto `phase-N-<tema>`. Un PR por fase (`gh pr create`), sin CI.

## Referencias

- Gates: `uv run pytest` · `uv run ruff check` · `uv run ruff format --check` · `uv run mypy wsp`
- Rutas: migraciones `wsp/store/migrations/NNNN_*.sql` · tests `tests/{store,protocol,daemon,cli}/` · `wsp/paths.py` (`WSP_HOME`)
- Entrypoints: `wsp.cli.main:app` (CLI) · `wsp.daemon.main:run` (daemon)
- Deps runtime: neonize, prompt_toolkit, pydantic>=2, keyring, structlog, rapidfuzz (autocompletado difuso `/open`), qrcode (QR de pairing)
- Deps dev: pytest, pytest-asyncio, ruff, mypy, pre-commit
- Agentes: `orquestador` (sesión principal), `database-agent`, `bulk-agent`, `refactor-agent`, `security-reviewer`, `planner`
- Cierre de cada fase: gates → security-reviewer sobre `git diff main...HEAD` → PR → planner. Bloqueantes de seguridad = no merge.
- Esquema (tablas, índices, FTS5, triggers): siempre `database-agent`.

---

## Fase 0 — Bootstrap

- [x] **F0.1** pyproject (uv) + deps runtime y dev · Agente: orquestador · Deps: — · Aceptación: `uv sync` exit 0
- [x] **F0.2** Estructura `wsp/{daemon,cli,store,protocol}` y entrypoints · Agente: orquestador · Deps: F0.1 · Aceptación: `uv run python -c "import wsp.cli.main, wsp.daemon.main"`
- [x] **F0.3** ruff + mypy --strict configurados · Agente: orquestador · Deps: F0.1 · Aceptación: `uv run ruff check && uv run mypy wsp`
- [x] **F0.4** pre-commit (ruff, ruff-format, mypy, detect-secrets + `.secrets.baseline`) · Agente: orquestador · Deps: F0.3 · Aceptación: `uv run pre-commit run --all-files`
- [x] **F0.5** `.gitignore` (DB, sesión, `.venv`, `.env`, secrets) · Agente: orquestador · Deps: — · Aceptación: `git status --short` sin artefactos de datos
- [x] **F0.6** `wsp/paths.py` (`WSP_HOME`) + tests · Agente: orquestador · Deps: F0.2 · Aceptación: `uv run pytest tests/test_paths.py -q`
- [x] **F0.7** `CLAUDE.md` al stack wsp · Agente: orquestador · Deps: — · Aceptación: `grep -c "Fases" CLAUDE.md` ≥ 1
- [x] **F0.8** `README.md` · Agente: bulk-agent · Deps: F0.2 · Aceptación: `test -s README.md`
- [x] **F0.9** Gates en verde · Agente: orquestador · Deps: F0.1–F0.8 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [x] **F0.10** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F0.9 · Aceptación: informe sin hallazgos bloqueantes
- [x] **F0.11** PR fase 0 (rama `feat/f0-bootstrap`) · Agente: orquestador · Deps: F0.10 · Aceptación: `gh pr view --json state,mergeable` → OPEN/MERGEABLE
- [x] **F0.12** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F0.11 · Aceptación: `grep -c "^- \[~\]" TASK_PLAN.md` en fase 0 = 0

---

## Fase 1 — Store y migraciones (rama `feat/f1-store`, commits 29eb3bb y 1344b9c)

- [x] **F1.1** Runner de migraciones por `PRAGMA user_version`: `wsp/store/migrate.py`, transacción por archivo, error si la DB es más nueva que las migraciones conocidas · Agente: database-agent · Deps: F0.12 · Aceptación: `uv run pytest tests/store -q`
- [x] **F1.2** Conexión SQLite: `wsp/store/db.py`, WAL, `foreign_keys=ON`, `busy_timeout`, permisos 600, conexión writer + conexión read-only · Agente: database-agent · Deps: F1.1 · Aceptación: `uv run pytest tests/store -q`
- [x] **F1.3** Migración `0001_init.sql`: chats, contacts, messages, reactions, mutes, outbox/estado de envío; UNIQUE en `wa_message_id` · Agente: database-agent · Deps: F1.1 · Aceptación: `uv run pytest tests/store -q`
- [x] **F1.4** Índices (chat+timestamp, wa_message_id) en `0002_indexes_fts.sql` · Agente: database-agent · Deps: F1.3 · Aceptación: EXPLAIN QUERY PLAN usa índices (`docs/query-plans.md`)
- [x] **F1.5** FTS5 + triggers de sincronización en `0002_indexes_fts.sql` · Agente: database-agent · Deps: F1.3 · Aceptación: `uv run pytest tests/store -q` (insert/update/delete coherentes)
- [x] **F1.6** API tipada `wsp/store/repo.py`: upserts idempotentes, `since=<ts>`, listado de chats, historial, search; SQL parametrizado · Agente: database-agent · Deps: F1.3, F1.4 · Aceptación: `uv run pytest tests/store -q`
- [x] **F1.7** Retención (purga por antigüedad) en `repo.py` · Agente: database-agent · Deps: F1.6, F1.5 · Aceptación: `uv run pytest tests/store -q`
- [x] **F1.8** `tools/seed_fake.py` (100k mensajes / 300 chats) + `docs/query-plans.md`; sin full scan; last_messages ~0.2 ms, search ~9 ms · Agente: database-agent · Deps: F1.4, F1.5 · Aceptación: tiempos en Bitácora
- [x] **F1.9** Tests y fixtures de DB temporal · Agente: bulk-agent · Deps: F1.6 · Aceptación: 63 tests verdes
- [x] **F1.10** Gates en verde (63 tests, ruff y mypy limpios) · Agente: orquestador · Deps: F1.9 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [x] **F1.11** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F1.10 · Aceptación: sin bloqueantes; hallazgos corregidos en 1344b9c
- [ ] **F1.12** PR fase 1 (`feat/f1-store`) · Agente: orquestador · Deps: F1.11 · Aceptación: `gh pr view --json state,mergeable`
- [x] **F1.13** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F1.11 · Aceptación: sin `[~]` en fase 1

### Pendientes trasladados de Fase 1: migración 0003 (database-agent)

Ejecutar antes de F3.4 (persistencia). Un solo archivo `wsp/store/migrations/0003_*.sql` + cambios en `repo.py`/`migrate.py` + tests. Ids del reviewer entre paréntesis.

- [ ] **F1.14** (M1) `UNIQUE(chat_jid, wa_id, COALESCE(sender_jid,''))` en messages · Agente: database-agent · Deps: F1.13 · Aceptación: test de duplicado con `sender_jid` NULL no inserta dos filas
- [ ] **F1.15** (M4) Purge marca outbox `sending` atascadas como `failed` en vez de borrarlas · Agente: database-agent · Deps: F1.14 · Aceptación: test de purge conserva fila con estado `failed`
- [ ] **F1.16** (B2) Purgar chats/contactos huérfanos · Agente: database-agent · Deps: F1.14 · Aceptación: test sin huérfanos tras purge
- [ ] **F1.17** (B3) `apply_delete` borra también reacciones del mensaje · Agente: database-agent · Deps: F1.14 · Aceptación: test sin filas en reactions tras delete
- [ ] **F1.18** (B4) Guard `in_transaction` antes de `ROLLBACK` · Agente: database-agent · Deps: F1.14 · Aceptación: test de error previo al `BEGIN` no enmascara la excepción original
- [ ] **F1.19** (B1) Tope de tamaño de payload en outbox y vaciado del payload en `failed` definitivo · Agente: database-agent · Deps: F1.14 · Aceptación: test de payload excedido rechazado y payload NULL tras fallo definitivo
- [ ] **F1.20** (B8) Tope de 64 chars por token FTS · Agente: database-agent · Deps: F1.14 · Aceptación: test de token largo no rompe `/search`
- [ ] **F1.21** Gates + security-reviewer sobre 0003 · Agente: orquestador · Deps: F1.15–F1.20 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`; sin bloqueantes

---

## Fase 2 — Protocolo pydantic + socket Unix (rama `feat/f2-protocol`, commits cead574, b0219d4 y de79c26)

- [x] **F2.1** Modelos pydantic v2 versionados (eventos: mensaje, estado, reacción, conexión; comandos: send, react, mute, history, search, hello/since) · Agente: orquestador · Deps: F1.13 · Aceptación: `uv run pytest tests/protocol/test_models.py -q` (round-trip, campo `v`, rechazo de inválidos/versión desconocida)
- [x] **F2.2** `request_id` idempotente (idempotencia por request_id implementada), ack/error con motivo · Agente: orquestador · Deps: F2.1 · Aceptación: `uv run pytest tests/protocol/test_idempotency.py -q`
- [x] **F2.3** Codec JSON por líneas con límite de tamaño · Agente: orquestador · Deps: F2.1 · Aceptación: `uv run pytest tests/protocol/test_codec.py -q`
- [x] **F2.4** Servidor asyncio en `wspd.sock` (600), limpieza de socket obsoleto, broadcast; cliente lento no bloquea · Agente: orquestador · Deps: F2.3 · Aceptación: `uv run pytest tests/daemon/test_socket_server.py -q`
- [x] **F2.5** Cliente asyncio (reconexión, handshake `since`, error claro sin daemon) · Agente: orquestador · Deps: F2.4 · Aceptación: `uv run pytest tests/cli/test_socket_client.py -q`
- [x] **F2.6** Tests de protocolo y socket sin sleeps frágiles · Agente: bulk-agent · Deps: F2.5 · Aceptación: `uv run pytest tests/protocol tests/daemon tests/cli -q`
- [x] **F2.7** Gates en verde (200 tests, ruff/mypy limpios) · Agente: orquestador · Deps: F2.6 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [x] **F2.8** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F2.7 · Aceptación: sin bloqueantes (validación de entrada, permisos, DoS por línea); fixes aplicados en de79c26
- [ ] **F2.9** PR fase 2 · Agente: orquestador · Deps: F2.8 · Aceptación: `gh pr view --json state,mergeable`
- [x] **F2.10** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F2.9 · Aceptación: sin `[~]` en fase 2

---

## Fase 3 — Daemon neonize (rama `feat/f3-daemon`)

- [~] **F3.1** Esqueleto `wsp.daemon.main:run`: ciclo de vida asyncio, señales, `config.toml`, structlog sin contenido de mensajes ni números completos (solo últimos 4 dígitos), `umask 0o077` al arrancar, y evaluar riesgo SSRF de linkpreview/requests transitivas de neonize (no procesar URLs de mensajes) · Agente: orquestador · Deps: F2.10 · Aceptación: `uv run pytest tests/daemon/test_lifecycle.py tests/daemon/test_log_redaction.py -q`
- [ ] **F3.2** Pairing `wsp pair [--phone]` (qrcode en terminal / código por teléfono); sesión 600; secretos vía keyring · Agente: orquestador · Deps: F3.1 · Aceptación: `uv run pytest tests/daemon/test_pairing.py -q`
- [ ] **F3.3** Puente de eventos neonize → modelos de protocolo · Agente: orquestador · Deps: F3.1, F2.1 · Aceptación: `uv run pytest tests/daemon/test_bridge.py -q`
- [ ] **F3.4** Persistencia al store (único escritor), upserts idempotentes, estados ✓ ✓✓ ✗(motivo) · Agente: orquestador · Deps: F3.3, F1.6 · Aceptación: `uv run pytest tests/daemon/test_persistence.py -q`
- [ ] **F3.5** Envío idempotente por `request_id` (texto, reacción), intención persistida antes de enviar · Agente: orquestador · Deps: F3.4, F2.2 · Aceptación: `uv run pytest tests/daemon/test_send.py -q`
- [ ] **F3.6** Reconexión con backoff exponencial + jitter (reloj/azar inyectados) · Agente: orquestador · Deps: F3.1 · Aceptación: `uv run pytest tests/daemon/test_backoff.py -q`
- [ ] **F3.7** Token bucket de envíos · Agente: orquestador · Deps: F3.5 · Aceptación: `uv run pytest tests/daemon/test_rate_limit.py -q`
- [ ] **F3.8** Relleno `since=<ts>` desde SQLite · Agente: orquestador · Deps: F3.4, F2.5 · Aceptación: `uv run pytest tests/daemon/test_backfill.py -q`
- [ ] **F3.9** Revisión de concurrencia (carreras store/socket/neonize, cancelación, tareas huérfanas) · Agente: orquestador · Deps: F3.5–F3.8 · Aceptación: `uv run pytest tests/daemon -q -W error`
- [ ] **F3.10** Tests del daemon con neonize simulado, sin red · Agente: bulk-agent · Deps: F3.9 · Aceptación: `uv run pytest tests/daemon -q`
- [ ] **F3.11** Gates en verde · Agente: orquestador · Deps: F3.10 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F3.12** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F3.11 · Aceptación: sin bloqueantes (sesión, keyring, logs, PII)
- [ ] **F3.13** PR fase 3 · Agente: orquestador · Deps: F3.12 · Aceptación: `gh pr view --json state,mergeable`
- [ ] **F3.14** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F3.13 · Aceptación: sin `[~]` en fase 3

Pendientes trasladados de Fase 1 (completar antes de F3.11):

- [ ] **F3.15** (A1) Daemon guarda siempre `sender_jid=chat_jid` en entrantes directos; test de integración; contadores de `applied=False` sin PII · Agente: orquestador · Deps: F3.4, F1.14 · Aceptación: `uv run pytest tests/daemon/test_persistence.py -q`
- [ ] **F3.16** (M2) Daemon ejecuta FTS `optimize` + `wal_checkpoint(TRUNCATE)` tras borrados · Agente: orquestador · Deps: F3.4 · Aceptación: `uv run pytest tests/daemon/test_maintenance.py -q`
- [ ] **F3.17** (M3) Errores de outbox/status como códigos, no texto libre; normalizar separadores antes de redactar dígitos · Agente: orquestador · Deps: F3.1, F3.5 · Aceptación: `uv run pytest tests/daemon/test_log_redaction.py -q` (`+34 600-123-456` redactado)

Pendientes trasladados de Fase 2 (security-reviewer):

- [ ] **F3.18** (M4) `flock` sobre lockfile para evitar dos daemons en carrera en `_remove_stale` del socket (decidir en F3 o F4.1/F4.2) · Agente: orquestador · Deps: F3.1 · Aceptación: `uv run pytest tests/daemon/test_socket_server.py -q` (segundo arranque falla sin borrar el socket vivo)
- [ ] **F3.19** (B2) Revisar `umask` global dentro de `Server.start()` (efecto lateral en el proceso); preferir `umask 0o077` de F3.1 + `chmod` explícito del socket · Agente: orquestador · Deps: F3.1 · Aceptación: test de que `Server.start()` no altera el umask del proceso y el socket queda 600

Módulos del daemon (desglose de implementación):

- [~] **F3.20** Migración 0003 + API outbox (cubre F1.14–F1.20) · Agente: database-agent (en curso) · Deps: F1.13 · Aceptación: `uv run pytest tests/store -q`
- [ ] **F3.21** `wsp/daemon/session.py`: wrapper async sobre neonize con `flock` de instancia única (segundo arranque sale con exit 2) · Agente: orquestador · Deps: F3.1 · Aceptación: `uv run pytest tests/daemon/test_session.py -q`
- [ ] **F3.22** `wsp/daemon/events.py`: traducción de eventos (texto, extendidos, respuestas, medios solo metadatos, reacciones, ediciones, revocaciones, recibos, presencia, grupos/contactos, history sync); persistir y luego emitir; eventos no traducibles se loguean solo por tipo · Agente: orquestador · Deps: F3.21, F2.1, F1.6 · Aceptación: `uv run pytest tests/daemon/test_events.py -q`
- [ ] **F3.23** `wsp/daemon/outbox.py`: worker con token bucket configurable, 3 reintentos con backoff, idempotencia por `request_id` · Agente: orquestador · Deps: F3.20, F3.21, F2.2 · Aceptación: `uv run pytest tests/daemon/test_outbox.py -q`
- [ ] **F3.24** `wsp/daemon/config.py` + `wsp/daemon/logsetup.py`: structlog JSON en `state/wspd.log`, rotación 5 MB×3, números enmascarados (`+56 9 ****1234`) · Agente: orquestador · Deps: F3.1 · Aceptación: `uv run pytest tests/daemon/test_log_redaction.py -q`
- [ ] **F3.25** `wspd` main con cierre limpio por SIGTERM/SIGINT · Agente: orquestador · Deps: F3.21–F3.24 · Aceptación: `uv run pytest tests/daemon/test_lifecycle.py -q`
- [ ] **F3.26** `wsp pair` en cli usando `daemon/session.py` · Agente: orquestador · Deps: F3.21, F3.2 · Aceptación: `uv run pytest tests/daemon/test_pairing.py -q`

---

## Fase 4 — LaunchAgent + `wsp daemon` (rama `phase-4-launchd`)

- [ ] **F4.1** Generador de plist (`KeepAlive`, `RunAtLoad`, logs, entorno; sin secretos) · Agente: orquestador · Deps: F3.14 · Aceptación: `uv run pytest tests/cli/test_plist.py -q` y `plutil -lint <plist generado>`
- [ ] **F4.2** `wsp daemon install` (idempotente, `launchctl bootstrap gui/<uid>`) · Agente: orquestador · Deps: F4.1 · Aceptación: `uv run pytest tests/cli/test_daemon_install.py -q`
- [ ] **F4.3** `wsp daemon uninstall` (bootout + borra plist, idempotente) · Agente: orquestador · Deps: F4.2 · Aceptación: `uv run pytest tests/cli/test_daemon_uninstall.py -q`
- [ ] **F4.4** `wsp daemon status` (launchd + ping socket + conexión WhatsApp; exit code por caso) · Agente: orquestador · Deps: F4.2 · Aceptación: `uv run pytest tests/cli/test_daemon_status.py -q`
- [ ] **F4.5** `wsp daemon logs` (`-f`, `-n`, logs rotados, sin contenido) · Agente: orquestador · Deps: F4.2 · Aceptación: `uv run pytest tests/cli/test_daemon_logs.py -q`
- [ ] **F4.6** Tests de launchd con `launchctl` simulado · Agente: bulk-agent · Deps: F4.5 · Aceptación: `uv run pytest tests/cli -q` sin tocar launchd real
- [ ] **F4.7** Gates en verde · Agente: orquestador · Deps: F4.6 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F4.8** Refactor fases 1–4 (duplicación, async bloqueante, cancelación; latencia p50/p95 antes/después; sin cambio de comportamiento) · Agente: refactor-agent · Deps: F4.7 · Aceptación: `uv run pytest -q` idéntico antes/después; métricas en Bitácora
- [ ] **F4.9** Gates tras refactor · Agente: orquestador · Deps: F4.8 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F4.10** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F4.9 · Aceptación: sin bloqueantes (plist, permisos, rutas)
- [ ] **F4.11** PR fase 4 · Agente: orquestador · Deps: F4.10 · Aceptación: `gh pr view --json state,mergeable`
- [ ] **F4.12** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F4.11 · Aceptación: sin `[~]` en fase 4

---

## Fase 5 — REPL en vivo (rama `phase-5-repl`)

- [ ] **F5.1** REPL base: `PromptSession` + `patch_stdout`, prompt `› `, Ctrl-C/Ctrl-D limpios · Agente: orquestador · Deps: F4.12 · Aceptación: `uv run pytest tests/cli/test_repl_base.py -q`
- [ ] **F5.2** Formato `[14:02] Juan › hola`, `[14:02] Familia / Ana › …`, estados ✓ ✓✓ ✗(motivo) · Agente: orquestador · Deps: F5.1 · Aceptación: `uv run pytest tests/cli/test_format.py -q`
- [ ] **F5.3** `NO_COLOR` y `--no-color` · Agente: orquestador · Deps: F5.2 · Aceptación: `uv run pytest tests/cli/test_no_color.py -q` (sin ANSI)
- [ ] **F5.4** Conexión al socket y bucle de eventos en vivo · Agente: orquestador · Deps: F5.1, F2.5 · Aceptación: `uv run pytest tests/cli/test_live_events.py -q`
- [ ] **F5.5** Relleno al reconectar (`since=<último ts>`) sin pérdidas ni duplicados · Agente: orquestador · Deps: F5.4, F3.8 · Aceptación: `uv run pytest tests/cli/test_reconnect_backfill.py -q`
- [ ] **F5.6** Indicador de conexión y mensaje claro sin daemon (sugiere `wsp daemon install`) · Agente: orquestador · Deps: F5.4 · Aceptación: `uv run pytest tests/cli/test_no_daemon.py -q` (sin traceback)
- [ ] **F5.7** Envío de texto desde chat abierto con `request_id` · Agente: orquestador · Deps: F5.4, F3.5 · Aceptación: `uv run pytest tests/cli/test_repl_send.py -q`
- [ ] **F5.8** Tests del REPL con daemon falso · Agente: bulk-agent · Deps: F5.7 · Aceptación: `uv run pytest tests/cli -q`
- [ ] **F5.9** Gates en verde · Agente: orquestador · Deps: F5.8 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F5.10** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F5.9 · Aceptación: sin bloqueantes (escapes de terminal en contenido entrante)
- [ ] **F5.11** PR fase 5 · Agente: orquestador · Deps: F5.10 · Aceptación: `gh pr view --json state,mergeable`
- [ ] **F5.12** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F5.11 · Aceptación: sin `[~]` en fase 5
- [ ] **F5.13** (B6, trasladado de F1) Paginar `messages_since` con cursor `(ts,id)` · Agente: orquestador · Deps: F5.5 · Aceptación: `uv run pytest tests/cli/test_reconnect_backfill.py -q` (sin saltos ni duplicados entre páginas; completar antes de F5.9)
- [ ] **F5.14** (M3, trasladado de F2) Cursor `since` basado en ts puede perder mensajes viejos que llegan tarde; usar cursor por rowid/secuencia monótona en handshake `since`, `messages_since` y cliente · Agente: orquestador · Deps: F5.5 · Aceptación: `uv run pytest tests/cli/test_reconnect_backfill.py -q` (mensaje con ts antiguo insertado tarde se entrega; completar junto con F5.13)

---

## Fase 6 — Comandos, notificaciones, send/tail (rama `phase-6-comandos`)

- [ ] **F6.1** `/chats [n]`, `/open <chat>` (autocompletado difuso con rapidfuzz), `/back` · Agente: orquestador · Deps: F5.12 · Aceptación: `uv run pytest tests/cli/test_cmd_chats.py -q`
- [ ] **F6.2** `/r <n> <texto>` y `/react <n> <emoji>` · Agente: orquestador · Deps: F6.1 · Aceptación: `uv run pytest tests/cli/test_cmd_reply_react.py -q`
- [ ] **F6.3** `/search <texto>` (FTS5, consulta con caracteres especiales segura) y `/history [n]` · Agente: orquestador · Deps: F6.1, F1.5 · Aceptación: `uv run pytest tests/cli/test_cmd_search_history.py -q`
- [ ] **F6.4** `/mute <chat> <dur>` (30m, 8h, 1d; expira solo) · Agente: orquestador · Deps: F6.1 · Aceptación: `uv run pytest tests/cli/test_cmd_mute.py -q`
- [ ] **F6.5** `/help` y `/quit` · Agente: bulk-agent · Deps: F6.1 · Aceptación: `uv run pytest tests/cli/test_cmd_help_quit.py -q`
- [ ] **F6.6** Notificaciones osascript (desactivables, escapado seguro, respetan mute) · Agente: orquestador · Deps: F6.4 · Aceptación: `uv run pytest tests/cli/test_notify.py -q` (sin inyección AppleScript)
- [ ] **F6.7** `wsp send <chat> <texto>` (exit 0 al confirmar; ambigüedad falla) · Agente: orquestador · Deps: F5.7 · Aceptación: `uv run pytest tests/cli/test_send_cmd.py -q`
- [ ] **F6.8** `wsp tail [chat]` (apto para pipes, sin color en no-TTY, SIGINT/SIGPIPE limpios) · Agente: orquestador · Deps: F5.4 · Aceptación: `uv run pytest tests/cli/test_tail_cmd.py -q`
- [ ] **F6.9** Tests de comandos, send, tail y notificaciones (osascript simulado) · Agente: bulk-agent · Deps: F6.8 · Aceptación: `uv run pytest tests/cli -q`
- [ ] **F6.10** README/man: pair, daemon, REPL, send, tail · Agente: bulk-agent · Deps: F6.8 · Aceptación: `grep -cE "wsp (pair|daemon|send|tail)" README.md` ≥ 4
- [ ] **F6.11** Gates en verde · Agente: orquestador · Deps: F6.9, F6.10 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F6.12** Refactor fases 5–6 (duplicación, async bloqueante, cancelación; latencia p50/p95 antes/después; sin cambio de comportamiento) · Agente: refactor-agent · Deps: F6.11 · Aceptación: `uv run pytest -q` idéntico antes/después; métricas en Bitácora
- [ ] **F6.13** Gates tras refactor · Agente: orquestador · Deps: F6.12 · Aceptación: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run mypy wsp`
- [ ] **F6.14** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F6.13 · Aceptación: sin bloqueantes (osascript, argv, PII)
- [ ] **F6.15** PR fase 6 · Agente: orquestador · Deps: F6.14 · Aceptación: `gh pr view --json state,mergeable`
- [ ] **F6.16** Cierre: actualizar TASK_PLAN.md · Agente: planner · Deps: F6.15 · Aceptación: `grep -c "^- \[ \]\|^- \[~\]" TASK_PLAN.md` = 0

---

## Bitácora

<!-- Formato: AAAA-MM-DD · tarea · nota (decisiones, métricas, intentos fallidos) -->

2026-10-20 · F0 · Fase 0 cerrada: 8 tests en verde, security-reviewer sin bloqueantes (altos/medios corregidos en 5610e19), PR abierto en `feat/f0-bootstrap`. pip-audit no corre en sandbox: pendiente que el usuario ejecute fuera `uv export --no-dev | uvx pip-audit -r /dev/stdin`.
2026-10-20 · F1 · Fase 1 cerrada (29eb3bb, 1344b9c): 63 tests, ruff/mypy limpios, security-reviewer sin bloqueantes; 100k msgs/300 chats: last_messages ~0.2 ms, search ~9 ms, sin full scan. PR F1.12 pendiente.
2026-10-20 · F1 · Trasladado: 0003 (F1.14–F1.21), F3.15–F3.17, F5.13. Externo: pip-audit sigue pendiente (usuario, fuera del sandbox).
2026-10-20 · F2 · Fase 2 cerrada (cead574, b0219d4, de79c26): 200 tests, ruff/mypy limpios, security-reviewer sin bloqueantes, fixes aplicados; idempotencia por request_id hecha. F2.9 (PR) pendiente.
2026-10-20 · F2 · Diferidos: M3 cursor por rowid -> F5.14 (junto a F5.13); M4 flock anti doble daemon -> F3.18; B2 umask en Server.start() -> F3.19.
2026-10-20 · F0 · `.claude/settings.json` y `.claude/hooks/*.sh` siguen con el stack viejo (el sandbox impide editarlos); pendiente actualizarlos fuera del sandbox.
2026-10-20 · F3 · F3 iniciada en feat/f3-daemon; libmagic ausente en el equipo: neonize no importa hasta `brew install libmagic` (necesario solo para la prueba manual; tests usan cliente falso).
