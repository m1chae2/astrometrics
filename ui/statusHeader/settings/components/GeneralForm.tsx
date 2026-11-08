import { Button } from '../../../common/components/Button';
import { Checkbox } from '../../../common/components/Checkbox';
import { TextInput } from '../../../common/components/TextInput';
import { PopoverSelect } from '../../../common/components/PopoverSelect';
import { ConfigData } from '../utils/configUtils';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from '../../../common/constants/displayFlags';

interface GeneralFormProps {
    secondaryWindowEnabled: boolean;
    handleToggleSecondaryWindow: (enabled: boolean) => void;
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
    isReindexing: boolean;
    reindexingStatus: string | null;
    reindexingProgress: number;
    onReindex: () => void;
}

const IMAGE_LIBRARY_SECTION = 'Image Library';

export const GeneralForm: React.FC<GeneralFormProps> = ({
    secondaryWindowEnabled,
    handleToggleSecondaryWindow,
    configData,
    handleConfigChange,
    loadingConfig = false,
    isReindexing,
    reindexingStatus,
    reindexingProgress,
    onReindex,
}) => {
    return (
        <div className="settings__form">
            <h3>General</h3>

            <h4>Secondary Window</h4>
            <Checkbox
                className="settings__field settings__field--row"
                label="Enable Secondary Window"
                checked={secondaryWindowEnabled} onChange={(e) => handleToggleSecondaryWindow(e.target.checked)}
            />
            {secondaryWindowEnabled && (
                <div className="settings__field settings__field--spaced">
                    <span className="settings__label">Secondary Window Display</span>
                    <PopoverSelect
                        label="Secondary Window Default Display"
                        options={DISPLAY_DEFINITIONS
                            .filter(({ mode }) => isDisplayEnabled(configData, mode))
                            .map(({ mode }) => ({ id: mode, label: mode }))}
                        selectedId={String(configData['Frontend']?.['secondary_window_mode'] || 'Image Processing')}
                        onSelect={(id) => handleConfigChange('Frontend', 'secondary_window_mode', id ?? '')}
                    />
                </div>
            )}

            <div className="settings__divider">
                <h4>Displays</h4>
                {DISPLAY_DEFINITIONS.map(({ mode, flag }) => (
                    <Checkbox
                        key={flag}
                        className="settings__field settings__field--row"
                        label={mode}
                        checked={configData['Frontend']?.[flag] !== 'false'}
                        onChange={(e) => handleConfigChange('Frontend', flag, e.target.checked ? 'true' : 'false')}
                        disabled={loadingConfig}
                    />
                ))}
            </div>

            <div className="settings__divider">
                <h4>Image Library</h4>
                <label className="settings__field">
                    <span className="settings__label">Library Path</span>
                    <TextInput
                        value={String(configData[IMAGE_LIBRARY_SECTION]?.['path'] ?? '')}
                        onChange={(e) => handleConfigChange(IMAGE_LIBRARY_SECTION, 'path', e.target.value)}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        Absolute path to your image library. Captured frames live in a
                        "frames" subfolder underneath this path.
                    </div>
                </label>

                <div className="settings__action-section">
                    <h4 className="settings__subgroup-title">Maintenance</h4>
                    <div className="settings__action-row">
                        <Button
                            fit
                            onClick={onReindex}
                            disabled={isReindexing}
                        >
                            {isReindexing ? 'Reindexing...' : 'Reindex Library'}
                        </Button>
                        {isReindexing && (
                            <div className="settings__progress-info">
                                <span className="settings__progress-text">{reindexingStatus}</span>
                                <div className="settings__progress-bar">
                                    <div
                                        className={`settings__progress-fill ${!reindexingProgress ? 'settings__progress-fill--animate' : ''}`}
                                        style={{ width: `${reindexingProgress || 0}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                    <p className="settings__help-text">
                        Scans all local folders (lights, darks, flats, biases) and synchronizes the index files.
                    </p>
                </div>
            </div>
        </div>
    );
};
