---
paths:
  - "apps/web/**"
  - "apps/mobile/**"
  - "packages/**"
---

# Directrices y Estándares Frontend & Mobile

## 1. Gestor de Paquetes Estricto
- Usar **EXCLUSIVAMENTE `pnpm`** (y `pnpm dlx` para binarios ejecutables). Prohibido utilizar `npm`, `npx` o `yarn`. Prohibido generar `package-lock.json` o `yarn.lock`.

## 2. Gestión de Estado y APIs
- **TanStack Query (React Query):** Estándar obligatorio y responsabilidad exclusiva para peticiones asíncronas a Django/FastAPI, manejo de caché y mutaciones. Encapsular siempre en Custom Hooks (`/src/hooks/`).
- **Stores de Zustand (`/src/store/`):** Exclusivamente para tokens JWT, preferencias de tema o estado local de cliente con persistencia.
- **Componentes UI (`/src/components/ui/`):** Deben ser puramente visuales, desacoplados y reutilizables.

## 3. Variables de Entorno Públicas
- Solo exponer variables de entorno que inicien con prefijos públicos autorizados (`PUBLIC_` en Astro o `EXPO_PUBLIC_` en Expo). Nunca exponer llaves secretas de backend en el cliente.
