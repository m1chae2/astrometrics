import { createContext, useContext } from 'react';

/** Possible levels for a toast notification. */
export type ToastType = 'success' | 'error';

/** Function type for showing a toast notification. */
export type ToastShowFn = (
    msg: string,
    type?: ToastType,
    timeoutMs?: number
) => void;

/** Context shape for the toast system. */
export interface ToastContextShape {
    /** Function to display a toast message. */
    show: ToastShowFn;
}

/** React Context for toast notifications. */
export const ToastContext = createContext<ToastContextShape | null>(null);

/**
 * Hook to access the global toast system.
 * Must be used within a ToastProvider.
 */
export function useToast(): ToastContextShape {
    const c = useContext(ToastContext);
    if (!c) {
        throw new Error('useToast must be used inside ToastProvider');
    }
    return c;
}
