import { TextInput } from '../../../common/components/TextInput';
import { ConfigData } from '../utils/configUtils';

interface ProcessingFormProps {
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
}

const ONLINE_SOLVER_SECTION = 'Processing.Astrometry.Online Solver';
const SIRIL_SECTION = 'Processing.Siril';

export const ProcessingForm: React.FC<ProcessingFormProps> = ({
    configData,
    handleConfigChange,
    loadingConfig = false,
}) => {
    return (
        <div className="settings__form">
            <h3>Processing</h3>
            <label className="settings__field">
                <span className="settings__label">astrometry.net API Key</span>
                <TextInput
                    type="password"
                    value={String(configData[ONLINE_SOLVER_SECTION]?.['api_key'] ?? '')}
                    onChange={(e) => handleConfigChange(ONLINE_SOLVER_SECTION, 'api_key', e.target.value)}
                    disabled={loadingConfig}
                    spellCheck={false}
                />
                <div className="settings__help">
                    From https://nova.astrometry.net/api_help. Plate solving needs
                    this or a local solver installed separately.
                </div>
            </label>

            <label className="settings__field">
                <span className="settings__label">Siril Executable</span>
                <TextInput
                    value={String(configData[SIRIL_SECTION]?.['siril_executable'] ?? '')}
                    onChange={(e) => handleConfigChange(SIRIL_SECTION, 'siril_executable', e.target.value)}
                    disabled={loadingConfig}
                    spellCheck={false}
                />
                <div className="settings__help">
                    The Siril CLI command, e.g. "siril-cli" for an apt install.
                </div>
            </label>
        </div>
    );
};
