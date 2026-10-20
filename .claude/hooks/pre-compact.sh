#!/bin/bash
# Pre-compact hook: Backup de estado antes de la compactación automática
set -e

BACKUP_DIR=".claude/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/pre-compact_${TIMESTAMP}.md"

echo "💾 [Pre-Compact Hook] Iniciando backup de estado antes de compactar..."

# Crear directorio de backups si no existe
mkdir -p "$BACKUP_DIR"

# 1. Backup de TASK_PLAN.md
if [ -f "TASK_PLAN.md" ]; then
  echo "📋 Backupeando TASK_PLAN.md..."
  cp "TASK_PLAN.md" "${BACKUP_DIR}/TASK_PLAN_${TIMESTAMP}.md"
fi

# 2. Backup de configuración local
if [ -f ".claude/settings.local.json" ]; then
  echo "⚙️ Backupeando settings.local.json..."
  cp ".claude/settings.local.json" "${BACKUP_DIR}/settings.local_${TIMESTAMP}.json"
fi

# 3. Capturar estado de Git (branch, diff resumido)
echo "🌿 Capturando estado de Git..."
git branch --show-current > "${BACKUP_DIR}/git-branch_${TIMESTAMP}.txt" 2>/dev/null || echo "No git repo" > "${BACKUP_DIR}/git-branch_${TIMESTAMP}.txt"
git diff --stat > "${BACKUP_DIR}/git-diff-stat_${TIMESTAMP}.txt" 2>/dev/null || echo "No changes" > "${BACKUP_DIR}/git-diff-stat_${TIMESTAMP}.txt"

# 4. Generar resumen de contexto actual
cat > "$BACKUP_FILE" << EOF
# Pre-Compact Backup - $TIMESTAMP

## Estado del Proyecto
- **Fecha:** $(date)
- **Rama Git:** $(git branch --show-current 2>/dev/null || echo "N/A")
- **Cambios pendientes:** $(git diff --stat 2>/dev/null | wc -l) archivos modificados

## TASK_PLAN.md
$(cat TASK_PLAN.md 2>/dev/null || echo "No disponible")

## Cambios Git Recientes
\`\`\`
$(git diff --stat 2>/dev/null || echo "Sin cambios")
\`\`\`

---
*Backup automático generado antes de la compactación*
EOF

echo "✅ Backup completado: $BACKUP_FILE"
exit 0