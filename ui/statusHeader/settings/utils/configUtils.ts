export interface ConfigSection {
    [key: string]: string | number | boolean;
}

export interface ConfigData {
    [section: string]: ConfigSection;
}

export interface GroupedConfig {
    name: string;
    sectionName: string;
    fields: { key: string; value: string; label: string }[];
    subGroups: GroupedConfig[];
}

/**
 * LABEL_MAP: Provides human-readable overrides for configuration keys.
 * REQ: GEN-1.1: Standardized Labels
 */
export const LABEL_MAP: Record<string, string> = {
    // General
    'api_key': 'API Key',
    'auth_token': 'Authentication Token',
    'server_url': 'Server URL',
    'port': 'Port',
    'enabled': 'Enabled',

    // Telescope
    'mount_type': 'Mount Type',
    'focal_length': 'Focal Length',
    'aperture': 'Aperture',
    'driver_name': 'INDI Driver Name',
    'slew_rate': 'Slew Rate',

    // Camera
    'sensor_width': 'Sensor Width (px)',
    'sensor_height': 'Sensor Height (px)',
    'pixel_size': 'Pixel Size (um)',
    'gain': 'Gain',
    'offset': 'Offset',

};

/**
 * Converts a raw key to a human-readable label.
 * Fallback: Snake Case to Title Case.
 */
export const getLabel = (key: string): string => {
    if (LABEL_MAP[key.toLowerCase()]) return LABEL_MAP[key.toLowerCase()];

    // Fallback: convert_this_key -> Convert This Key
    return key
        .split(/[._-]/)
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ');
};

export const groupConfig = (data: ConfigData): GroupedConfig[] => {
    const rootGroups: Record<string, GroupedConfig> = {};
    const sortedKeys = Object.keys(data).sort();

    sortedKeys.forEach(sectionName => {
        const parts = sectionName.split('.');
        const rootName = parts[0];

        if (!rootGroups[rootName]) {
            rootGroups[rootName] = {
                name: rootName,
                sectionName: rootName,
                fields: [],
                subGroups: []
            };
        }

        let currentGroup = rootGroups[rootName];

        for (let i = 1; i < parts.length; i++) {
            const subPart = parts[i];
            const currentPath = parts.slice(0, i + 1).join('.');

            let subGroup = currentGroup.subGroups.find(g => g.name === subPart);
            if (!subGroup) {
                subGroup = {
                    name: subPart,
                    sectionName: currentPath,
                    fields: [],
                    subGroups: []
                };
                currentGroup.subGroups.push(subGroup);
            }
            currentGroup = subGroup;
        }

        const fields = Object.entries(data[sectionName]).map(([k, v]) => ({
            key: k,
            value: String(v),
            label: getLabel(k)
        }));
        currentGroup.fields = fields;
    });

    return Object.values(rootGroups);
};
