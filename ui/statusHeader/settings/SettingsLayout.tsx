import React, { useRef, useEffect } from 'react';
import { SystemForm } from './components/SystemForm';
import { ConfigForm } from './components/ConfigForm';
import { useSettingsLogic } from './hooks/useSettingsLogic';

type LogicResult = ReturnType<typeof useSettingsLogic>;

interface SettingsLayoutProps extends LogicResult {
    open: boolean; // needed for focus effect dependency if re-opening? Main wrapper handles mount.
    closing: boolean;
    onClose: () => void;
}

export const SettingsLayout: React.FC<SettingsLayoutProps> = ({
    activeConfigTab, setActiveConfigTab,
    validationError,
    backendInput, setBackendInput,
    secondaryWindowEnabled, handleToggleSecondaryWindow,
    configData, loadingConfig,
    groupedConfig,
    handleConfigChange,
    handleSaveBackend,
    handleRevertBackend,
    agentShortcut, setAgentShortcutInput,
    isReindexing, reindexingStatus, reindexingProgress, handleReindex,
    closing,
    onClose
}) => {
    const closeBtnRef = useRef<HTMLButtonElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);

    // Focus close button on mount
    useEffect(() => {
        const t = setTimeout(() => closeBtnRef.current?.focus(), 50);
        return () => clearTimeout(t);
    }, []);

    // Scroll reset on tab change
    useEffect(() => {
        if (contentRef.current) {
            requestAnimationFrame(() => {
                if (contentRef.current) contentRef.current.scrollTop = 0;
            });
        }
    }, [activeConfigTab]);

    return (
        <div
            className="overlay"
            role="dialog"
            aria-modal="true"
            onClick={onClose}
        >
            <div className="overlay__backdrop" aria-hidden="true" />
            <div
                className={`settings ${closing ? 'closing' : 'opening'}`}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="settings__header">
                    <h2 className="settings__title">System Configuration</h2>
                    <button
                        ref={closeBtnRef}
                        className="settings__close-button"
                        onClick={onClose}
                        aria-label="Close settings"
                        type="button"
                    >
                        <svg viewBox="0 0 24 24" width="20" height="20">
                            <path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                        </svg>
                    </button>
                </div>

                {/* Sub-tabs for Config Sections */}
                {!loadingConfig && (
                    <div className="settings__tabs settings__tabs--sub">
                        <button
                            className={`settings__tab-button ${activeConfigTab === 'System' ? 'settings__tab-button--active' : ''}`}
                            onClick={() => setActiveConfigTab('System')}
                        >
                            System
                        </button>
                        {groupedConfig.filter(g => g.name !== 'System').map(group => (
                            <button
                                key={group.name}
                                className={`settings__tab-button ${activeConfigTab === group.name ? 'settings__tab-button--active' : ''}`}
                                onClick={() => setActiveConfigTab(group.name)}
                            >
                                {group.name}
                            </button>
                        ))}
                    </div>
                )}

                <div
                    ref={contentRef}
                    className="settings__content"
                >
                    {activeConfigTab === 'System' && (
                        <SystemForm
                            backendInput={backendInput}
                            setBackendInput={setBackendInput}
                            validationError={validationError}
                            secondaryWindowEnabled={secondaryWindowEnabled}
                            handleToggleSecondaryWindow={handleToggleSecondaryWindow}
                            configData={configData}
                            handleConfigChange={handleConfigChange}
                            agentShortcut={agentShortcut}
                            setAgentShortcutInput={setAgentShortcutInput}
                        />
                    )}

                    {loadingConfig ? (
                        <div className="settings__loading">Loading configuration...</div>
                    ) : (
                        <>
                            {groupedConfig.filter(g => g.name !== 'System').map(group => {
                                if (group.name !== activeConfigTab) return null;
                                return (
                                    <ConfigForm
                                        key={group.name}
                                        group={group}
                                        onConfigChange={handleConfigChange}
                                        isReindexing={isReindexing}
                                        reindexingStatus={reindexingStatus}
                                        reindexingProgress={reindexingProgress}
                                        onReindex={handleReindex}
                                    />
                                );
                            })}
                        </>
                    )}

                    <div className="settings__footer">
                        <button
                            className="btn"
                            onClick={handleRevertBackend}
                            type="button"
                        >
                            Revert
                        </button>
                        <button
                            className="btn btn--primary"
                            onClick={handleSaveBackend}
                            type="button"
                        >
                            Save
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};
