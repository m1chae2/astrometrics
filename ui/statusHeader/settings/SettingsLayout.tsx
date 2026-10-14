import React, { useRef, useEffect } from 'react';
import { GeneralForm } from './components/GeneralForm';
import { ObservatoryForm } from './components/ObservatoryForm';
import { EquipmentForm } from './components/EquipmentForm';
import { ProcessingForm } from './components/ProcessingForm';
import { AdvancedForm } from './components/AdvancedForm';
import { useSettingsLogic } from './hooks/useSettingsLogic';

type LogicResult = ReturnType<typeof useSettingsLogic>;

const SETTINGS_TABS = ['General', 'Observatory', 'Equipment', 'Processing', 'Advanced'] as const;

interface SettingsLayoutProps extends LogicResult {
    open: boolean; // needed for focus effect dependency if re-opening? Main wrapper handles mount.
    closing: boolean;
    onClose: () => void;
}

export const SettingsLayout: React.FC<SettingsLayoutProps> = ({
    activeConfigTab, setActiveConfigTab,
    secondaryWindowEnabled, handleToggleSecondaryWindow,
    controllerModeEnabled, isChangingControlMode, handleSetControlMode,
    configData, loadingConfig,
    handleConfigChange,
    handleSaveBackend,
    handleRevertBackend,
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
                        title="Close settings"
                        type="button"
                    >
                        <svg viewBox="0 0 24 24" width="20" height="20">
                            <path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                        </svg>
                    </button>
                </div>

                <div className="settings__body">
                    <div className="settings__sidebar">
                        {SETTINGS_TABS.map((tab) => (
                            <button
                                key={tab}
                                className={`settings__tab-button ${activeConfigTab === tab ? 'settings__tab-button--active' : ''}`}
                                onClick={() => setActiveConfigTab(tab)}
                                type="button"
                            >
                                {tab}
                            </button>
                        ))}
                    </div>

                    <div
                        ref={contentRef}
                        className="settings__content"
                    >
                    {loadingConfig ? (
                        <div className="settings__loading">Loading configuration...</div>
                    ) : (
                        <>
                            {activeConfigTab === 'General' && (
                                <GeneralForm
                                    secondaryWindowEnabled={secondaryWindowEnabled}
                                    handleToggleSecondaryWindow={handleToggleSecondaryWindow}
                                    configData={configData}
                                    handleConfigChange={handleConfigChange}
                                    loadingConfig={loadingConfig}
                                    isReindexing={isReindexing}
                                    reindexingStatus={reindexingStatus}
                                    reindexingProgress={reindexingProgress}
                                    onReindex={handleReindex}
                                />
                            )}
                            {activeConfigTab === 'Observatory' && (
                                <ObservatoryForm
                                    configData={configData}
                                    handleConfigChange={handleConfigChange}
                                    loadingConfig={loadingConfig}
                                    controllerModeEnabled={controllerModeEnabled}
                                    isChangingControlMode={isChangingControlMode}
                                    onSetControlMode={handleSetControlMode}
                                />
                            )}
                            {activeConfigTab === 'Equipment' && (
                                <EquipmentForm
                                    configData={configData}
                                    handleConfigChange={handleConfigChange}
                                    loadingConfig={loadingConfig}
                                />
                            )}
                            {activeConfigTab === 'Processing' && (
                                <ProcessingForm
                                    configData={configData}
                                    handleConfigChange={handleConfigChange}
                                    loadingConfig={loadingConfig}
                                />
                            )}
                            {activeConfigTab === 'Advanced' && (
                                <AdvancedForm
                                    configData={configData}
                                    handleConfigChange={handleConfigChange}
                                    loadingConfig={loadingConfig}
                                />
                            )}
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
        </div>
    );
};
