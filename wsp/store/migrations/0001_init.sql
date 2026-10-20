-- Esquema base de wsp. Timestamps en segundos Unix (UTC).

CREATE TABLE contacts (
    jid         TEXT PRIMARY KEY,
    push_name   TEXT,
    alias       TEXT,
    is_business INTEGER NOT NULL DEFAULT 0 CHECK (is_business IN (0, 1)),
    updated_at  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE chats (
    jid             TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('direct', 'group', 'broadcast')),
    title           TEXT,
    unread_count    INTEGER NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    last_message_at INTEGER,
    archived        INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    muted_until     INTEGER NOT NULL DEFAULT 0,
    pinned          INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1))
);

CREATE TABLE messages (
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
    deleted_at   INTEGER,
    UNIQUE (chat_jid, wa_id)
);

-- Sin FK a messages: una reaccion puede llegar antes que su mensaje.
CREATE TABLE reactions (
    chat_jid   TEXT NOT NULL,
    wa_id      TEXT NOT NULL,
    sender_jid TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    PRIMARY KEY (chat_jid, wa_id, sender_jid)
);

CREATE TABLE outbox (
    request_id TEXT PRIMARY KEY,
    chat_jid   TEXT NOT NULL,
    payload    TEXT NOT NULL CHECK (json_valid(payload)),
    state      TEXT NOT NULL DEFAULT 'queued'
               CHECK (state IN ('queued', 'sending', 'done', 'failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
