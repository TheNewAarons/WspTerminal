# Security & Audit Skill

## Propósito
Habilidad especializada en auditorías de seguridad, revisión de vulnerabilidades y cumplimiento de estándares de defensa.

## Disparadores
- Comandos que contengan: `security`, `seguridad`, `audit`, `auditoría`, `vulnerabilidad`, `pentest`, `OWASP`
- Archivos en directorios: `security/**`, `.claude/agents/security-reviewer.json`

## Flujo de Trabajo

### 1. Auditoría de Código (SAST)
```bash
# Análisis estático de seguridad
ruff check . --select S
bandit -r apps/ -f json
npm audit --audit-level=high
```

### 2. Verificación de Secretos
```bash
# Buscar credenciales expuestas
git secrets --scan-history
trufflehog git file://.
detect-secrets scan
```

### 3. Análisis de Dependencias
```bash
# Vulnerabilidades en dependencias
npm audit
safety check
pip-audit
```

### 4. Revisión de Permisos y Sandbox
```bash
# Verificar configuración de sandbox
cat .claude/settings.json | jq '.sandbox'
# Revisar deny/allow rules
cat .claude/settings.json | jq '.permissions'
```

## Checklist de Auditoría

### Autenticación y Autorización
- [ ] Contraseñas hasheadas (bcrypt/argon2)
- [ ] JWT con expiración y refresh tokens
- [ ] RBAC implementado correctamente
- [ ] Sesiones con timeout inactivo
- [ ] Rate limiting en endpoints críticos

### Criptografía
- [ ] TLS 1.2+ en toda comunicación
- [ ] Datos sensibles en tránsito encriptados
- [ ] Secrets management (no hardcodeados)
- [ ] Encriptación PII en reposo

### Inyección
- [ ] SQL injection prevenido (ORM parametrizado)
- [ ] XSS prevenido (escaping automático)
- [ ] Command injection prevenido
- [ ] Template injection prevenido

### Exposición de Datos
- [ ] .gitignore configurado correctamente
- [ ] .env no commitado
- [ ] Logs sin secrets
- [ ] Responses sin PII sensible

### Protocolo Anti-Bucle (Regla de 2 Intentos)
Si una corrección de seguridad falla 2 veces consecutivas:
1. Detener ejecución inmediatamente
2. Revertir cambios (`git checkout -- .`)
3. Registrar en `TASK_PLAN.md` la vulnerabilidad y enfoque descartado
4. Solicitar asistencia de seguridad antes de un tercer intento

## Estándares de Seguridad

### Zero Trust
- Validar siempre las entradas
- Denegar por defecto, permitir explícitamente
- Microsegmentación de responsabilidades

### Variables de Entorno Sensibles (Bloqueadas)
- `NPM_TOKEN`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `JWT_SECRET`
- `API_KEY`
- `DATABASE_URL`
- `REDIS_URL`

### Dominios Permitidos
- `github.com`
- `*.npmjs.org`
- `*.pypi.org`
- `api.anthropic.com`

### Dominios Bloqueados
- `uploads.github.com`
- `pastebin.com`
- `gist.githubusercontent.com`