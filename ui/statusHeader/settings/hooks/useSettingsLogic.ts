import { useState, useEffect } from 'react';
import { getSystemConfig, saveSystemConfig } from '../../../common/services/systemService';
import { enterMonitoringMode, enterControllerMode } from '../../../common/services/observatoryService';
import { useToast } from '../../../common/hooks/useToast';
import { reportError } from '../../../common/utils/reportError';
import { useBackendFetch } from '../../../common/hooks/useBackendFetch';
import { ConfigData } from '../utils/configUtils';

export const DEFAULT_SETTINGS_TAB = 'General';

export const useSettingsLogic = (open: boolean, closing: boolean, onClose: () => void) => {
    const { show: showToast } = useToast();

    // UI State
    const [activeConfigTab, setActiveConfigTab] = useState<string>(DEFAULT_SETTINGS_TAB);

    // Form State
    const [secondaryWindowEnabled, setSecondaryWindowEnabled] = useState(false);

    // Monitoring/controller mode: an immediate action, not part of the
    // deferred config-patch/save flow -- the real backing for the retired
    // "Safe Mode" config checkbox (Wayfinding_Library_Architecture.md M8).
    // This local flag reflects only the last action taken in this session,
    // not a persisted status the backend can be queried for yet.
    const [controllerModeEnabled, setControllerModeEnabled] = useState(false);
    const [isChangingControlMode, setIsChangingControlMode] = useState(false);

    // Config Data: `configData` is the editable working copy shown in every
    // form; `configPatch` mirrors only the fields the user actually changed
    // (via handleConfigChange) and is the only thing ever sent to
    // saveSystemConfig. Sending the full fetched blob back on every save
    // used to round-trip untouched, already-stringified structured values
    // (e.g. camera calibration inline tables) through `update_config`'s
    // configparser-style `str(value)` writer, silently corrupting them.
    const [configData, setConfigData] = useState<ConfigData>({});
    const [configPatch, setConfigPatch] = useState<ConfigData>({});

    const {
        data: fetchedConfig,
        loading: loadingConfig,
    } = useBackendFetch(
        (signal) => getSystemConfig({ signal, timeoutMs: 10000 }),
        [open, closing],
        { enabled: open && !closing, errorMessage: 'Failed to load configuration' }
    );

    useEffect(() => {
        if (fetchedConfig) {
            setConfigData(fetchedConfig as ConfigData);
            setConfigPatch({});
        }
    }, [fetchedConfig]);

    // Sync Secondary Window
    useEffect(() => {
        const app = (window as any).astrometrics?.app;
        if (app?.onSecondaryWindowClosed) {
            return app.onSecondaryWindowClosed(() => {
                setSecondaryWindowEnabled(false);
            });
        }
    }, []);

    const [reindexingJobId, setReindexingJobId] = useState<string | null>(null);
    const [reindexingStatus, setReindexingStatus] = useState<string | null>(null);
    const [reindexingProgress, setReindexingProgress] = useState<number>(0);
    const [isReindexing, setIsReindexing] = useState(false);

    // Re-index Status Polling
    useEffect(() => {
        if (!reindexingJobId) return;

        let timer: any;
        let errorCount = 0;
        const poll = async () => {
            if (!reindexingJobId) return;
            try {
                const { getIngestionJobStatus } = await import('../../../common/services/ingestionService');
                const status = await getIngestionJobStatus(reindexingJobId);

                // REQ: IMG-5.6 - Prefer message (which contains counts) over raw progress string
                setReindexingStatus(status.message || status.progress);
                if (status.progressCurrent !== undefined) {
                    setReindexingProgress(status.progressCurrent);
                }
                errorCount = 0; // Reset on success

                if (status.status === 'completed') {
                    showToast('Library re-indexed successfully', 'success');
                    setIsReindexing(false);
                    setReindexingJobId(null);
                    getSystemConfig().then(data => setConfigData(data as ConfigData));
                } else if (status.status === 'failed') {
                    showToast('Library re-indexing failed', 'error');
                    setIsReindexing(false);
                    setReindexingJobId(null);
                } else {
                    timer = setTimeout(poll, 1500);
                }
            } catch (err) {
                errorCount++;
                console.error(`Polling error (${errorCount}/3):`, err);
                if (errorCount >= 3) {
                    showToast('Lost connection to re-indexing job', 'error');
                    setIsReindexing(false);
                    setReindexingJobId(null);
                } else {
                    timer = setTimeout(poll, 3000); // Wait longer on error
                }
            }
        };

        poll();
        return () => clearTimeout(timer);
    }, [reindexingJobId]);


    // Handlers
    const handleConfigChange = (section: string, key: string, value: string) => {
        setConfigData(prev => ({
            ...prev,
            [section]: {
                ...prev[section],
                [key]: value
            }
        }));
        setConfigPatch(prev => ({
            ...prev,
            [section]: {
                ...prev[section],
                [key]: value
            }
        }));
    };

    const handleReindex = async () => {
        if (isReindexing) return;

        try {
            const { startReindex } = await import('../../../common/services/ingestionService');
            setIsReindexing(true);
            setReindexingStatus('Starting re-index...');
            const jobId = await startReindex();
            setReindexingJobId(jobId);
        } catch (err) {
            reportError(err instanceof Error ? err : new Error(String(err)), 'settings');
            setIsReindexing(false);
        }
    };

    const handleToggleSecondaryWindow = (enabled: boolean) => {
        setSecondaryWindowEnabled(enabled);
        const app = (window as any).astrometrics?.app;
        if (app?.toggleSecondaryWindow) {
            const preferredMode = configData['Frontend']?.['secondary_window_mode'] || 'Image Processing';
            app.toggleSecondaryWindow(enabled, preferredMode);
        } else {
            reportError(new Error('Multi-window not supported in this environment'), 'settings');
        }
    };

    const handleSetControlMode = async (enterController: boolean) => {
        if (isChangingControlMode) return;
        setIsChangingControlMode(true);
        try {
            const outcome = enterController
                ? await enterControllerMode()
                : await enterMonitoringMode();

            const rejectedCapabilities = Object.keys(outcome.rejected);
            if (rejectedCapabilities.length > 0) {
                showToast(
                    `Controller mode partially applied -- not yet eligible: ${rejectedCapabilities.join(', ')}`,
                    'error'
                );
            } else {
                showToast(
                    enterController ? 'Controller mode enabled' : 'Monitoring mode enabled',
                    'success'
                );
            }
            // Reflects the requested mode even on partial rejection: the
            // capabilities that did succeed already left DELEGATED, so
            // "monitoring mode" is no longer strictly true either.
            setControllerModeEnabled(enterController);
        } catch (err) {
            reportError(err instanceof Error ? err : new Error(String(err)), 'settings');
        } finally {
            setIsChangingControlMode(false);
        }
    };

    const handleRevertBackend = () => {
        setConfigPatch({});
        getSystemConfig({ timeoutMs: 10000 })
            .then((data) => {
                setConfigData(data as ConfigData);
                showToast('Configuration reverted', 'success');
                window.dispatchEvent(new CustomEvent('astrometrics:configChange'));
            })
            .catch((err) => {
                reportError(err instanceof Error ? err : new Error(String(err)), 'settings');
            });
    };

    const handleSaveBackend = () => {
        if (Object.keys(configPatch).length === 0) {
            onClose();
            return;
        }

        saveSystemConfig(configPatch).then(success => {
            if (success) {
                setConfigPatch({});
                showToast('Configuration saved', 'success');
                window.dispatchEvent(new CustomEvent('astrometrics:configChange'));
                onClose();
            } else {
                showToast('Failed to save configuration', 'error');
            }
        });
    };

    return {
        activeConfigTab, setActiveConfigTab,
        secondaryWindowEnabled, handleToggleSecondaryWindow,
        controllerModeEnabled, isChangingControlMode, handleSetControlMode,
        configData, loadingConfig,
        handleConfigChange,
        handleSaveBackend,
        handleRevertBackend,
        isReindexing, reindexingStatus, reindexingProgress, handleReindex,
    };
};
