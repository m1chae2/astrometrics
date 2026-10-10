import { useState } from 'react';
import { ConfigData } from '../utils/configUtils';

export interface CollectionFieldSchema {
    key: string;
    label: string;
    type?: 'text' | 'select';
    /** For type 'select': the option list, or a function computing it from the current configData (for cross-references, e.g. a Setup's camera/optic pickers). */
    options?: string[] | ((configData: ConfigData) => string[]);
    /** When adding a new item, seed this field with the item's own name (e.g. a Camera's redundant `name` field). */
    seedFromName?: boolean;
}

interface SettingsCollectionEditorProps {
    /** Section holding the comma-separated list of item names, e.g. "Observatory.Optics". */
    availableSection: string;
    /** Key within `availableSection` holding the comma-separated list, e.g. "available". */
    availableKey: string;
    /** Prefix + item name = that item's own section, e.g. "Observatory.Optic." + "Apertura 75Q". */
    itemSectionPrefix: string;
    fields: CollectionFieldSchema[];
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
    addPlaceholder?: string;
    /** Optional extra read-only content rendered under each item's editable fields (e.g. Camera calibration data). */
    renderExtra?: (itemName: string, itemSection: string) => React.ReactNode;
}

export function parseList(raw: unknown): string[] {
    return String(raw ?? '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
}

/**
 * Shared add/remove/edit list editor for scalar-field equipment records
 * (Optics, Setups, Cameras) that all share the same "comma-list of names +
 * one section per name" TOML shape. Configured once per field schema
 * instead of writing a bespoke editor for each collection.
 */
export const SettingsCollectionEditor: React.FC<SettingsCollectionEditorProps> = ({
    availableSection,
    availableKey,
    itemSectionPrefix,
    fields,
    configData,
    handleConfigChange,
    loadingConfig = false,
    addPlaceholder = 'New item name',
    renderExtra,
}) => {
    const [newName, setNewName] = useState('');
    const itemNames = parseList(configData[availableSection]?.[availableKey]);

    const setAvailable = (names: string[]) => {
        handleConfigChange(availableSection, availableKey, names.join(', '));
    };

    const handleRemove = (name: string) => {
        setAvailable(itemNames.filter((n) => n !== name));
    };

    const handleAdd = () => {
        const trimmed = newName.trim();
        if (!trimmed || itemNames.includes(trimmed)) return;
        setAvailable([...itemNames, trimmed]);
        for (const field of fields) {
            if (field.seedFromName) {
                handleConfigChange(`${itemSectionPrefix}${trimmed}`, field.key, trimmed);
            }
        }
        setNewName('');
    };

    const resolveOptions = (field: CollectionFieldSchema): string[] =>
        typeof field.options === 'function' ? field.options(configData) : field.options ?? [];

    return (
        <div className="settings__collection">
            {itemNames.length === 0 && (
                <div className="settings__help" style={{ marginLeft: 0 }}>None configured yet.</div>
            )}
            {itemNames.map((name) => {
                const section = `${itemSectionPrefix}${name}`;
                return (
                    <div className="settings__collection-row" key={name}>
                        <div className="settings__collection-row__header">
                            <span className="settings__collection-row__title">{name}</span>
                            <button
                                type="button"
                                className="btn btn--secondary settings__collection-row__remove"
                                onClick={() => handleRemove(name)}
                                disabled={loadingConfig}
                            >
                                Remove
                            </button>
                        </div>
                        <div className="settings__grid">
                            {fields.map((field) => (
                                <label key={field.key} className="settings__field">
                                    <span className="settings__label">{field.label}</span>
                                    {field.type === 'select' ? (
                                        <select
                                            className="settings__input"
                                            value={String(configData[section]?.[field.key] ?? '')}
                                            onChange={(e) => handleConfigChange(section, field.key, e.target.value)}
                                            disabled={loadingConfig}
                                        >
                                            <option value="" disabled>Select...</option>
                                            {resolveOptions(field).map((opt) => (
                                                <option key={opt} value={opt}>{opt}</option>
                                            ))}
                                        </select>
                                    ) : (
                                        <input
                                            className="settings__input"
                                            type="text"
                                            value={String(configData[section]?.[field.key] ?? '')}
                                            onChange={(e) => handleConfigChange(section, field.key, e.target.value)}
                                            disabled={loadingConfig}
                                        />
                                    )}
                                </label>
                            ))}
                        </div>
                        {renderExtra?.(name, section)}
                    </div>
                );
            })}

            <div className="settings__collection-add">
                <input
                    className="settings__input"
                    type="text"
                    placeholder={addPlaceholder}
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    disabled={loadingConfig}
                />
                <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={handleAdd}
                    disabled={loadingConfig || !newName.trim()}
                >
                    Add
                </button>
            </div>
        </div>
    );
};
