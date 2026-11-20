# wsp

Cliente de WhatsApp para la terminal de macOS. Interfaz de línea (sin paneles), tiempo real, un solo usuario.

- `wspd`: daemon (LaunchAgent) con la única sesión de WhatsApp. Persiste en SQLite y emite a los clientes por un socket Unix.
- `wsp`: REPL en vivo, más `wsp send` y `wsp tail` para scripts.

## Requisitos

- macOS
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Instalación

```
uv tool install .
wsp daemon install
```

## Vinculación

```
wsp pair                       # muestra un QR: WhatsApp > Dispositivos vinculados
wsp pair --phone 521550000000  # alternativa: código de emparejamiento por número
```

Haz la vinculación una sola vez; la sesión queda en `~/.local/share/wsp/`.

## Daemon

```
wsp daemon install     # instala y carga el LaunchAgent (idempotente)
wsp daemon uninstall   # lo descarga y borra (conserva datos y sesión)
wsp daemon status      # launchd, socket y estado de la cuenta
wsp daemon restart     # reinicia wspd
wsp daemon logs        # sigue el log (-n N líneas, --no-follow, --json)
```

Los logs no contienen mensajes ni números completos.

## REPL

`wsp` abre el REPL. Prompt `› ` sin chat abierto, `Juan › ` con uno abierto. Texto sin `/` se envía al chat abierto (`//hola` envía «/hola»). Los mensajes entrantes se imprimen sobre el prompt:

```
[14:02] Juan › hola
[14:02] Familia / Ana › nos vemos a las 8
[14:03] tú › va ✓✓
```

Al reconectar, el REPL pide al daemon lo perdido y lo muestra entre separadores.

| Comando | Descripción |
|---|---|
| `/chats [n]` | Lista los n chats más recientes (15 por defecto, máx. 200). |
| `/open <n\|nombre>` | Abre un chat por número de la última lista o por nombre aproximado. |
| `/back` | Cierra el chat abierto. |
| `/r <n> <texto>` | Responde (cita) al mensaje n de la vista actual. |
| `/react <n> <emoji>` | Reacciona al mensaje n de la vista actual. |
| `/search <texto>` | Busca en todos los chats; luego `/open <n>` salta al chat del resultado. |
| `/mute <chat> <dur>` | Silencia un chat: `30m`, `8h`, `2d`, `1w` (máx. 52w) u `off` para quitarlo. |
| `/history [n]` | Muestra los últimos n mensajes del chat abierto (20 por defecto). |
| `/help` | Ayuda. |
| `/quit` | Sale (también Ctrl+D). Ctrl+C descarta la línea. |

Alias: `/q`, `/exit` (salir), `/h`, `/?` (ayuda).

```
› /chats
1  Juan (2)
2  Familia
3  Ana 🔇
› /open 1
── martes 20 oct 2026 ──
[14:02] Juan › hola
Juan › /r 1 hola, ¿qué tal?
Juan › /react 1 👍
› /search cena
1  Familia · 20 oct 14:02 · Ana: cena mañana
› /mute familia 8h
  Familia: silenciado.
```

Si un nombre es ambiguo, `/open` muestra hasta 5 candidatos y fija la lista para elegir con `/open <n>`.

### Estados de envío

| Marca | Significado |
|---|---|
| `✓` | enviado |
| `✓✓` | entregado (en cian si fue leído, con color activo) |
| `✗ (motivo)` | fallido, con el motivo |

## `wsp send`

```
wsp send <chat> <texto|-> [--wait-delivered]
echo "listo" | wsp send Juan -
```

`-` lee el texto de stdin (máx. 64 KiB). `--wait-delivered` espera hasta 30 s a ✓✓.

| Código | Significado |
|---|---|
| 0 | enviado (con `--wait-delivered`: entregado) |
| 1 | envío fallido, argumentos inválidos o texto vacío |
| 2 | el daemon no responde |
| 4 | chat inexistente o ambiguo |
| 5 | `--wait-delivered` agotó el tiempo |

Los errores van a stderr.

## `wsp tail`

```
wsp tail                 # todos los chats, en vivo
wsp tail Juan            # solo un chat
wsp tail --json | jq -r 'select(.type == "message.new") | .data.body'
```

Texto: mismo formato que el REPL (`[14:02] Juan › hola`). `--json`: un evento por línea con `type` y `data`. Termina con Ctrl+C o al cerrarse el pipe.

## Configuración

`wsp config` muestra la configuración efectiva y su ruta. Todo es opcional; si `~/.config/wsp/config.toml` no existe se usan los valores por defecto. Claves desconocidas son un error.

```toml
retention_days = 365      # 1-36500
notifications = true      # false desactiva las notificaciones del REPL

[outbox]
rate_per_sec = 1.0        # envíos por segundo sostenidos (token bucket)
burst = 5                 # ráfaga máxima
max_attempts = 3
retry_base = 1.0          # backoff entre reintentos de envío (s)

[reconnect]
base = 1.0                # backoff exponencial con jitter (s)
cap = 300.0
jitter = 0.3              # fracción del retardo

[log]
level = "INFO"            # DEBUG | INFO | WARNING | ERROR
```

Reinicia el daemon tras cambiar la configuración (`wsp daemon restart`).

## Notificaciones

El REPL notifica con `osascript` los mensajes ajenos de chats que no están abiertos ni silenciados. No notifica si tu terminal (Terminal, iTerm2, Ghostty, WezTerm, VS Code, Hyper) es la app al frente; en caso de duda, notifica. El texto se pasa como argumentos, nunca dentro del script. Desactívalas con `notifications = false`.

## Rutas

| Ruta | Contenido |
|---|---|
| `~/.local/share/wsp/` | base de datos y sesión de WhatsApp (permisos 600) |
| `~/.local/state/wsp/` | socket `wspd.sock` (600), logs, historial de comandos |
| `~/.config/wsp/config.toml` | configuración |

`WSP_HOME` redirige las tres (útil en pruebas). El historial del REPL guarda solo comandos `/…`, nunca mensajes.

## Color

Hay color solo en una terminal interactiva. `NO_COLOR` (no vacío) o `--no-color` lo desactivan.

## Desarrollo

```
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy wsp
```

Los tests con socket Unix necesitan poder crear sockets (no corren dentro de sandboxes que lo impiden). Fases y estado: `TASK_PLAN.md`. Convenciones: `CLAUDE.md`.

## Riesgos

- Cliente **no oficial**: usa whatsmeow vía neonize, no la API de WhatsApp. Meta no lo respalda.
- WhatsApp puede **suspender o banear la cuenta** vinculada. Úsalo bajo tu responsabilidad, preferiblemente con una cuenta que puedas perder.
- No lo uses para spam ni envíos masivos; el límite de envíos del daemon es una protección, no una garantía.
- La sesión vive en disco (`~/.local/share/wsp/`, permisos 600): quien la lea puede operar tu cuenta. Protege tu disco y no la subas a git.
- Sin garantía de ningún tipo. Los cambios del protocolo de WhatsApp pueden romperlo sin aviso.
