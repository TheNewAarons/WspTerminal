-- Endurecimiento: unicidad por autor en messages (F1.14) y wa_id real en outbox.

-- Reconstruccion de messages: UNIQUE (chat_jid, wa_id) pasa a indice unico por autor.
CREATE TABLE messages_new (
    id           INTEGER PRIMARY KEY,
    chat_jid     TEXT NOT NULL REFERENCES chats (jid) ON DELETE CASCADE,
    wa_id        TEXT NOT NULL,
    sender_jid   TEXT,
    from_me      INTEGER NOT NULL DEFAULT 0 CHECK (from_me IN (0, 1)),
    ts           INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'text',
    body         TEXT,
    quoted_wa_id TEXT,
    status       TEXT NOT NULL DEFAULT 'sent'
                 CHECK (status IN ('pending', 'sent', 'delivered', 'read', 'failed')),
    error        TEXT,
    edited_at    INTEGER,
    deleted_at   INTEGER
);

INSERT INTO messages_new (
    id, chat_jid, wa_id, sender_jid, from_me, ts, kind, body, quoted_wa_id, status, error,
    edited_at, deleted_at
)
SELECT
    id, chat_jid, wa_id, sender_jid, from_me, ts, kind, body, quoted_wa_id, status, error,
    edited_at, deleted_at
FROM messages;

-- Al borrar la tabla caen sus indices y triggers (se recrean abajo).
DROP TABLE messages;
ALTER TABLE messages_new RENAME TO messages;

-- Cubre tambien la busqueda por (chat_jid, wa_id) como prefijo.
CREATE UNIQUE INDEX uq_messages_author ON messages (chat_jid, wa_id, COALESCE(sender_jid, ''));
CREATE INDEX idx_messages_chat_ts ON messages (chat_jid, ts DESC, id DESC);
CREATE INDEX idx_messages_ts ON messages (ts);

CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts (rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;

CREATE TRIGGER messages_fts_au AFTER UPDATE OF body ON messages
WHEN old.body IS NOT new.body BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
    INSERT INTO messages_fts (rowid, body) VALUES (new.id, new.body);
END;

-- El indice externo apuntaba a la tabla anterior: reconstruir desde la nueva.
INSERT INTO messages_fts (messages_fts) VALUES ('rebuild');

-- Id real de WhatsApp asignado tras enviar.
ALTER TABLE outbox ADD COLUMN wa_id TEXT;
