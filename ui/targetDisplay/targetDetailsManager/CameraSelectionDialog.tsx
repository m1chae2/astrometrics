import React from 'react';
import './CameraSelectionDialog.css';

export interface CameraSelectionDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: () => void;
    availableCameras: string[];
    selectedCamera: string;
    onSelectCamera: (cam: string) => void;
}

/**
 * Modal dialog for selecting a camera to associate with new images.
 */
export const CameraSelectionDialog: React.FC<CameraSelectionDialogProps> = ({
    isOpen,
    onClose,
    onConfirm,
    availableCameras,
    selectedCamera,
    onSelectCamera
}) => {
    if (!isOpen) return null;

    return (
        <div className="camera-dialog-overlay">
            <div className="camera-dialog">
                <h3 className="camera-dialog__title">Select Camera</h3>
                <div className="camera-dialog__field">
                    <label className="camera-dialog__label">Camera Model:</label>
                    <select
                        value={selectedCamera}
                        onChange={(e) => onSelectCamera(e.target.value)}
                    >
                        {availableCameras.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                </div>
                <div className="camera-dialog__actions">
                    <button className="btn" onClick={onClose}>Cancel</button>
                    <button className="btn" onClick={onConfirm}>Select Files...</button>
                </div>
            </div>
        </div>
    );
};
