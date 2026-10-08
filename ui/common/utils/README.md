# Utilities

This folder contains small cross-cutting utilities used by the UI.

## Key Modules

- **eventBus.ts**: A simple, typed event bus for cross-component communication.
- **emitToast.ts**: Helper to emit toast notifications from anywhere (React or non-React).
- **reportError.ts**: Centralized error reporting (logs to console and emits toasts).
- **ToastProvider.tsx**: Global React provider for the toast system.
- **ConfirmDialog.tsx**: Reusable confirmation modal.
- **plotTools.ts**: Shared physics and plotting utilities.
- **dataUrl.ts**: Decodes a `data:` URL (a picture sent as text) into a Blob. The app's security policy blocks `fetch` on `data:` URLs, so use this instead.
- **socketClient.ts**: The app's WebSocket connections to the backend. `socketClient` keeps the `/ws/events` connection open and turns its messages into events. `openTerminalSocket` opens the Python terminal's `/ws/terminal` connection.

## Talking to the backend

The app may reach only the backend's public interface, which `backend/public_interface.py` declares. Use `callBackend` and `fetchImageFile` in `ui/common/services/backendApi.ts`, or the socket helpers in `socketClient.ts`. These two files are the only ones allowed to call `fetch` or open a WebSocket. An ESLint rule enforces this, and a backend test checks every path they use against the declaration.
