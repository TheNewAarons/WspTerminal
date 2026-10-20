# Deploy Skill

## Propósito
Habilidad especializada para despliegues automatizados y gestión de entornos.

## Disparadores
- Comandos que contengan: `deploy`, `desplegar`, `publicar`, `release`
- Archivos en directorios: `deploy/**`, `scripts/deploy/**`

## Flujo de Trabajo

### 1. Pre-Despliegue
```bash
# Validaciones previas obligatorias
pnpm exec tsc --noEmit
pnpm run lint
pnpm test
ruff check .
pytest
```

### 2. Construcción
```bash
# Frontend
pnpm build

# Backend Core (Django)
cd apps/backend-core && python manage.py collectstatic --noinput

# Backend API (FastAPI)
cd apps/backend-api && pip install -r requirements.txt
```

### 3. Despliegue
```bash
# Ejecutar script de despliegue
./deploy/deploy.sh
```

### 4. Post-Despliegue
- Verificar health checks
- Revisar logs de aplicación
- Confirmar estado de base de datos

## Configuración de Entornos

### Producción
- Dominio: `api.proyecto.com`
- BD: PostgreSQL en cluster principal
- Cache: Redis cluster

### Staging
- Dominio: `staging.proyecto.com`
- BD: PostgreSQL de staging
- Cache: Redis local

## Variables de Entorno Críticas
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `API_KEY`
- `SENTRY_DSN`

## Seguridad
- Nunca hardcodear credenciales
- Usar always variables de entorno
- Validar que secrets no estén en logs
- Rotación automática de claves API