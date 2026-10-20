# Testing Skill

## Propósito
Habilidad especializada en pruebas unitarias, integración y protocolos de calidad.

## Disparadores
- Comandos que contengan: `test`, `testing`, `prueba`, `mock`, `fixture`
- Archivos en directorios: `tests/**`

## Flujo de Trabajo

### 1. Pruebas Unitarias
```bash
# Backend (Python)
pytest tests/unit/ -v --cov=src

# Frontend/Mobile (pnpm)
pnpm test:unit
```

### 2. Pruebas de Integración
```bash
# Backend
pytest tests/integration/ -v

# Frontend
pnpm test:integration
``### 3. Cobertura de Código
```bash
# Backend
pytest --cov=src --cov-report=html

# Frontend
pnpm test:cov
```

### 4. Mocks y Aislamiento
- Mockear servicios externos y APIs de terceros
- Mockear envíos de correo Celery
- Pruebas de BD en transacciones aisladas que se revierten

## Estándares de Calidad

### Protocolo Anti-Bucle (2 Intentos)
Si una solución falla 2 veces consecutivas:
1. Detener ejecución inmediatamente
2. Revertir cambios locales (`git checkout -- .`)
3. Registrar en `TASK_PLAN.md` la hipótesis descartada
4. Analizar causa raíz o solicitar asistencia

### Verificación Previa a la Entrega
- Frontend/Mobile: `pnpm exec tsc --noEmit` y `pnpm run lint`
- Backend: `ruff check .` y `pytest`