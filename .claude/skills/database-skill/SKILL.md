# Database Skill

## Propósito
Habilidad especializada en operaciones de base de datos, migraciones y optimización.

## Disparadores
- Comandos que contengan: `migrar`, `migrate`, `schema`, `bd`, `base de datos`
- Archivos en directorios: `apps/backend-core/**`

## Flujo de Trabajo

### 1. Crear Migración
```bash
cd apps/backend-core
python manage.py makemigrations
python manage.py migrate
```

### 2. Validar Esquema
```bash
# Verificar estado de migraciones
python manage.py showmigrations

# Verificar consistencia
python manage.py check
```

### 3. Optimización
```bash
# Analizar query lento
EXPLAIN ANALYZE SELECT * FROM tabla WHERE condition;

# Reindexar
REINDEX DATABASE nombre_db;
```

### 4. Backup
```bash
# Backup completo
pg_dump -h host -U user -F c nombre_db > backup.sql

# Backup incremental
pg_dump -h host -U user --data-only --table=tabla nombre_db > incremental.sql
```

## Principios de Diseño

### Single Source of Truth
- **Django ORM** es el ÚNICO propietario de esquemas
- FastAPI solo mapea lecturas
- Prohibido usar Alembic

### Estrategia de Breaking Changes
1. Agregar nuevo campo sin borrar anterior
2. Migrar datos o código
3. Eliminar campo obsoleto en commit posterior

### Seguridad
- Usar siempre parámetros preparados
- Validar entradas con Pydantic v2
- No exponer credenciales en logs
- Encriptación de datos sensibles (PII)