import React from 'react';
import '../styles/listActions.css';

export interface ListActionsProps {
    children: React.ReactNode;
}

/**
 * Common container for list action buttons.
 * Ensures consistent styling and layout across all displays.
 */
export const ListActions: React.FC<ListActionsProps> = ({ children }) => {
    return <div className="list-actions">{children}</div>;
};
