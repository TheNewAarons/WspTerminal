# TASK_PLAN — wsp

Estados: `[ ]` pendiente · `[~]` en curso · `[x]` hecho.
Ramas por fase: `phase-N-<tema>`. Anti-bucle: 2 fallos consecutivos → revertir y replantear aquí.
Gates estándar: `uv run pytest` · `uv run ruff check` · `uv run ruff format --check` · `uv run mypy wsp`.

Agentes: `orquestador` (sesión principal), `database-agent`, `bulk-agent`, `refactor-agent`, `security-reviewer`, `planner`.

---

## Fase 0 — Bootstrap

- [x] **F0-T1** pyproject (uv) + estructura `wsp/{daemon,cli,store,protocol,migrations}` · Agente: orquestador · Deps: — · Aceptación: `uv sync` ok; paquetes importables.
- [x] **F0-T2** pre-commit (ruff, mypy, detect-secrets) · Agente: orquestador · Deps: F0-T1 · Aceptación: `pre-commit run --all-files` en verde.
- [x] **F0-T3** `.gitignore` (DB, sesión, `.venv`, secrets) · Agente: orquestador · Deps: — · Aceptación: `git status` sin artefactos de datos.
- [x] **F0-T4** `CLAUDE.md` actualizado al stack wsp · Agente: orquestador · Deps: — · Aceptación: describe arquitectura, agentes y fases.
- [x] **F0-T5** Limpieza del stack viejo (apps/, packages/, Django/FastAPI/Astro/Expo) · Agente: orquestador · Deps: — · Aceptación: sin referencias al stack viejo en el árbol.
- [ ] **F0-T6** Gates en verde · Agente: orquestador · Deps: F0-T1..T5 · Aceptación: los 4 comandos de gate exit 0.
- [ ] **F0-T7** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F0-T6 · Aceptación: sin hallazgos bloqueantes.
- [~] **F0-T8** PR fase 0 (`gh pr create`) · Agente: orquestador · Deps: F0-T7 · Aceptación: PR abierto y mergeable.
- [ ] **F0-T9** Actualizar TASK_PLAN.md (cierre fase 0) · Agente: planner · Deps: F0-T8 · Aceptación: estados y bitácora reflejan la realidad.

---

## Fase 1 — Store y migraciones

- [ ] **F1-T1** Runner de migraciones: aplica `wsp/migrations/NNNN_*.sql` en orden según `PRAGMA user_version`, transaccional · Agente: database-agent · Deps: F0-T8 · Aceptación: DB vacía llega a la última versión; re-ejecución es no-op; migración fallida hace rollback sin avanzar `user_version`.
- [ ] **F1-T2** Conexión SQLite: WAL, `foreign_keys=ON`, `busy_timeout`, permisos 600 en `~/.local/share/wsp/` · Agente: database-agent · Deps: F1-T1 · Aceptación: test verifica `journal_mode=wal` y modo de archivo 600.
- [ ] **F1-T3** Migración 0001: tablas chats, contacts, messages, reactions, mutes, estado de envío · Agente: database-agent · Deps: F1-T1 · Aceptación: esquema aplicado; constraints UNIQUE sobre `wa_message_id`.
- [ ] **F1-T4** Índices (chat_id+timestamp, wa_message_id) · Agente: database-agent · Deps: F1-T3 · Aceptación: `EXPLAIN QUERY PLAN` usa índices en listado por chat y búsqueda por id.
- [ ] **F1-T5** FTS5 para `/search` con triggers de sincronización · Agente: database-agent · Deps: F1-T3 · Aceptación: búsqueda devuelve coincidencias tras insert/update/delete.
- [ ] **F1-T6** API del store: upserts idempotentes (mensajes, chats, reacciones, estados), consultas `since=<ts>`, listado de chats, historial · Agente: database-agent · Deps: F1-T3, F1-T4 · Aceptación: doble upsert no duplica; SQL 100% parametrizado.
- [ ] **F1-T7** Retención configurable (purga por antigüedad) · Agente: database-agent · Deps: F1-T6 · Aceptación: purga elimina solo lo anterior al umbral e índice FTS coherente.
- [ ] **F1-T8** Benchmark con 100k mensajes + EXPLAIN documentado · Agente: database-agent · Deps: F1-T4, F1-T5 · Aceptación: consultas clave sin full scan; tiempos registrados en Bitácora.
- [ ] **F1-T9** Tests del store y migraciones (fixtures DB temporal) · Agente: bulk-agent · Deps: F1-T6 · Aceptación: cobertura de runner, upserts, `since`, FTS, retención.
- [ ] **F1-T10** Gates en verde · Agente: orquestador · Deps: F1-T9 · Aceptación: 4 comandos exit 0.
- [ ] **F1-T11** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F1-T10 · Aceptación: sin bloqueantes; SQL parametrizado confirmado.
- [ ] **F1-T12** PR fase 1 · Agente: orquestador · Deps: F1-T11 · Aceptación: PR abierto.
- [ ] **F1-T13** Actualizar TASK_PLAN.md · Agente: planner · Deps: F1-T12 · Aceptación: plan al día.

---

## Fase 2 — Protocolo pydantic + socket Unix

- [ ] **F2-T1** Modelos pydantic v2 versionados (eventos: mensaje, estado, reacción, conexión; comandos: send, react, mute, history, search, hello/since) · Agente: orquestador · Deps: F1-T12 · Aceptación: round-trip JSON válido; campo `v`; rechazo de payload inválido o versión desconocida.
- [ ] **F2-T2** `request_id` idempotente en comandos y respuestas (ack/error con motivo) · Agente: orquestador · Deps: F2-T1 · Aceptación: mismo `request_id` devuelve el mismo resultado sin reejecutar.
- [ ] **F2-T3** Codec JSON por líneas (framing, límite de tamaño de línea) · Agente: orquestador · Deps: F2-T1 · Aceptación: líneas parciales/concatenadas se parsean bien; línea sobredimensionada se rechaza.
- [ ] **F2-T4** Servidor asyncio en `~/.local/state/wsp/wspd.sock` con permisos 600, limpieza de socket obsoleto, broadcast a múltiples clientes · Agente: orquestador · Deps: F2-T3 · Aceptación: socket modo 600; dos clientes reciben el mismo evento; cliente lento no bloquea a otros.
- [ ] **F2-T5** Cliente de socket asyncio (conexión, reconexión, handshake con `since`) · Agente: orquestador · Deps: F2-T4 · Aceptación: cliente reconecta y envía `since`; error claro si no hay daemon.
- [ ] **F2-T6** Tests de protocolo y socket (modelos, codec, idempotencia, permisos, concurrencia) · Agente: bulk-agent · Deps: F2-T5 · Aceptación: tests en verde, sin sleeps frágiles.
- [ ] **F2-T7** Gates en verde · Agente: orquestador · Deps: F2-T6 · Aceptación: 4 comandos exit 0.
- [ ] **F2-T8** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F2-T7 · Aceptación: sin bloqueantes (validación de entrada, permisos, DoS por línea).
- [ ] **F2-T9** PR fase 2 · Agente: orquestador · Deps: F2-T8 · Aceptación: PR abierto.
- [ ] **F2-T10** Actualizar TASK_PLAN.md · Agente: planner · Deps: F2-T9 · Aceptación: plan al día.

---

## Fase 3 — Daemon neonize

- [ ] **F3-T1** Esqueleto `wspd`: ciclo de vida asyncio, señales, config (`~/.config/wsp/config.toml`), logging sin contenido ni números completos · Agente: orquestador · Deps: F2-T9 · Aceptación: arranca/para limpio con SIGTERM; test de redacción de logs (número enmascarado, sin cuerpo).
- [ ] **F3-T2** Pairing (`wsp pair [--phone]`): QR en terminal y código por teléfono; sesión en `~/.local/share/wsp/` 600; secretos vía keyring · Agente: orquestador · Deps: F3-T1 · Aceptación: sesión persiste entre reinicios; archivo modo 600; sin secretos en logs.
- [ ] **F3-T3** Puente de eventos neonize → modelos de protocolo (mensajes, recibos, reacciones, presencia, conexión) · Agente: orquestador · Deps: F3-T1, F2-T1 · Aceptación: eventos simulados producen modelos válidos.
- [ ] **F3-T4** Persistencia: eventos entrantes/salientes al store (único escritor) con upserts idempotentes · Agente: orquestador · Deps: F3-T3, F1-T6 · Aceptación: evento duplicado no crea filas; estados ✓ ✓✓ ✗(motivo) actualizados.
- [ ] **F3-T5** Envío idempotente por `request_id` (texto, reacción), persistiendo intención antes de enviar · Agente: orquestador · Deps: F3-T4, F2-T2 · Aceptación: reintento con mismo `request_id` no envía dos veces; fallo registra ✗ con motivo.
- [ ] **F3-T6** Reconexión con backoff exponencial + jitter y reset tras éxito · Agente: orquestador · Deps: F3-T1 · Aceptación: test con reloj/azar inyectados verifica crecimiento, tope y jitter acotado.
- [ ] **F3-T7** Rate limit de envíos con token bucket · Agente: orquestador · Deps: F3-T5 · Aceptación: ráfaga sobre capacidad se encola/rechaza con motivo; recarga correcta con reloj inyectado.
- [ ] **F3-T8** Servicio de relleno: responder `since=<ts>` desde SQLite al cliente · Agente: orquestador · Deps: F3-T4, F2-T5 · Aceptación: cliente recibe solo mensajes > ts, en orden, sin duplicados.
- [ ] **F3-T9** Revisión de concurrencia (carreras store/socket/neonize, cancelación) · Agente: orquestador · Deps: F3-T5..T8 · Aceptación: sin tareas huérfanas al apagar; sin escrituras concurrentes al store.
- [ ] **F3-T10** Tests del daemon con neonize simulado (bridge, persistencia, idempotencia, backoff, token bucket, redacción de logs) · Agente: bulk-agent · Deps: F3-T9 · Aceptación: tests en verde sin red.
- [ ] **F3-T11** Gates en verde · Agente: orquestador · Deps: F3-T10 · Aceptación: 4 comandos exit 0.
- [ ] **F3-T12** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F3-T11 · Aceptación: sin bloqueantes (sesión, keyring, logs, PII).
- [ ] **F3-T13** PR fase 3 · Agente: orquestador · Deps: F3-T12 · Aceptación: PR abierto.
- [ ] **F3-T14** Actualizar TASK_PLAN.md · Agente: planner · Deps: F3-T13 · Aceptación: plan al día.

---

## Fase 4 — LaunchAgent + `wsp daemon`

- [ ] **F4-T1** Generador de plist LaunchAgent (`KeepAlive`, `RunAtLoad`, rutas de log, entorno) · Agente: orquestador · Deps: F3-T13 · Aceptación: plist válido (`plutil -lint`); sin secretos embebidos.
- [ ] **F4-T2** `wsp daemon install` (escribe plist, `launchctl bootstrap gui/<uid>`) · Agente: orquestador · Deps: F4-T1 · Aceptación: idempotente; daemon queda cargado.
- [ ] **F4-T3** `wsp daemon uninstall` (bootout + elimina plist) · Agente: orquestador · Deps: F4-T2 · Aceptación: idempotente; no deja procesos ni plist.
- [ ] **F4-T4** `wsp daemon status` (launchd + ping al socket + estado de conexión WhatsApp) · Agente: orquestador · Deps: F4-T2 · Aceptación: distingue no instalado / instalado detenido / corriendo / desconectado; exit code distinto por caso.
- [ ] **F4-T5** `wsp daemon logs` (tail/`-f`, `-n`) con logs rotados · Agente: orquestador · Deps: F4-T2 · Aceptación: muestra últimas N líneas y sigue; sin contenido de mensajes.
- [ ] **F4-T6** Tests de la capa launchd con `launchctl` simulado · Agente: bulk-agent · Deps: F4-T5 · Aceptación: tests en verde sin tocar launchd real.
- [ ] **F4-T7** Refactor de fases 1-4: duplicación, async bloqueante, cancelación, latencia p50/p95 antes/después, sin cambio de comportamiento · Agente: refactor-agent · Deps: F4-T6 · Aceptación: tests existentes intactos y en verde; métricas antes/después en Bitácora.
- [ ] **F4-T8** Gates en verde · Agente: orquestador · Deps: F4-T7 · Aceptación: 4 comandos exit 0.
- [ ] **F4-T9** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F4-T8 · Aceptación: sin bloqueantes (plist, permisos, rutas).
- [ ] **F4-T10** PR fase 4 · Agente: orquestador · Deps: F4-T9 · Aceptación: PR abierto.
- [ ] **F4-T11** Actualizar TASK_PLAN.md · Agente: planner · Deps: F4-T10 · Aceptación: plan al día.

---

## Fase 5 — REPL en vivo

- [ ] **F5-T1** REPL base: `PromptSession` + `patch_stdout`, prompt `› `, Ctrl-C/Ctrl-D limpios · Agente: orquestador · Deps: F4-T10 · Aceptación: mensajes entrantes se imprimen sin romper la línea en edición.
- [ ] **F5-T2** Formato de líneas `[14:02] Juan › hola` y `[14:02] Familia / Ana › …`; estados ✓ ✓✓ ✗(motivo) · Agente: orquestador · Deps: F5-T1 · Aceptación: tests de formato para 1:1, grupo y cada estado.
- [ ] **F5-T3** Soporte `NO_COLOR` y `--no-color` · Agente: orquestador · Deps: F5-T2 · Aceptación: sin secuencias ANSI con cualquiera de los dos.
- [ ] **F5-T4** Conexión al socket y bucle de eventos en vivo · Agente: orquestador · Deps: F5-T1, F2-T5 · Aceptación: eventos del daemon se muestran en tiempo real.
- [ ] **F5-T5** Relleno al reconectar: guardar último ts visto y pedir `since=<ts>` · Agente: orquestador · Deps: F5-T4, F3-T8 · Aceptación: tras matar/relanzar daemon no hay mensajes perdidos ni duplicados.
- [ ] **F5-T6** Indicador de estado de conexión y mensaje claro si el daemon no corre (sugiere `wsp daemon install`) · Agente: orquestador · Deps: F5-T4 · Aceptación: mensaje en ambos casos sin traceback.
- [ ] **F5-T7** Envío de texto plano desde el REPL en chat abierto, con `request_id` · Agente: orquestador · Deps: F5-T4, F3-T5 · Aceptación: reenvío por reconexión no duplica el mensaje.
- [ ] **F5-T8** Tests del REPL (formato, relleno, reconexión, NO_COLOR) · Agente: bulk-agent · Deps: F5-T7 · Aceptación: tests en verde con daemon falso.
- [ ] **F5-T9** Gates en verde · Agente: orquestador · Deps: F5-T8 · Aceptación: 4 comandos exit 0.
- [ ] **F5-T10** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F5-T9 · Aceptación: sin bloqueantes (inyección de escapes de terminal en contenido entrante).
- [ ] **F5-T11** PR fase 5 · Agente: orquestador · Deps: F5-T10 · Aceptación: PR abierto.
- [ ] **F5-T12** Actualizar TASK_PLAN.md · Agente: planner · Deps: F5-T11 · Aceptación: plan al día.

---

## Fase 6 — Comandos, notificaciones, send/tail

- [ ] **F6-T1** `/chats [n]`, `/open <chat>`, `/back` (prompt `Juan › `) · Agente: orquestador · Deps: F5-T11 · Aceptación: lista numerada; abrir cambia prompt; `/back` lo restaura.
- [ ] **F6-T2** `/r <n> <texto>` (responder al mensaje n) y `/react <n> <emoji>` · Agente: orquestador · Deps: F6-T1 · Aceptación: cita/reacción llega al daemon con `request_id`; n inválido da error claro.
- [ ] **F6-T3** `/search <texto>` (FTS5) y `/history [n]` · Agente: orquestador · Deps: F6-T1, F1-T5 · Aceptación: resultados ordenados y acotados; consulta con caracteres especiales no rompe FTS.
- [ ] **F6-T4** `/mute <chat> <dur>` (ej. 30m, 8h, 1d) · Agente: orquestador · Deps: F6-T1 · Aceptación: duración parseada; chat silenciado no notifica; expira solo.
- [ ] **F6-T5** `/help` y `/quit` · Agente: bulk-agent · Deps: F6-T1 · Aceptación: `/help` lista todos los comandos; `/quit` sale con código 0.
- [ ] **F6-T6** Notificaciones osascript (desactivables por config), escapado seguro del contenido · Agente: orquestador · Deps: F6-T4 · Aceptación: sin inyección AppleScript con comillas/backslashes; respeta mute y desactivación.
- [ ] **F6-T7** `wsp send <chat> <texto>` no interactivo · Agente: orquestador · Deps: F5-T7 · Aceptación: exit 0 al confirmar, !=0 con motivo; resolución de chat ambigua falla explícitamente.
- [ ] **F6-T8** `wsp tail [chat]` (stream a stdout, apto para pipes) · Agente: orquestador · Deps: F5-T4 · Aceptación: sin color en no-TTY; termina limpio con SIGINT/SIGPIPE.
- [ ] **F6-T9** Tests de comandos, send, tail y notificaciones (osascript simulado) · Agente: bulk-agent · Deps: F6-T8 · Aceptación: tests en verde.
- [ ] **F6-T10** README/man con uso, instalación y comandos · Agente: bulk-agent · Deps: F6-T8 · Aceptación: cubre pair, daemon, REPL, send, tail.
- [ ] **F6-T11** Refactor de fases 5-6: duplicación, async bloqueante, cancelación, latencia p50/p95 antes/después, sin cambio de comportamiento · Agente: refactor-agent · Deps: F6-T9 · Aceptación: tests intactos en verde; métricas en Bitácora.
- [ ] **F6-T12** Gates en verde · Agente: orquestador · Deps: F6-T11 · Aceptación: 4 comandos exit 0.
- [ ] **F6-T13** security-reviewer sobre `git diff main...HEAD` · Agente: security-reviewer · Deps: F6-T12 · Aceptación: sin bloqueantes (osascript, argv, PII).
- [ ] **F6-T14** PR fase 6 · Agente: orquestador · Deps: F6-T13 · Aceptación: PR abierto.
- [ ] **F6-T15** Actualizar TASK_PLAN.md (cierre) · Agente: planner · Deps: F6-T14 · Aceptación: plan al día.

---

## Bitácora

<!-- Formato: AAAA-MM-DD · tarea · nota (decisiones, métricas, intentos fallidos) -->
