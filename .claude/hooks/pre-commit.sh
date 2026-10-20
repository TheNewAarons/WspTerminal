#!/bin/bash
# Pre-commit hook ejecutado por Claude Code
set -e

echo "🔍 [Pre-Commit Hook] Iniciando validaciones deterministas..."

# 1. Validación de Tipos en TypeScript (Frontend / Mobile)
if [ -f "package.json" ]; then
  echo "🔷 Verificando tipos TypeScript con pnpm..."
  pnpm exec tsc --noEmit || {
    echo "❌ [ERROR] Falló la verificación de tipos en TypeScript."
    exit 1
  }
fi

# 2. Validación de Linter en Python (Django / FastAPI)
if command -v ruff &> /dev/null; then
  echo "🐍 Verificando linter Python con Ruff..."
  ruff check . || {
    echo "❌ [ERROR] Falló la verificación de linter con Ruff."
    exit 1
  }
elif command -v flake8 &> /dev/null; then
  echo "🐍 Verificando linter Python con Flake8..."
  flake8 . || {
    echo "❌ [ERROR] Falló la verificación con Flake8."
    exit 1
  }
fi

echo "✅ [Pre-Commit Hook] Validaciones aprobadas con éxito. Procediendo con el commit."
exit 0
