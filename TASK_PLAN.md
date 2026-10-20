# Plan de Trabajo y Sincronización entre Agentes

## 🤖 Matriz de Enrutamiento de Modelos (Routing Table)

| Tier / Esfuerzo | Modelos y Aliases Disponibles | Tipos de Tarea Asignadas |
| :--- | :--- | :--- |
| **Tier 1 (High Effort)** | `claude` (Claude 3.7 Sonnet / Opus Nativo) | Arquitectura core, debugging de concurrencia, refactorizaciones críticas, revisión de seguridad. |
| **Tier 2 (Mid Effort)** | `claude-ds` (DeepSeek V4)<br>`claude-qwen` (Qwen 3.7 Max) | Endpoints API, componentes UI, algoritmos, features estándar. |
| **Tier 3 (Low Effort / Bulk)** | `claude-kimi` (Kimi K3)<br>`claude-qwen` (Qwen 3.7 Max) | Escaneo de repo, redactar/actualizar `TASK_PLAN.md`, unit tests, tipos Zod/TS, JSDoc, linters. |

---

## 📋 Tablero de Estado de Tareas

### Tareas en Progreso / Pendientes
- [ ] **[Tarea #1]** Escaneo de arquitectura e inventario de archivos
  - **Asignado a:** `Tier 3 - Qwen` (`claude-qwen`) / `Tier 3 - Kimi` (`claude-kimi`)
  - **Estado:** Pendiente
  - **Archivos impactados:** `TASK_PLAN.md`
- [ ] **[Tarea #2]** Implementación de lógica de negocio o feature crítica
  - **Asignado a:** `Tier 1 - Claude` (`claude`) / `Tier 2 - DeepSeek` (`claude-ds`)
  - **Estado:** Pendiente
  - **Archivos impactados:** TBD

### Tareas Completadas
- [x] **[Setup]** Inicialización de la estructura `.claude/` y reglas de proyecto
  - **Asignado a:** `Tier 1 - Claude`
  - **Estado:** Completado

---

## 🛑 Registro de Hipótesis Descartadas (Protocolo Anti-Bucle)
*(Si un enfoque o test falla 2 veces consecutivas, se detiene la ejecución, se revierte con `git checkout -- .` y se registra el fallo aquí antes de intentar otra solución).*

- *[Fecha / Commit / Modelo]:* N/A

---

## 📝 Log de Transferencia y Bitácora entre Agentes
*(Cada subagente que complete un hito debe dejar un resumen de 2 líneas antes de transferir el control al siguiente Tier).*

- **[Setup inicial]:** Estructura base configurada con reglas modulares en `.claude/rules/`, subagentes en `.claude/agents/` y hooks en `.claude/hooks/`.
