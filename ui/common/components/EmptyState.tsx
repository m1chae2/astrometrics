import React from 'react';
import { Skeleton } from './Skeleton';
import '../styles/emptyState.css';

export interface EmptyStateProps {
    /** Selects the icon/tone shown alongside the message. Defaults to 'empty'. */
    variant?: 'empty' | 'loading' | 'error';
    message: React.ReactNode;
    className?: string;
}

/**
 * Shared message for list-shaped panels: "empty" and "error" show text; "loading" shows the
 * shared list Skeleton, with `message` as its hidden label. Loading and empty stay distinct.
 */
export const EmptyState: React.FC<EmptyStateProps> = ({ variant = 'empty', message, className = '' }) => {
    if (variant === 'loading') {
        return <Skeleton variant="list" label={typeof message === 'string' ? message : 'Loading'} className={className} />;
    }
    return (
        <div
            className={`empty-state empty-state--${variant} ${className}`}
            role={variant === 'error' ? 'alert' : undefined}
        >
            <span className="empty-state__text">{message}</span>
        </div>
    );
};
