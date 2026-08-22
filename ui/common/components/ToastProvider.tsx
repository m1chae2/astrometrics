import React, { useState, useCallback, useEffect, useRef } from 'react';
import { ToastContext, ToastType } from '../../common/hooks/useToast';
import { on as onEvent } from '../utils/eventBus';
import '../styles/toast.css';

/** Represents a single toast notification. */
interface Toast {
  id: number;
  msg: string;
  type: ToastType;
}

/** Props for the ToastProvider component. */
interface Props {
  /** Application content. */
  children?: React.ReactNode;
}

/**
 * Global provider for the toast notification system.
 * Manages toast lifecycle and listens for global toast events.
 */
export const ToastProvider: React.FC<Props> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);

  /** Removes a toast by its ID. */
  const remove = useCallback((id: number): void => {
    setToasts((toastItems) => toastItems.filter((item) => item.id !== id));
  }, []);

  /** Shows a new toast notification. */
  const show = useCallback(
    (msg: string, type: ToastType = 'success', timeoutMs = 3000) => {
      const maxLen = 200;
      const displayMsg =
        typeof msg === 'string' && msg.length > maxLen
          ? `${msg.slice(0, maxLen - 3)}...`
          : msg;
      const id = Date.now() + Math.floor(Math.random() * 1000);
      const toast: Toast = { id, msg: displayMsg, type };

      // Log errors to console for persistence.
      if (type === 'error') {
        try {
          console.error(displayMsg);
        } catch {
          // Ignore console failures.
        }
      }

      // Prevent duplicate error toasts.
      setToasts((currentToasts) => {
        if (
          type === 'error' &&
          currentToasts.some(
            (item) => item.type === 'error' && item.msg === displayMsg
          )
        ) {
          return currentToasts;
        }
        const next = [...currentToasts, toast];
        if (timeoutMs > 0) {
          setTimeout(() => remove(id), timeoutMs);
        }
        return next;
      });
    },
    [remove]
  );

  const recentKeysRef = useRef<Set<string>>(new Set());

  // Listen for programmatic toast events from non-React modules.
  useEffect(() => {
    const detach = onEvent('toast', (payload?: unknown) => {
      try {
        if (!payload) return;
        const p = payload as Record<string, unknown> | string;
        const rawText =
          typeof p === 'string'
            ? p
            : String(p['text'] ?? p['msg'] ?? p['message'] ?? '');
        const maxLen = 200;
        const text =
          typeof rawText === 'string' && rawText.length > maxLen
            ? `${rawText.slice(0, maxLen - 3)}...`
            : rawText;
        const source =
          typeof p === 'object' && p !== null && typeof p['source'] === 'string'
            ? String(p['source'])
            : 'external';
        const kind =
          typeof p === 'object' &&
            p !== null &&
            (p as Record<string, unknown>)['kind'] === 'error'
            ? 'error'
            : 'success';

        const key = `${source}:${text}`;
        const recent = recentKeysRef.current;
        if (recent.has(key)) return;
        recent.add(key);

        setTimeout(() => {
          try {
            recent.delete(key);
          } catch {
            // Ignore.
          }
        }, 4000);

        if (text) {
          show(text, kind as ToastType);
        }
      } catch {
        // Ignore failures from external emitters.
      }
    });
    return () => {
      try {
        detach();
      } catch {
        // Ignore detacher failures.
      }
    };
  }, [show]);

  // Listen for window-level CustomEvents.
  useEffect(() => {
    const handler = (evt: Event): void => {
      try {
        const customEvent = evt as CustomEvent;
        const p = customEvent?.detail;
        if (!p) return;
        const rawText =
          typeof p === 'string'
            ? p
            : String(p['text'] ?? p['msg'] ?? p['message'] ?? '');
        const maxLen = 200;
        const text =
          typeof rawText === 'string' && rawText.length > maxLen
            ? `${rawText.slice(0, maxLen - 3)}...`
            : rawText;
        const source =
          typeof p === 'object' && p !== null && typeof p['source'] === 'string'
            ? String(p['source'])
            : 'external';
        const kind =
          typeof p === 'object' &&
            p !== null &&
            (p as Record<string, unknown>)['kind'] === 'error'
            ? 'error'
            : 'success';

        const key = `${source}:${text}`;
        const recent = recentKeysRef.current;
        if (recent.has(key)) return;
        recent.add(key);

        setTimeout(() => {
          try {
            recent.delete(key);
          } catch {
            // Ignore.
          }
        }, 4000);

        if (text) {
          show(text, kind as ToastType);
        }
      } catch {
        // Ignore listener failures.
      }
    };
    window.addEventListener('astrometrics:toast', handler);
    return () => window.removeEventListener('astrometrics:toast', handler);
  }, [show]);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast ${t.type === 'success' ? 'toast-success' : 'toast-error'
              }`}
          >
            {t.msg}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};
