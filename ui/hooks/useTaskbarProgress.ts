import { useCallback } from 'react';

type ProgressMode = 'none' | 'normal' | 'indeterminate' | 'error' | 'paused';

interface TaskbarProgressControl {
    setProgress: (value: number, mode?: ProgressMode) => void;
    finish: () => void;
    error: () => void;
}

export const useTaskbarProgress = (): TaskbarProgressControl => {
    const setProgress = useCallback((value: number, mode: ProgressMode = 'normal') => {
        if (window.astrometrics?.app?.setProgress) {
            window.astrometrics.app.setProgress(value, mode);
        }
    }, []);

    const finish = useCallback(() => {
        if (window.astrometrics?.app?.setProgress) {
            window.astrometrics.app.setProgress(-1, 'none');
        }
    }, []);

    const error = useCallback(() => {
        if (window.astrometrics?.app?.setProgress) {
            window.astrometrics.app.setProgress(1, 'error');
        }
    }, []);

    return { setProgress, finish, error };
};
