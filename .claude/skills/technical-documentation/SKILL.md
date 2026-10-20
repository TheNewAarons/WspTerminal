# Technical Documentation Skill

## Propósito
Habilidad especializada en generación, mantenimiento y publicación de documentación técnica estructurada.

## Disparadores
- Comandos que contengan: `docs`, `documentación`, `readme`, `wiki`, `api-doc`, `openapi`, `swagger`
- Archivos en directorios: `docs/**`, `*.md`

## Flujo de Trabajo

### 1. Generación de API Docs
```bash
# Backend (FastAPI / Swagger)
python -c "import main; print(main.app.openapi())" > docs/openapi.json

# Backend (Django REST Framework / OpenAPI)
python manage.py generateschema > docs/schema.yml
```

### 2. Generación de JSDoc / Docstrings
```bash
# TypeScript / JSDoc
pnpm exec typedoc --out docs/ts-api src/

# Python Docstrings (Sphinx / MkDocs)
mkdocs build -d docs/html
```

### 3. Validación de Links y Markdown
```bash
# Validar links rotos en docs
markdown-link-check docs/**/*.md

# Linter de Markdown
markdownlint docs/**/*.md
```

### 4. Publicación / Deployment de Docs
```bash
# Publicar en GitHub Pages
gh-pages -d docs/html

# Publicar en ReadTheDocs
mkdocs gh-deploy
```

## Estructura de Documentación Estandarizada

### CLAUDE.md (Raíz)
- **WHAT:** Arquitectura y alcance (100-200 líneas)
- **WHY:** Principios y decisiones de diseño
- **HOW:** Comandos clave y estándares

### TASK_PLAN.md (Raíz)
- Estado actual de las tareas
- Bitácora de 2 líneas por agente
- Próximos pasos y responsables

### README.md (Raíz)
- Descripción general del proyecto
- Guía de inicio rápido
- Enlaces a documentación detallada

### Directorio `docs/`
- `architecture/` - Decisiones de diseño, diagramas C4
- `api/` - Especificación OpenAPI, endpoints, esquemas
- `deployment/` - Guías de despliegue, variables de entorno
- `guides/` - Guías de desarrollo, configuración local
- `rules/` - Reglas de calidad y estilo de código

## Principios de Documentación

### Living Documentation (Documentación Viva)
- Mantener la documentación sincronizada con el código
- Actualizar diagramas ante cambios de arquitectura
- Integrar la documentación en la revisión de código

### Markdown Estricto
- Encabezados consistentes (`#`, `##`, `###`)
- Listas estructuradas
- Bloques de código con sintaxis especificada
- Links relativos funcionales

### Glosario Técnico
- Definir términos específicos del dominio
- Evitar ambigüedades en la terminología
- Mantener la consistencia en nombres de módulos y variables