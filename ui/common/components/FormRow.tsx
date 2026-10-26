/**
 * @fileoverview FormRow: a label, an input slot, an optional unit and an inline error message.
 */

import React from 'react';
import './sharedComponents.css';

export interface FormRowProps {
    /** Text of the label. */
    label: string;
    /** Id of the input in `children`. The label points to it. */
    htmlFor: string;
    /** Optional unit shown after the input, for example "s" or "deg". */
    unit?: string;
    /** Error text. When set it is shown under the input as an alert. */
    error?: string | null;
    /** The input, select or other control. Give it `id={htmlFor}`. */
    children: React.ReactNode;
}

/**
 * Builds the id of a FormRow's error message. Put it in the input's `aria-describedby`.
 * @param htmlFor The row's `htmlFor` value.
 * @return The error element id.
 */
export function formRowErrorId(htmlFor: string): string {
    return `${htmlFor}-error`;
}

/**
 * Shows one labelled form row.
 *
 * The error appears with `role="alert"` so a screen reader announces it. The input should
 * list {@link formRowErrorId} in `aria-describedby` and set `aria-invalid` while there is an error.
 *
 * @param props See {@link FormRowProps}.
 * @return The row element.
 */
export const FormRow: React.FC<FormRowProps> = ({ label, htmlFor, unit, error, children }) => (
    <div className="form-row">
        <div className="form-row__line">
            <label className="form-row__label" htmlFor={htmlFor}>{label}</label>
            {children}
            {unit && <span className="form-row__unit">{unit}</span>}
        </div>
        {error && (
            <p className="form-row__error" role="alert" id={formRowErrorId(htmlFor)}>{error}</p>
        )}
    </div>
);
