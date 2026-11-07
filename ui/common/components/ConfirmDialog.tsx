import React from 'react';
import '../styles/ConfirmDialog.css';
import { Button } from './Button';

export interface Props {
  open: boolean;
  title?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Minimal, dependency-free confirm dialog.
 * Uses standard BEM button classes and styled via `ConfirmDialog.css`.
 */
export const ConfirmDialog: React.FC<Props> = ({
  open,
  title = 'Confirm',
  message = 'Are you sure?',
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}) => {
  if (!open) return null;

  return (
    <div className="confirm-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="confirm-backdrop" aria-hidden="true" />
      <div className="confirm-dialog">
        <h3>{title}</h3>
        <div className="confirm-body">{message}</div>
        <div className="confirm-actions">
          <Button onClick={onCancel}>{cancelLabel}</Button>
          <Button variant="danger" onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
};
