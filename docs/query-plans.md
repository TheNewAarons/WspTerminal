# Planes de consulta (store SQLite)

DB sintética: 100 000 mensajes, 300 chats (`uv run python tools/seed_fake.py --db <ruta>`),
seed fija 42, tras `ANALYZE`. SQLite 3.50.4, macOS, conexión read-only. Mediana de 60 ejecuciones.

## EXPLAIN QUERY PLAN

`list_chats`
```
SCAN chats USING INDEX idx_chats_last_message
```
(recorrido ordenado por índice sobre una tabla pequeña; sin TEMP B-TREE.)

`last_messages` (chat más activo)
```
SEARCH messages USING INDEX idx_messages_chat_ts (chat_jid=?)
```

`messages_since`
```
SEARCH messages USING INDEX idx_messages_ts (ts>?)
```

`search` (FTS5, `"canción"*`)
```
SCAN messages_fts VIRTUAL TABLE INDEX 0:M1
SEARCH m USING INTEGER PRIMARY KEY (rowid=?)
USE TEMP B-TREE FOR ORDER BY
```
(el SCAN es del índice FTS acotado por MATCH; el TEMP B-TREE ordena solo los aciertos por bm25.)

## Tiempos (mediana)

| Consulta | Mediana | Objetivo |
|---|---|---|
| list_chats(50) | 0.12 ms | - |
| last_messages(50) | 0.20 ms | < 5 ms |
| messages_since(1 h) | 0.05 ms | - |
| search("canción") | 10.0 ms | < 50 ms |
| search("hola") (término muy común) | 12.2 ms | < 50 ms |
| search("reunión café") | 2.7 ms | < 50 ms |

## Política de retención

- Mensajes: `repo.purge_older_than(days)`; valor por defecto sugerido `DEFAULT_RETENTION_DAYS = 365`.
- Borra mensajes con `ts` anterior al corte, reacciones huérfanas y filas `done`/`failed` antiguas del outbox.
  El índice FTS se mantiene coherente por los triggers `messages_fts_ad`.
- Chats y contactos no se purgan. El daemon decide cuándo invocarla (p. ej. al arrancar, una vez al día).
