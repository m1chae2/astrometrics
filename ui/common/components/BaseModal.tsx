import React, { useEffect, useRef } from 'react';
import '../styles/modal.css';

interface BaseModalProps {
    isOpen: boolean;
    onClose: () => void;
    title?: string;
    children: React.ReactNode;
    className?: string;
    footer?: React.ReactNode;
}

/**
 * A generic modal/dialog component that enforces consistent styling.
 *
 * Features:
 * - Centered overlay (no blur by default)
 * - Distinct "menu-like" container
 * - Standard header with close button
 * - Scrollable content area
 */
export const BaseModal: React.FC<BaseModalProps> = ({
    isOpen,
    onClose,
    title,
    children,
    className = '',
    footer
}) => {
    const modalRef = useRef<HTMLDivElement>(null);

    // Close on Escape key
    useEffect(() => {
        const handleEscape = (e: KeyboardEvent) => {
            if (isOpen && e.key === 'Escape') {
                onClose();
            }
        };
        window.addEventListener('keydown', handleEscape);
        return () => window.removeEventListener('keydown', handleEscape);
    }, [isOpen, onClose]);

    // Handle overlay click
    const handleOverlayClick = (e: React.MouseEvent) => {
        if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
            onClose();
        }
    };

    if (!isOpen) return null;

    return (
        <div className="modal-overlay" onClick={handleOverlayClick}>
            <div className={`modal ${className}`} ref={modalRef} role="dialog" aria-modal="true">
                <div className="modal__header">
                    <h2>{title}</h2>
                    <button className="modal__close" onClick={onClose} aria-label="Close">&times;</button>
                </div>
                <div className="modal__content">
                    {children}
                </div>
                {footer && (
                    <div className="modal__footer">
                        {footer}
                    </div>
                )}
            </div>
        </div>
    );
};
