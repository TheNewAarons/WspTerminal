#!/bin/bash
# Pre-command hook: Validaciones de seguridad antes de ejecutar comandos críticos
set -e

COMMAND="$1"

echo "🛡️ [Pre-Command Hook] Validando seguridad para: $COMMAND"

# 1. Bloquear comandos destructivos globales
if echo "$COMMAND" | grep -qE "rm\s+-rf\s+/|dd\s+if=|mkfs|shutdown|reboot|init\s+0"; then
  echo "❌ [SEGURIDAD] Comando destructivo detectado y bloqueado: $COMMAND"
  exit 1
fi

# 2. Validar que no se intenten borrar archivos de configuración
if echo "$COMMAND" | grep -qE "rm\s+.*\.claude/|rm\s+.*CLAUDE\.md|rm\s+.*TASK_PLAN\.md"; then
  echo "❌ [SEGURIDAD] Intento de eliminación de archivos críticos del proyecto detectado"
  exit 1
fi

# 3. Bloquear force push a main/master
if echo "$COMMAND" | grep -qE "git\s+push\s+.*--force.*origin\s+(main|master)"; then
  echo "❌ [SEGURIDAD] Force push a rama principal prohibido"
  exit 1
fi

# 4. Validar que no se expongan variables de entorno sensibles
if echo "$COMMAND" | grep -qE "export\s+(AWS_|NPM_TOKEN|SECRET|PRIVATE|PASSWORD|API_KEY)"; then
  echo "❌ [SEGURIDAD] Exportación de variables sensibles detectada"
  exit 1
fi

# 5. Permitir pero advertir sobre curl a URLs externas
if echo "$COMMAND" | grep -qE "curl\s+http"; then
  echo "⚠️  [ADVERTENCIA] Uso de curl detectado. Verifique la URL."
fi

echo "✅ Validaciones de seguridad aprobadas."
exit 0