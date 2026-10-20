# Pauta de Arquitectura y Desarrollo

## WHAT: Arquitectura y Alcance
Monorepo modular híbrido escalable por dominios:
- `apps/backend-core`: Django REST Framework (Autenticación, RBAC, PII, Admin, Celery).
- `apps/backend-api`: FastAPI (Asíncrono, lectura masiva, Pydantic v2, Redis).
- `apps/web`: Astro + React (SSR/SSG, Dashboards, TanStack Query, Zustand).
- `apps/mobile`: React Native + Expo Router (iOS/Android, NativeWind).
- `packages/`: Tipos compartidos (`shared-types`) y configuraciones (`config`).

## WHY: Principios y Decisiones de Diseño
- Single Source of Truth DB: Django ORM es el ÚNICO propietario y gestor de esquemas/migraciones en PostgreSQL. FastAPI lee en modo asíncrono.
- Gestor de Paquetes Estricto: Usar exclusivamente `pnpm` en Frontend/Mobile. Prohibido `npm`, `npx` o `yarn`.
- Zero Trust & Protocolo Anti-Bucle: Todo código sintético debe validarse con linters/tests. Límite de 2 intentos consecutivos ante un error antes de revertir y replantear en `TASK_PLAN.md`.
- Eficiencia de Tokens: Respuestas sin introducción/despedida ("Zero Fluff"). Explicación de cambios en máximo 2 líneas.

## HOW: Comandos Clave y Estándares
- Docker Local: `colima start --cpu 2 --memory 4` | TUI: `lazydocker`
- Backend Core (Django): `python manage.py runserver` | `pytest` | `ruff check .`
- Backend API (FastAPI): `uvicorn main:app --reload` | `pytest`
- Web & Mobile (pnpm): `pnpm dev` | `pnpm exec tsc --noEmit` | `pnpm test`
- GitHub CLI: `gh pr create` | `gh pr merge --squash` | `gh issue list`

---

## 🤖 Claude Code: Estructura de Agentes y Skills

### Agente Principal y Subagentes Especializados
- **Tier 1 (High Effort / Architecture):** `claude` (Claude Sonnet 5 / Opus Nativo) -> Arquitectura core, debugging de concurrencia, refactorizaciones críticas y revisión de seguridad.
- **Tier 2 (Mid Effort / Feature):** `claude-ds` (DeepSeek V4) / `claude-qwen` (Qwen 3.7 Max) -> Endpoints API, componentes UI, algoritmos y features estándar.
- **Tier 3 (Low Effort / Bulk):** `claude-kimi` (Kimi K3) -> Escaneo de repositorio, redactar/actualizar `TASK_PLAN.md`, unit tests, tipos Zod/TS, JSDoc y linters.

### Skills Personalizadas Disponibles
- **deploy-skill:** Despliegues automatizados, validaciones previas, gestión de entornos.
- **database-skill:** Migraciones, optimización, backup/restore, seguridad de BD.
- **testing-skill:** Protocolos de prueba, mocks, cobertura, anti-bucle.
- **security-audit:** Auditorías de seguridad, revisión de vulnerabilidades, cumplimiento de estándares de defensa.
- **technical-documentation:** Generación, mantenimiento y publicación de documentación técnica estructurada.

### Hooks de Automatización
- **pre-commit.sh:** Validaciones TypeScript y Ruff antes de commits.
- **post-edit.sh:** Formateo automático y detección de secrets.
- **pre-command.sh:** Bloqueo de comandos destructivos y exportación de secrets.
- **pre-compact.sh:** Backup de TASK_PLAN.md y estado de Git antes de compactar.
- **post-commit.sh:** Limpieza de backups antiguos y actualización de TASK_PLAN.md.

---

## 🛠️ Comandos CLI de Claude Code

### Gestión de Contexto y Memoria
- `/compact` - Compactación inmediata de la ventana de contexto actual.
- `/memory` - Abre el panel interactivo de control sobre la memoria del sistema (Auto Memory).
- `/clear` - Elimina el historial de conversación activo y reinicia el contexto.
- `/rewind` - Restaura el código a un punto de control (checkpoint) previo.

### Configuración y Modelos
- `/effort` - Ajusta el nivel de razonamiento para el modelo activo (low, medium, high, xhigh).
- `/config` - Abre el menú interactivo de ajustes de configuración.
- `/model` - Selecciona o cambia el modelo principal de la sesión.
- `/fast` - Activa o desactiva el modo rápido (fast mode) para ejecuciones de alta velocidad.

### Diagnóstico y Aislamiento
- `/doctor` - Diagnostica el estado de la ventana de contexto, límite de uso de skills e integración de herramientas.
- `/sandbox` - Despliega el panel de inspección y modos del aislamiento de Sandbox.
- `/add-dir` - Agrega un directorio externo como directorio de trabajo temporal en la sesión.

---

## 📋 Convenciones Sintácticas Estrictas
- Todas las funciones exportadas deben declarar sus tipos de retorno de forma explícita.
- Los commits deben seguir la convención de Commits Convencionales (ej. `feat:`, `fix:`, `refactor:`).
- Las respuestas deben seguir el protocolo "Zero Fluff" (máximo 2 líneas de explicación).
- Las tareas que excedan 3 archivos o 200 líneas deben delegarse a un subagente Tier 2/Tier 3.
