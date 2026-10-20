-- Indices de acceso y busqueda de texto completo (FTS5).

-- Ultimos N mensajes de un chat (id DESC desempata de forma determinista).
CREATE INDEX idx_messages_chat_ts ON messages (chat_jid, ts DESC, id DESC);
-- messages_since(ts). La busqueda por wa_id la cubre UNIQUE (chat_jid, wa_id).
CREATE INDEX idx_messages_ts ON messages (ts);
CREATE INDEX idx_chats_last_message ON chats (last_message_at DESC);
CREATE INDEX idx_outbox_state ON outbox (state, created_at);

CREATE VIRTUAL TABLE messages_fts USING fts5 (
    body,
    content = 'messages',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

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

-- Indexa mensajes preexistentes (no-op si la tabla esta vacia).
INSERT INTO messages_fts (messages_fts) VALUES ('rebuild');
