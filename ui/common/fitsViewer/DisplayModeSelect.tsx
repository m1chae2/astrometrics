/**
 * @file DisplayModeSelect.tsx
 * @description The "Display" control of the FITS viewer: a labeled picker with two choices, Linear and Auto Stretch.
 */
import React from 'react';
import { PopoverSelect } from '../components/PopoverSelect';
import './displayModeSelect.css';
import { DISPLAY_MODE_OPTIONS } from './displayMode';
import type { DisplayMode } from './displayMode';

/** Props for {@link DisplayModeSelect}. */
export interface DisplayModeSelectProps {
  /** The mode now in use. */
  mode: DisplayMode;
  /** Called with the mode the user picks. */
  onChange: (mode: DisplayMode) => void;
  /** True to dim the control, for example while no image is open. */
  disabled?: boolean;
}

/**
 * Shows the Display control for a viewer's header: the word "Display" and a picker of the two modes.
 *
 * @param props See {@link DisplayModeSelectProps}.
 * @returns The control.
 */
export const DisplayModeSelect: React.FC<DisplayModeSelectProps> = ({ mode, onChange, disabled = false }) => (
  <>
    <span className="display-mode-select__label" aria-hidden="true">Display</span>
    <PopoverSelect
      label="Display"
      size="compact"
      options={DISPLAY_MODE_OPTIONS.map((option) => ({ id: option.id, label: option.label, title: option.title }))}
      selectedId={mode}
      disabled={disabled}
      onSelect={(id) => {
        if (id === 'linear' || id === 'auto-stretch') onChange(id);
      }}
    />
  </>
);
