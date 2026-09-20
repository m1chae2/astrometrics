
/**
 * @fileoverview Frontend NotificationService for dispatching native desktop notifications.
 * Bridges application alerts to Electron IPC / GNOME notification center.
 */

export interface NotificationOptions {
    urgency?: 'normal' | 'critical';
    actions?: string[];
}

export const NotificationService = {
    /**
     * Dispatches a native desktop notification if supported, falling back to console.
     * @param title The notification title header.
     * @param body The descriptive message body.
     * @param options Additional notification options like urgency level and interactive action buttons.
     */
    show: (title: string, body: string, options?: NotificationOptions): void => {
        if (window.astrometrics?.app?.showNotification) {
            window.astrometrics.app.showNotification(title, body, options);
        } else {
            console.log(`[Notification ${options?.urgency || 'normal'}]`, title, body);
        }
    },

    /**
     * Dispatches a critical desktop notification that persists in the OS notification center
     * and plays an alert sound (e.g. for sequence failures, weather alerts, or guide star loss).
     * @param title The critical alert title.
     * @param body The alert details.
     * @param actions Optional action button labels.
     */
    critical: (title: string, body: string, actions?: string[]): void => {
        NotificationService.show(title, body, { urgency: 'critical', actions });
    }
};
