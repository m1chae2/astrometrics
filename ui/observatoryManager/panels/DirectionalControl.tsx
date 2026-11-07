import React from 'react';
import { Button } from '../../common/components/Button';
import { Slider } from '../../common/components/Slider';
// Styles handled by panels.css

/** The slew rate the slider starts at, until the parent says otherwise. */
export const DEFAULT_SLEW_RATE = 2;

export interface DirectionalControlProps {
    onStop: () => void;
    onStartMove: (direction: string) => void;
    onStopMove: (direction: string) => void;
    onSetSlewRate: (rate: number) => void;
    /**
     * The slew rate the slider shows. The parent keeps it, so it survives the
     * Manual tab being closed and opened again.
     */
    slewRate?: number;
    /** True when the app may not command the mount. Every control is then disabled. */
    disabled?: boolean;
}

/**
 * DirectionalControl Component
 *
 * Renders a compass-pad style interface for manual telescope slewing.
 * Supports touch, mouse and keyboard (Space or Enter held down) for continuous
 * movement relative to press duration. A move never outlives the pad: it stops
 * when the pointer is released anywhere, when the window loses focus, and when
 * the pad is removed from the screen.
 * The 3x3 pad is compact and the slew-speed slider sits directly under it, as wide as the pad.
 */
export const DirectionalControl: React.FC<DirectionalControlProps> = ({
    onStop,
    onStartMove,
    onStopMove,
    onSetSlewRate,
    slewRate = DEFAULT_SLEW_RATE,
    disabled = false
}) => {
    const [activeDirection, setActiveDirection] = React.useState<string | null>(null);
    // The newest move and stop callbacks, so the cleanup below never uses old ones.
    const activeDirectionRef = React.useRef<string | null>(null);
    const onStopMoveRef = React.useRef(onStopMove);
    React.useEffect(() => {
        onStopMoveRef.current = onStopMove;
    });

    /** Starts a move; ignored while disabled or while another move is held. */
    const handleStart = (direction: string) => {
        if (disabled || activeDirectionRef.current) return;
        activeDirectionRef.current = direction;
        setActiveDirection(direction);
        onStartMove(direction);
    };

    /** Stops the move in progress, whichever way it was started. */
    const stopActiveMove = React.useCallback(() => {
        const direction = activeDirectionRef.current;
        if (!direction) return;
        activeDirectionRef.current = null;
        setActiveDirection(null);
        onStopMoveRef.current(direction);
    }, []);

    // While a move is held, a release or focus loss anywhere ends it.
    React.useEffect(() => {
        if (!activeDirection) return undefined;
        window.addEventListener('pointerup', stopActiveMove);
        window.addEventListener('pointercancel', stopActiveMove);
        window.addEventListener('blur', stopActiveMove);
        return () => {
            window.removeEventListener('pointerup', stopActiveMove);
            window.removeEventListener('pointercancel', stopActiveMove);
            window.removeEventListener('blur', stopActiveMove);
        };
    }, [activeDirection, stopActiveMove]);

    // Closing the Manual tab (or switching display) while a move is held stops the mount.
    React.useEffect(() => stopActiveMove, [stopActiveMove]);

    const handleRateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        if (disabled) return;
        const value = parseInt(event.target.value, 10);
        onSetSlewRate(value);
    };

    return (
        <div id="manual-guide-controls" className="navigator navigator--side">
            <div id="directional-button-grid" className="navigator__grid">
                <Button
                    variant="ghost"
                    id="btn-move-nw"
                    className="navigator__btn nw"
                    aria-label="Move northwest"
                    onMouseDown={() => handleStart('NW')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('NW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('NW'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↖
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-n"
                    className="navigator__btn n"
                    aria-label="Move north"
                    onMouseDown={() => handleStart('N')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('N'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('N'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↑
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-ne"
                    className="navigator__btn ne"
                    aria-label="Move northeast"
                    onMouseDown={() => handleStart('NE')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('NE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('NE'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↗
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-w"
                    className="navigator__btn w"
                    aria-label="Move west"
                    onMouseDown={() => handleStart('W')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('W'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('W'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ←
                </Button>
                <Button
                    variant="ghost"
                    id="btn-manual-stop"
                    className="navigator__stop"
                    disabled={disabled}
                    aria-disabled={disabled}
                    onClick={() => {
                        if (disabled) return;
                        activeDirectionRef.current = null;
                        setActiveDirection(null);
                        onStop();
                    }}
                >
                    STOP
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-e"
                    className="navigator__btn e"
                    aria-label="Move east"
                    onMouseDown={() => handleStart('E')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('E'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('E'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    →
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-sw"
                    className="navigator__btn sw"
                    aria-label="Move southwest"
                    onMouseDown={() => handleStart('SW')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('SW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('SW'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↙
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-s"
                    className="navigator__btn s"
                    aria-label="Move south"
                    onMouseDown={() => handleStart('S')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('S'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('S'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↓
                </Button>
                <Button
                    variant="ghost"
                    id="btn-move-se"
                    className="navigator__btn se"
                    aria-label="Move southeast"
                    onMouseDown={() => handleStart('SE')}
                    onMouseUp={stopActiveMove}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('SE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); stopActiveMove(); }}
                    onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); handleStart('SE'); } }}
                    onKeyUp={(e) => { if (e.key === ' ' || e.key === 'Enter') stopActiveMove(); }}
                    onBlur={stopActiveMove}
                    disabled={disabled}
                    aria-disabled={disabled}
                >
                    ↘
                </Button>
            </div>

            <div id="slew-speed-control" className="slew-rate">
                <span id="label-slew-speed" className="slew-rate__title">Slew speed</span>
                <div className="slew-rate__track">
                    <span id="label-slew-min" className="slew-rate__end">Min</span>
                    <Slider
                        id="input-slew-rate-slider"
                        aria-labelledby="label-slew-speed"
                        min="0"
                        max="3"
                        step="1"
                        value={slewRate}
                        onChange={handleRateChange}
                        disabled={disabled}
                        aria-disabled={disabled}
                        className="slew-rate__slider"
                    />
                    <span id="label-slew-max" className="slew-rate__end">Max</span>
                </div>
            </div>
        </div>
    );
};
