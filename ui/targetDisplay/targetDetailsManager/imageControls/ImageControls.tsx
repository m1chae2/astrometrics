import React from 'react';
import './imageControls.css';

export interface ImageControlsProps {
  onAddImage?: () => void;
}

export const ImageControls: React.FC<ImageControlsProps> = ({ onAddImage }) => (
  <div className="image-controls">
    <button className="btn" onClick={onAddImage}>Add Processed Image</button>
  </div>
);
