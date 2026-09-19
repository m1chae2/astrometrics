
/**
 * @fileoverview Frontend NotificationService for dispatching native desktop notifications.
 * Bridges application alerts to Electron IPC / GNOME notification center.
 */

export const NotificationService = {
    /**
     * Dispatches a native desktop notification if supported, falling back to console.
     * @param title The notification title header.
     * @param body The descriptive message body.
     */
    show: (title: string, body: string): void => {
        if (window.astrometrics?.app?.showNotification) {
            window.astrometrics.app.showNotification(title, body);
        } else {
            console.log('Notification:', title, body);
        }
    }
};
