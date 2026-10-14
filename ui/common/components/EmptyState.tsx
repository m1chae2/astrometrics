import React from 'react';
import { LoadingSpinner } from './LoadingSpinner';
import '../styles/emptyState.css';

export interface EmptyStateProps {
    /** Selects the icon/tone shown alongside the message. Defaults to 'empty'. */
    variant?: 'empty' | 'loading' | 'error';
    message: React.ReactNode;
    className?: string;
}

/**
 * Shared "nothing to show yet" message for list-shaped panels, so every
 * view uses the same visual convention for loading vs. truly-empty data
 * instead of going blank or improvising its own text.
 */
export const EmptyState: React.FC<EmptyStateProps> = ({ variant = 'empty', message, className = '' }) => (
    <div
        className={`empty-state empty-state--${variant} ${className}`}
        role={variant === 'loading' ? 'status' : undefined}
    >
        {variant === 'loading' && <LoadingSpinner size={16} />}
        <span className="empty-state__text">{message}</span>
    </div>
);
