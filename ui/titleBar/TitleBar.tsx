import React, { useEffect, useState } from 'react';
import appIcon from '../../assets/orbit-smooth-64.png';
import './titleBar.css';

/**
 * Custom in-app title bar for frameless windows (see
 * BasePlatform#getWindowOptions on the Electron side), matching the look of
 * apps like VS Code: an app icon and drag region in place of the native
 * title bar, with our own minimize/maximize/close buttons.
 *
 * Renders nothing when the current window has native decorations (i.e.
 * `window.astrometrics.window.hasCustomTitleBar` is false), so this is safe
 * to always mount.
 */
export const TitleBar: React.FC = () => {
    const [isMaximized, setIsMaximized] = useState(false);
    const [isFocused, setIsFocused] = useState(true);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const windowApi = window.astrometrics?.window;

    useEffect(() => {
        if (!windowApi) return;
        windowApi.isMaximized().then(setIsMaximized).catch(() => { /* Ignore */ });
        return windowApi.onMaximizedChange(setIsMaximized);
    }, [windowApi]);

    useEffect(() => {
        if (!windowApi) return;
        windowApi.isFocused().then(setIsFocused).catch(() => { /* Ignore */ });
        return windowApi.onFocusChange(setIsFocused);
    }, [windowApi]);

    useEffect(() => {
        if (!windowApi) return;
        windowApi.isFullscreen().then(setIsFullscreen).catch(() => { /* Ignore */ });
        return windowApi.onFullscreenChange(setIsFullscreen);
    }, [windowApi]);

    // Native title bars vanish in fullscreen (VS Code and GNOME apps both do
    // this); ours should too, rather than floating over the content.
    if (!windowApi?.hasCustomTitleBar || isFullscreen) return null;

    return (
        <div
            className={`title-bar ${isFocused ? '' : 'title-bar--unfocused'}`}
            onDoubleClick={() => windowApi.toggleMaximize()}
        >
            <div className="title-bar__brand">
                <img className="title-bar__icon" src={appIcon} alt="" />
                <span className="title-bar__title">Astrometrics</span>
            </div>

            <div className="title-bar__controls">
                <button
                    className="title-bar__button"
                    aria-label="Minimize window"
                    title="Minimize window"
                    onClick={() => windowApi.minimize()}
                    type="button"
                >
                    <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
                        <rect x="2" y="5.5" width="8" height="1" />
                    </svg>
                </button>

                <button
                    className="title-bar__button"
                    aria-label={isMaximized ? 'Restore window' : 'Maximize window'}
                    title={isMaximized ? 'Restore window' : 'Maximize window'}
                    onClick={() => windowApi.toggleMaximize()}
                    type="button"
                >
                    {isMaximized ? (
                        <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
                            <rect x="3.5" y="2" width="6.5" height="6.5" fill="none" stroke="currentColor" strokeWidth="1" />
                            <path d="M2 3.5V9.5H8" fill="none" stroke="currentColor" strokeWidth="1" />
                        </svg>
                    ) : (
                        <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
                            <rect x="2" y="2" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1" />
                        </svg>
                    )}
                </button>

                <button
                    className="title-bar__button title-bar__button--close"
                    aria-label="Close window"
                    title="Close window"
                    onClick={() => windowApi.close()}
                    type="button"
                >
                    <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
                        <path d="M2 2L10 10M10 2L2 10" fill="none" stroke="currentColor" strokeWidth="1.2" />
                    </svg>
                </button>
            </div>
        </div>
    );
};
