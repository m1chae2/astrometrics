import React from 'react';
import { GroupedConfig, getLabel } from '../utils/configUtils';

interface ConfigFormProps {
    group: GroupedConfig;
    onConfigChange: (section: string, key: string, value: string) => void;
    isReindexing?: boolean;
    reindexingStatus?: string | null;
    reindexingProgress?: number;
    onReindex?: () => void;
}

const ConfigGroupRenderer: React.FC<{
    group: GroupedConfig;
    depth: number;
    onConfigChange: (section: string, key: string, value: string) => void;
}> = ({ group, depth, onConfigChange }) => {
    return (
        <div key={group.sectionName} className={`settings__group ${depth > 0 ? 'settings__group--nested' : ''}`}>
            {depth > 0 && (
                <h4 className="settings__subgroup-title">
                    {getLabel(group.name)}
                </h4>
            )}

            {/* Fields */}
            {group.fields.length > 0 && (
                <div className="settings__grid">
                    {group.fields.map((field) => {
                        const isBoolean = field.value.toLowerCase() === 'true' || field.value.toLowerCase() === 'false';

                        return (
                            <label key={`${group.sectionName}-${field.key}`} className={`settings__field ${isBoolean ? 'settings__field--checkbox' : ''}`}>
                                {isBoolean ? (
                                    <>
                                        <input
                                            type="checkbox"
                                            checked={field.value.toLowerCase() === 'true'}
                                            onChange={(e) => onConfigChange(group.sectionName, field.key, e.target.checked ? 'true' : 'false')}
                                            className="settings__checkbox"
                                        />
                                        <span className="settings__label" title={field.key}>{field.label}</span>
                                    </>
                                ) : (
                                    <>
                                        <span className="settings__label" title={field.key}>{field.label}</span>
                                        <input
                                            className="entry"
                                            type={field.key.includes('api_key') ? 'password' : 'text'}
                                            value={field.value}
                                            onChange={(e) => onConfigChange(group.sectionName, field.key, e.target.value)}
                                            spellCheck={false}
                                        />
                                    </>
                                )}
                            </label>
                        );
                    })}
                </div>
            )}

            {/* Sub-groups */}
            {group.subGroups.map(sub => (
                <ConfigGroupRenderer
                    key={sub.sectionName}
                    group={sub}
                    depth={depth + 1}
                    onConfigChange={onConfigChange}
                />
            ))}
        </div>
    );
};

export const ConfigForm: React.FC<ConfigFormProps> = ({
    group, onConfigChange,
    isReindexing, reindexingStatus, reindexingProgress, onReindex
}) => {
    return (
        <div className="settings__dynamic-form">
            <ConfigGroupRenderer group={group} depth={0} onConfigChange={onConfigChange} />

            {group.name === 'Image Library' && onReindex && (
                <div className="settings__action-section">
                    <h4 className="settings__subgroup-title">Maintenance</h4>
                    <div className="settings__action-row">
                        <button
                            className={`btn ${isReindexing ? 'btn--disabled' : ''} settings__action-label`}
                            onClick={onReindex}
                            disabled={isReindexing}
                            type="button"
                        >
                            {isReindexing ? 'Reindexing...' : 'Reindex Library'}
                        </button>
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
            )}
        </div>
    );
};
