#!/bin/bash
# Post-commit hook: Limpieza y notificación después de commit
set -e

COMMIT_HASH="$1"

echo "📝 [Post-Commit Hook] Commit: ${COMMIT_HASH:-N/A}"

# 1. Limpiar backups antiguos (mantener últimos 7 días)
BACKUP_DIR=".claude/backups"
if [ -d "$BACKUP_DIR" ]; then
  echo "🧹 Limpiando backups antiguos..."
  find "$BACKUP_DIR" -type f -mtime +7 -delete 2>/dev/null || true
fi

# 2. Limpiar archivos temporales de build
echo "🗑️  Limpiando archivos temporales..."
find . -type d -name ".next" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 3. Actualizar TASK_PLAN.md con estado del commit
if [ -f "TASK_PLAN.md" ]; then
  echo "📊 Actualizando TASK_PLAN.md..."
  cat >> "TASK_PLAN.md" << EOF

---
### Commit: ${COMMIT_HASH:-$(git rev-parse --short HEAD 2>/dev/null || echo "N/A")}
- **Fecha:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- **Rama:** $(git branch --show-current 2>/dev/null || echo "N/A")
- **Estado:** Completado ✅
EOF
fi

# 4. Sugerir /compact si hay muchas iteraciones
if [ -f ".claude/iteration-count" ]; then
  ITERATIONS=$(cat ".claude/iteration-count")
  if [ "$ITERATIONS" -ge 15 ]; then
    echo "💡 [SUGERENCIA] Has alcanzado $ITERATIONS iteraciones. Considera ejecutar /compact para optimizar el contexto."
  fi
fi

echo "✅ Post-commit completado."
exit 0