"""Siembra una DB sintética (mensajes en español) para validar planes de consulta."""

import argparse
import random
import time
from pathlib import Path

from wsp.store import db, repo
from wsp.store.migrate import migrate

_WORDS = (
    "hola buenos días tarde noche mañana canción música película reunión trabajo proyecto "
    "documento llamada café comida cena almuerzo fútbol partido viaje vuelo aeropuerto "
    "teléfono número dirección calle señor señora niño niña año cumpleaños regalo fiesta "
    "gracias por favor perdón entonces también después antes ahora siempre nunca quizás "
    "está bien mal rápido lento grande pequeño nuevo viejo casa oficina escuela universidad "
    "examen tarea pago factura transferencia banco dinero precio oferta compra envío paquete "
    "foto video audio ubicación contraseña correo mensaje semana lunes martes miércoles jueves "
    "viernes sábado domingo mañana ñandú pingüino corazón sueño él ella nosotros ustedes"
).split()
_KINDS = ("text",) * 18 + ("image", "audio")
_YEAR = 365 * 86_400


def _sentence(rng: random.Random) -> str:
    words = rng.choices(_WORDS, k=rng.randint(3, 14))
    text = " ".join(words)
    return text.capitalize() + rng.choice((".", "", "!", "?", " :)"))


def seed(path: Path, messages: int, chats: int, rng_seed: int = 42) -> None:
    rng = random.Random(rng_seed)  # noqa: S311
    conn = db.open_write(path)
    try:
        migrate(conn)
        now = int(time.time())
        jids: list[str] = []
        for i in range(chats):
            is_group = i % 3 == 0
            jid = f"1203630{i:08d}@g.us" if is_group else f"5215500{i:06d}@s.whatsapp.net"
            jids.append(jid)
        members = [f"5215511{i:06d}@s.whatsapp.net" for i in range(40)]
        weights = [rng.paretovariate(1.2) for _ in jids]
        with db.transaction(conn):
            for i, jid in enumerate(jids):
                repo.upsert_chat(conn, jid, title=f"Chat {i}")
            for n in range(messages):
                jid = rng.choices(jids, weights)[0]
                group = jid.endswith("@g.us")
                from_me = rng.random() < 0.4
                kind = rng.choice(_KINDS)
                repo.upsert_message(
                    conn,
                    repo.MessageIn(
                        chat_jid=jid,
                        wa_id=f"SEED{n:08d}",
                        ts=now - rng.randrange(_YEAR),
                        from_me=from_me,
                        sender_jid=None if from_me else (rng.choice(members) if group else jid),
                        kind=kind,
                        body=_sentence(rng) if kind == "text" else None,
                        status=rng.choice(("sent", "delivered", "read")) if from_me else None,
                    ),
                    count_unread=False,
                )
            conn.execute("UPDATE chats SET unread_count = abs(random()) % 5")
        conn.execute("ANALYZE")
        db.secure_files(path)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--chats", type=int, default=300)
    args = parser.parse_args()
    seed(args.db, args.messages, args.chats)


if __name__ == "__main__":
    main()
