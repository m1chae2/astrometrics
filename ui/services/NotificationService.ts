
export const NotificationService = {
    show: (title: string, body: string) => {
        if (window.astrometrics?.app?.showNotification) {
            window.astrometrics.app.showNotification(title, body);
        } else {
            console.log('Notification:', title, body);
        }
    }
};
