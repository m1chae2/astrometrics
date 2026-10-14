/**
 * @file configUtils.ts
 * @description Shared types for the backend's section/key config dictionary,
 * as edited by the curated Settings tab components.
 */
export interface ConfigSection {
    [key: string]: string | number | boolean;
}

export interface ConfigData {
    [section: string]: ConfigSection;
}
