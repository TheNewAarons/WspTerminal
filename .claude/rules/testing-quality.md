---
paths:
  - "**/tests/**"
  - "**/test_*.py"
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "**/*_test.go"
  - ".claude/skills/testing-skill/**"
---

# Calidad de Código, Pruebas y Protocolo Anti-Bucle

## 1. Protocolo Anti-Bucle (Regla de 2 Intentos)
- Si una solución, corrección de bug o prueba unitaria falla **2 veces consecutivas**:
  1. Detener la ejecución inmediatamente.
  2. Revertir los cambios locales no funcionales (`git checkout -- .`).
  3. Registrar en `TASK_PLAN.md` la hipótesis o enfoque descartado.
  4. Analizar la causa raíz o solicitar asistencia al usuario antes de un tercer intento.

## 2. Verificación Previa a la Entrega
- Antes de dar una tarea por terminada, ejecutar obligatoriamente:
  - **Frontend/Mobile:** `pnpm exec tsc --noEmit` y `pnpm run lint`
  - **Backend:** `ruff check .` y `pytest`

## 3. Aislamiento y Mocks
- Mappear con mocks cualquier servicio externo, APIs de terceros o envíos de correo Celery.
- Las pruebas de base de datos deben ser independientes y ejecutarse dentro de transacciones aisladas que se reviertan al finalizar.
