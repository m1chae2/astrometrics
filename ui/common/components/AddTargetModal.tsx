import React, { useState } from 'react';
import { validateRightAscension, validateDeclination } from '../utils/coordinateValidation';
import { useToast } from '../hooks/useToast';
import '../styles/addTargetModal.css';

interface AddTargetModalProps {
    isOpen: boolean;
    onClose: () => void;
    onAdd: (name: string, ra: string, dec: string) => Promise<void>;
}

/**
 * A premium modal for adding new targets to the Astrometrics library.
 * Features glassmorphism and smooth transitions.
 */
export const AddTargetModal: React.FC<AddTargetModalProps> = ({ isOpen, onClose, onAdd }) => {
    const [name, setName] = useState('');
    const [ra, setRa] = useState('');
    const [dec, setDec] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const toast = useToast();

    if (!isOpen) return null;

    const handleSubmit = async (e: React.FormEvent) => {
        // Without this the form navigates on submit, reloading the app before
        // any validation message below can be shown.
        e.preventDefault();

        if (!name.trim()) {
            toast.show('Target Name is required', 'error');
            return;
        }

        // Coordinates are optional here; when supplied they must be in range
        // (RA 0-24h, Dec -90 to +90) and in a form the backend can parse.
        const coordinateError = validateRightAscension(ra) ?? validateDeclination(dec);
        if (coordinateError) {
            toast.show(coordinateError, 'error');
            return;
        }

        setIsSubmitting(true);
        try {
            await onAdd(name.trim(), ra.trim(), dec.trim());
            setName('');
            setRa('');
            setDec('');
            onClose();
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Failed to add target';
            toast.show(message, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="add-target-modal panel" onClick={(e) => e.stopPropagation()}>
                <div className="panel__header">
                    <h3>Add New Target</h3>
                    <button className="close-btn" onClick={onClose}>&times;</button>
                </div>
                <form onSubmit={handleSubmit} className="modal-form">
                    <div className="form-group">
                        <label htmlFor="target-name">Target Name *</label>
                        <input
                            id="target-name"
                            className="entry entry--fill"
                            type="text"
                            placeholder="e.g. M42"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            autoFocus
                        />
                    </div>
                    <div className="form-row">
                        <div className="form-group">
                            <label htmlFor="target-ra">RA (Optional)</label>
                            <input
                                id="target-ra"
                                className="entry entry--fill"
                                type="text"
                                placeholder="05h 35m 17s"
                                value={ra}
                                onChange={(e) => setRa(e.target.value)}
                            />
                        </div>
                        <div className="form-group">
                            <label htmlFor="target-dec">DEC (Optional)</label>
                            <input
                                id="target-dec"
                                className="entry entry--fill"
                                type="text"
                                placeholder="-05d 23m 28s"
                                value={dec}
                                onChange={(e) => setDec(e.target.value)}
                            />
                        </div>
                    </div>
                    <div className="modal-actions">
                        <button type="button" className="btn btn--cancel" onClick={onClose} disabled={isSubmitting}>
                            Cancel
                        </button>
                        <button type="submit" className="btn btn--primary" disabled={isSubmitting}>
                            {isSubmitting ? 'Adding...' : 'Add Target'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};
