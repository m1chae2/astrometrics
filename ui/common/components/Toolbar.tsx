/**
 * @module Toolbar
 * @fileoverview Small layout pieces for a compact toolbar: a row, a muted
 * label before a control, a plain value, and a thin divider between groups.
 *
 * They give every display's toolbar the same text size, gaps and dividers,
 * matching the Image Processing FITS Viewer bar ("Display  Linear ▼"). Put
 * compact controls in it: `Button size="small"`, `PopoverSelect
 * size="compact"` (or `SessionPicker size="compact"`) and `Checkbox
 * size="compact"`.
 */

import React from 'react';
import './toolbar.css';

/** Props for Toolbar. */
export interface ToolbarProps {
  /** The toolbar's contents. */
  children: React.ReactNode;
  /** A layout-only class, for placing the toolbar on its screen. */
  className?: string;
  /** Name of the toolbar for screen readers. */
  ariaLabel?: string;
}

/**
 * A row of compact controls that wraps onto more lines when it runs out of room.
 *
 * @param {ToolbarProps} props The contents and an optional layout class.
 * @returns {React.ReactElement} The toolbar row.
 */
export const Toolbar: React.FC<ToolbarProps> = ({ children, className, ariaLabel }) => (
  <div className={`toolbar${className ? ` ${className}` : ''}`} role="toolbar" aria-label={ariaLabel}>
    {children}
  </div>
);

/** Props for ToolbarGroup. */
export interface ToolbarGroupProps {
  /** The controls that belong together. */
  children: React.ReactNode;
}

/**
 * Keeps related controls together, such as a label and its control.
 *
 * @param {ToolbarGroupProps} props The grouped controls.
 * @returns {React.ReactElement} The group.
 */
export const ToolbarGroup: React.FC<ToolbarGroupProps> = ({ children }) => (
  <div className="toolbar__group">{children}</div>
);

/** Props for ToolbarLabel. */
export interface ToolbarLabelProps {
  /** The label text, such as "Session". */
  children: React.ReactNode;
}

/**
 * The small muted word before a control or value, such as "Session".
 *
 * Hidden from screen readers: the control after it names itself.
 *
 * @param {ToolbarLabelProps} props The label text.
 * @returns {React.ReactElement} The label.
 */
export const ToolbarLabel: React.FC<ToolbarLabelProps> = ({ children }) => (
  <span className="toolbar__label" aria-hidden="true">{children}</span>
);

/** Props for ToolbarValue. */
export interface ToolbarValueProps {
  /** The value text, such as "135.0°". */
  children: React.ReactNode;
  /** Text read by screen readers instead of the label and value, such as "Field of view 135.0 degrees". */
  ariaLabel?: string;
  /** Hover text. */
  title?: string;
}

/**
 * A plain value shown after a label, in the toolbar's text size.
 *
 * @param {ToolbarValueProps} props The value text.
 * @returns {React.ReactElement} The value.
 */
export const ToolbarValue: React.FC<ToolbarValueProps> = ({ children, ariaLabel, title }) => (
  <span className="toolbar__value" aria-label={ariaLabel} title={title}>{children}</span>
);

/**
 * A thin upright line between groups of controls.
 *
 * @returns {React.ReactElement} The divider.
 */
export const ToolbarDivider: React.FC = () => (
  <span className="toolbar__divider" role="separator" aria-orientation="vertical" />
);
