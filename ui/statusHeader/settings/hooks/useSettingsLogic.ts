import { useState, useEffect, useMemo } from 'react';
import { getBackendBase, setBackendBase } from '../../../common/services/backendApi';
import { getSystemConfig, saveSystemConfig, getAgentShortcut, setAgentShortcut } from '../../../common/services/systemService';
import { useToast } from '../../../common/hooks/useToast';
import { groupConfig, ConfigData } from '../utils/configUtils';

export const useSettingsLogic = (open: boolean, closing: boolean, onClose: () => void) => {
    const { show: showToast } = useToast();

    // UI State
    const [activeConfigTab, setActiveConfigTab] = useState<string>('System');
    const [validationError, setValidationError] = useState<string | null>(null);

    // Form State
    const [backendInput, setBackendInput] = useState<string>(() => {
        try { return getBackendBase(); } catch { return ''; }
    });
    const [secondaryWindowEnabled, setSecondaryWindowEnabled] = useState(false);
    const [agentShortcut, setAgentShortcutInput] = useState<string>(() => {
        try { return getAgentShortcut(); } catch { return 'Ctrl+Space'; }
    });

    // Config Data
    const [configData, setConfigData] = useState<ConfigData>({});
    const [loadingConfig, setLoadingConfig] = useState(false);

    // Fetch Config on Open
    useEffect(() => {
        if (open && !closing) {
            setLoadingConfig(true);
            getSystemConfig().then((data) => {
                setConfigData(data as ConfigData);
                setLoadingConfig(false);
            }).catch(() => setLoadingConfig(false));

            // Re-read backend base incase changed externally?
            // Usually not, but good practice.
            try {
                setBackendInput(getBackendBase());
                setAgentShortcutInput(getAgentShortcut());
            } catch { /* ignore */ }
        }
    }, [open, closing]);

    // Sync Secondary Window
    useEffect(() => {
        const app = (window as any).astrometrics?.app;
        if (app?.onSecondaryWindowClosed) {
            return app.onSecondaryWindowClosed(() => {
                setSecondaryWindowEnabled(false);
            });
        }
    }, []);

    // Group Config
    const groupedConfig = useMemo(() => groupConfig(configData), [configData]);

    // Ensure valid activeConfigTab
    useEffect(() => {
        if (groupedConfig.length > 0 &&
            !groupedConfig.find(g => g.name === activeConfigTab) &&
            activeConfigTab !== 'System') {
            setActiveConfigTab(groupedConfig[0].name);
        }
    }, [groupedConfig, activeConfigTab]);


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
            showToast(err instanceof Error ? err.message : 'Failed to start re-index', 'error');
            setIsReindexing(false);
        }
    };

    const handleToggleSecondaryWindow = (enabled: boolean) => {
        setSecondaryWindowEnabled(enabled);
        const app = (window as any).astrometrics?.app;
        if (app?.toggleSecondaryWindow) {
            app.toggleSecondaryWindow(enabled);
        } else {
            showToast('Multi-window not supported in this environment', 'error');
        }
    };

    const handleRevertBackend = () => {
        try {
            setBackendInput(getBackendBase());
        } catch {
            setBackendInput('');
        }
        getSystemConfig().then((data) => {
            setConfigData(data as ConfigData);
            setLoadingConfig(false);
            showToast('Configuration reverted', 'success');
            window.dispatchEvent(new CustomEvent('astrometrics:configChange'));
        });
    };

    const handleSaveBackend = () => {
        const raw = backendInput && backendInput.trim() ? backendInput.trim() : '';
        setAgentShortcut(agentShortcut); // Persist shortcut

        if (!raw) {
            setBackendBase(null);
            setValidationError(null);
            showToast('Backend override cleared', 'success');
        } else {
            let parsed: URL | null = null;
            try {
                parsed = new URL(raw);
            } catch {
                try {
                    parsed = new URL(`http://${raw}`);
                } catch {
                    parsed = null;
                }
            }

            if (!parsed) {
                setValidationError('Invalid URL or IP');
                return;
            }

            const value = parsed.href.replace(/\/$/, '');
            setBackendBase(value);
            setValidationError(null);
            showToast('Backend URL saved', 'success');
        }

        saveSystemConfig(configData).then(success => {
            if (success) {
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
        validationError,
        backendInput, setBackendInput,
        agentShortcut, setAgentShortcutInput,
        secondaryWindowEnabled, handleToggleSecondaryWindow,
        configData, loadingConfig,
        groupedConfig,
        handleConfigChange,
        handleSaveBackend,
        handleRevertBackend,
        isReindexing, reindexingStatus, reindexingProgress, handleReindex
    };
};
