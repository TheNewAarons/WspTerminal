---
paths:
  - "TASK_PLAN.md"
  - "**/hooks/**"
  - "**/commit-msg"
  - "**/.git/**"
  - ".claude/skills/deploy-skill/**"
---

# Flujo de Trabajo en Git y GitHub (`gh` CLI)

## 1. Alcance Operativo (Sin CI / GitHub Actions)
- **IMPORTANTE:** Este proyecto NO utiliza integraciones continuas (CI / GitHub Actions). Prohibido crear o esperar la ejecución de workflows en `.github/workflows/`.
- El flujo se limita estrictamente a: ** crear ramas, hacer commits atómicos, subir cambios (push) y realizar merges**.

## 2. Estrategia de Ramificación (Git Flow / Trunk-Based)
- Prohibido hacer commits o push directos a la rama principal (`main` / `master`).
- Nombres de ramas estandarizados:
  - **Fases:** `phase-N-<tema>`
  - **Funcionalidades:** `feat/{{short-description}}`
  - **Correcciones:** `fix/{{issue-id}}/{{short-description}}` o `bugfix/{{issue-number}}`
  - **Refactorización:** `refactor/{{modulo}}`
  - **Documentación / Chores:** `docs/{{tema}}` o `chore/{{tarea}}`

## 3. Mensajes de Commit (Conventional Commits)
- Estructura obligatoria: `<type>(<scope>): <subject>`
- **Tipos de producción:** `feat`, `fix`, `perf`.
- **Tipos de desarrollo:** `docs`, `style`, `refactor`, `test`, `chore`, `build`.
- **Reglas del mensaje:** Sujeto en tiempo presente e imperativo, minúsculas, sin punto final (máximo 100 caracteres). Commits atómicos e aislados.

## 4. Pull Requests y Merges (vía `gh` CLI o Local)
- **Creación de PR:** Usar `gh pr create` estructurando la descripción básica y vinculación a issues (`Closes #123`).
- **Fusión (Merge):** Usar `gh pr merge --squash` o realizar merge manual en local. **No esperar ningún estado de aprobación de CI/Checks.**

## 5. Mantenimiento Autónomo de `.gitignore`
Asegurar siempre que archivos `.env`, `.claude/settings.local.json`, `.venv/`, la DB SQLite, la sesión de neonize y binarios estén en `.gitignore`.
