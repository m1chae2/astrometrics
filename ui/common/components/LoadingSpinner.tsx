import React from 'react';
import '../styles/animations.css';

interface LoadingSpinnerProps {
    size?: number;
    color?: string;
    className?: string;
    style?: React.CSSProperties;
}

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
    size = 18,
    color = '#4fc1ff',
    className = '',
    style
}) => {
    const spinnerStyle: React.CSSProperties = {
        display: 'inline-block',
        width: `${size}px`,
        height: `${size}px`,
        border: `2px solid ${color}4d`, // 30% opacity using hex alpha approximation (0.3 * 255 ~= 4d)
        borderTop: `2px solid ${color}`,
        borderRadius: '50%',
        animation: 'spinner-spin 1s linear infinite',
        verticalAlign: 'middle',
        ...style
    };

    return <div className={`loading-spinner ${className}`} style={spinnerStyle} />;
};
