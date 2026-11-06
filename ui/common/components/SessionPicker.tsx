/**
 * Description: A picker for choosing which observing session a view shows.
 * It is the shared PopoverSelect with a first "live" choice (no session picked, so the view
 * shows what is happening now). Below it come the past sessions the caller lists.
 * The Planetarium toolbar and the Observatory Manager both use it.
 */

import React from 'react';
import { PopoverSelect, PopoverSelectOption } from './PopoverSelect';

/** One past session the picker can offer. */
export type SessionPickerOption = PopoverSelectOption;

export interface SessionPickerProps {
    /** The name of the picker for screen readers and the tooltip, such as "Night". */
    label: string;
    /** The past sessions to offer, in the order to show them. */
    options: SessionPickerOption[];
    /** The id of the picked session, or null for the live choice. */
    selectedId: string | null;
    /** Called with the picked id, or null when the live choice is picked. */
    onSelect: (id: string | null) => void;
    /** The text of the live choice. Defaults to "Live". */
    liveLabel?: string;
    /** Extra class names for the outer element, for layout. */
    className?: string;
}

/**
 * Shows the session picker: a PopoverSelect whose empty choice is the live one.
 *
 * @param props See {@link SessionPickerProps}.
 * @returns The picker.
 */
export const SessionPicker: React.FC<SessionPickerProps> = ({ liveLabel = 'Live', ...rest }) => (
    <PopoverSelect {...rest} emptyLabel={liveLabel} />
);
