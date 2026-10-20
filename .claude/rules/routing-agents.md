---
paths:
  - "TASK_PLAN.md"
  - "**/.claude/agents/**"
  - "**/.claude/settings.json"
  - "**/.claude/settings.local.json"
---

# Coordinación de Agentes, Routing y Estado Compartido

## 1. Routing por Niveles (Tiers)
- **Tier 1 (High Effort / Architecture):** `claude` (Claude 3.7 Sonnet / Opus Nativo) -> Arquitectura core, debugging de concurrencia, refactorizaciones críticas y revisión de seguridad.
- **Tier 2 (Mid Effort / Feature):** `claude-ds` (DeepSeek V4) / `claude-qwen` (Qwen 3.7 Max) -> Endpoints API, componentes UI, algoritmos y features estándar.
- **Tier 3 (Low Effort / Bulk):** `claude-kimi` (Kimi K3) / `claude-qwen` (Qwen 3.7 Max) -> Escaneo de repositorio, redactar/actualizar `TASK_PLAN.md`, unit tests, tipos Zod/TS, JSDoc y linters.

## 2. Reglas Autónomas de Delegación
- **Evaluación Preliminar (>3 archivos o >200 líneas):** Si la tarea es de Tier 2 o Tier 3, advierte al usuario o genera un script/comando para delegarla al subagente correspondiente (`claude-qwen`, `claude-ds` o `claude-kimi`).
- **Plan First, Code Later:** Para tareas que requieran leer más de 5 archivos de contexto:
  1. Usa un subagente de Tier 3 (`claude-kimi` o `claude-qwen`) para escanear la estructura y redactar/actualizar `TASK_PLAN.md`.
  2. Retoma la sessión principal en Tier 1 (`claude`) para leer `TASK_PLAN.md` e implementar la solución crítica.

## 3. Estado Compartido (`TASK_PLAN.md`)
- Obligatorio usar `TASK_PLAN.md` como tablero de transferencia entre agentes.
- Cada subagente que complete una fase debe actualizar el estado de la tarea y dejar una bitácora concisa de 2 líneas antes de ceder el control.

## 4. Eficiencia de Tokens y Ciclo de Vida del Contexto
- Al alcanzar 15-20 iteraciones o completar un hito complejo, sugiere explícitamente al usuario ejecutar `/compact`.
- Al cambiar de issue o módulo no relacionado, sugiere `/clear` para iniciar una ventana de contexto limpia.
