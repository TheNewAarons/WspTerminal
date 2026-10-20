---
paths:
  - "wsp/**"
  - "tests/**"
---

# Estándares Python (wsp)

- SQLite: solo el daemon escribe; SQL siempre parametrizado (`?`), nunca f-strings. Esquema solo vía `wsp/store/migrations/NNNN_*.sql` aplicado por `PRAGMA user_version`; cambios de esquema pasan por `database-agent`.
- Bordes del protocolo (socket Unix) validados con pydantic v2; mensajes versionados.
- Logging estructurado con `logging`; prohibido `print()` en daemon; nunca contenido de mensajes ni números completos.
- Archivos de datos y socket con permisos 600; secretos solo en keyring.
- `mypy --strict`, funciones con tipo de retorno explícito; I/O async sin bloquear el loop.
