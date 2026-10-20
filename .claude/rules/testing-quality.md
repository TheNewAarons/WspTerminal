---
paths:
  - "tests/**"
  - "**/test_*.py"
  - ".claude/skills/testing-skill/**"
---

# Calidad, pruebas y anti-bucle

- 2 fallos consecutivos → detener, revertir cambios no funcionales, registrar el enfoque descartado en `TASK_PLAN.md`, analizar causa raíz antes de un tercer intento.
- Antes de entregar: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy wsp`.
- Mockear neonize, osascript, keyring y reloj; DB de tests en `tmp_path`, nunca la real.
