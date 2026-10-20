#!/bin/bash
# Post-edit hook ejecutado por Claude Code para formateo automático y validación
TARGET_FILE="$1"

# Si no se pasó un archivo como argumento, salir silenciosamente
if [ -z "$TARGET_FILE" ]; then
  exit 0
fi

echo "🔧 [Post-Edit Hook] Procesando: $TARGET_FILE"

# 1. Formateo de archivos Python (.py)
if [[ "$TARGET_FILE" == *.py ]]; then
  if command -v ruff &> /dev/null; then
    echo "🐍 Formateando Python con Ruff..."
    ruff format "$TARGET_FILE" &> /dev/null
    ruff check --fix "$TARGET_FILE" &> /dev/null
  elif command -v black &> /dev/null; then
    black "$TARGET_FILE" &> /dev/null
  fi

# 2. Formateo de TypeScript, JavaScript, JSON, CSS y Markdown
elif [[ "$TARGET_FILE" =~ \.(ts|tsx|js|jsx|json|css|md|astro)$ ]]; then
  if [ -f "package.json" ] && pnpm exec prettier --version &> /dev/null; then
    echo "✨ Formateando con Prettier..."
    pnpm exec prettier --write "$TARGET_FILE" &> /dev/null
  elif command -v prettier &> /dev/null; then
    prettier --write "$TARGET_FILE" &> /dev/null
  fi

# 3. Formateo de PHP (para compatibilidad con WordPress/Symfony)
elif [[ "$TARGET_FILE" == *.php ]]; then
  if command -v php-cs-fixer &> /dev/null; then
    echo "PHP Formateando con php-cs-fixer..."
    php-cs-fixer fix "$TARGET_FILE" &> /dev/null
  fi
fi

# 4. Validación de secrets en archivos editados
if [[ "$TARGET_FILE" == *.py ]] || [[ "$TARGET_FILE" == *.js ]] || [[ "$TARGET_FILE" == *.ts ]] || [[ "$TARGET_FILE" == *.json ]]; then
  if grep -iE "(api_key|secret|password|token|private_key)" "$TARGET_FILE" &> /dev/null; then
    echo "⚠️  [ADVERTENCIA] Posible secreto detectado en $TARGET_FILE"
    echo "    Verifique que no sea un valor hardcodeado."
  fi
fi

echo "✅ Formateo completado."
exit 0
