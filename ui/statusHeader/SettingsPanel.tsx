import React from 'react';
import { useSettingsLogic } from './settings/hooks/useSettingsLogic';
import { SettingsLayout } from './settings/SettingsLayout';
import './statusHeader.css';

export interface SettingsPanelProps {
    open: boolean;
    closing: boolean;
    onClose: () => void;
}

/**
 * Modal panel for application settings and terminal.
 */
export const SettingsPanel: React.FC<SettingsPanelProps> = ({ open, closing, onClose }) => {
    const logic = useSettingsLogic(open, closing, onClose);

    if (!open && !closing) return null;

    return (
        <SettingsLayout
            open={open}
            closing={closing}
            onClose={onClose}
            {...logic}
        />
    );
};
