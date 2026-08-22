# Utilities

This folder contains small cross-cutting utilities used by the UI.

## Key Modules

- **eventBus.ts**: A simple, typed event bus for cross-component communication.
- **emitToast.ts**: Helper to emit toast notifications from anywhere (React or non-React).
- **reportError.ts**: Centralized error reporting (logs to console and emits toasts).
- **ToastProvider.tsx**: Global React provider for the toast system.
- **ConfirmDialog.tsx**: Reusable confirmation modal.
- **plotTools.ts**: Shared physics and plotting utilities.

## Deprecated

- **backend.ts**: (Removed) Core backend logic has moved to `src/services/backendApi.ts` and `src/types/backendTypes.ts`.

## Architecture Note

For backend communication, use the **Services** layer (`src/services/backendApi.ts`) instead of putting fetch calls directly in components. This keeps the data fetching logic reusable and easier to mock in tests.
