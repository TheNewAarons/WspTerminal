---
paths:
  - "apps/backend-core/**"
  - "apps/backend-api/**"
---

# Directrices y Estándares Backend (Python)

## 1. Single Source of Truth & Migraciones (Django ORM)
- `apps/backend-core` (Django) es el **ÚNICO propietario autorizado** para definir modelos relacionales, crear migraciones (`python manage.py makemigrations`) y ejecutarlas (`python manage.py migrate`).
- `apps/backend-api` (FastAPI) solo mapea las tablas existentes en modo lectura o consulta asíncrona. Queda estrictamente prohibido generar migraciones con Alembic o modificar esquemas desde FastAPI.
- **Estrategia Breaking Changes en BD:** (Paso 1: Agregar nuevo campo sin borrar el anterior, Paso 2: Migrar datos o código, Paso 3: Eliminar campo obsoleto en commit posterior).

## 2. Seguridad, Variables de Entorno y ORM
- Usar obligatoriamente el ORM parametrizado (Django ORM / SQLAlchemy). Prohibido concatenar strings o formatear variables (`f-strings`) en SQL.
- Validar todas las entradas mediante esquemas **Pydantic v2** (FastAPI) o **Serializadores estrictos** (DRF).
- FastAPI valida entornos al iniciar mediante `BaseSettings`. Django falla inmediatamente en `settings.py` si falta una variable crítica.

## 3. Tareas Asíncronas y Logging
- Usar exclusivamente el módulo `logging` estructurado. Prohibido el uso de `print()` en código de producción.
- Tareas pesadas y correos deben delegarse a Celery Worker (`celery -A core worker -l info`).
- Para depurar tareas bloqueadas: `celery -A core worker -l debug`.
