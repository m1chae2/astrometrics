import React from 'react';
import '../../common/styles/button.css';
// Styles handled by panels.css

export interface DirectionalControlProps {
    onStop: () => void;
    onStartMove: (direction: string) => void;
    onStopMove: (direction: string) => void;
    onSetSlewRate: (rate: number) => void;
}

/**
 * DirectionalControl Component
 *
 * Renders a compass-pad style interface for manual telescope slewing.
 * Renders a compass-pad style interface for manual telescope slewing.
 * Supports touch and mouse interactions for continuous movement relative to press duration.
 * REQ: OBS-2: Manual Telescope Movement
 */
export const DirectionalControl: React.FC<DirectionalControlProps> = ({
    onStop,
    onStartMove,
    onStopMove,
    onSetSlewRate
}) => {
    const [activeDirection, setActiveDirection] = React.useState<string | null>(null);

    const handleStart = (direction: string) => {
        setActiveDirection(direction);
        onStartMove(direction);
    };

    const handleEnd = (direction: string) => {
        if (activeDirection === direction) {
            setActiveDirection(null);
            onStopMove(direction);
        }
    };

    const handleRateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        const value = parseInt(event.target.value, 10);
        onSetSlewRate(value);
    };

    return (
        <div id="manual-guide-controls" className="navigator">
            <div id="directional-button-grid" className="navigator__grid">
                <button
                    id="btn-move-nw"
                    className="navigator__btn nw"
                    // REQ: OBS-2.2: The display SHALL provide diagonal slew buttons for combined motion (NW, NE, SW, SE).
                    onMouseDown={() => handleStart('NW')} onMouseUp={() => handleEnd('NW')}
                    onMouseLeave={() => handleEnd('NW')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('NW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('NW'); }}
                    type="button"
                >
                    ↖
                </button>
                <button
                    id="btn-move-n"
                    className="navigator__btn n"
                    // REQ: OBS-2.1: The display SHALL provide directional slew buttons for North, South, East, and West commands.
                    // REQ: OBS-2.3: The display SHALL initiate movement in the selected direction immediately upon button press.
                    // REQ: OBS-2.4: The display SHALL halt movement immediately upon button release.
                    onMouseDown={() => handleStart('N')} onMouseUp={() => handleEnd('N')}
                    onMouseLeave={() => handleEnd('N')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('N'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('N'); }}
                    type="button"
                >
                    ↑
                </button>
                <button
                    id="btn-move-ne"
                    className="navigator__btn ne"
                    onMouseDown={() => handleStart('NE')} onMouseUp={() => handleEnd('NE')}
                    onMouseLeave={() => handleEnd('NE')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('NE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('NE'); }}
                    type="button"
                >
                    ↗
                </button>

                <button
                    id="btn-move-w"
                    className="navigator__btn w"
                    onMouseDown={() => handleStart('W')} onMouseUp={() => handleEnd('W')}
                    onMouseLeave={() => handleEnd('W')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('W'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('W'); }}
                    type="button"
                >
                    ←
                </button>
                <button
                    id="btn-manual-stop"
                    className="navigator__stop"
                    onClick={() => {
                        setActiveDirection(null);
                        onStop();
                    }}
                    // REQ: OBS-2.5: The display SHALL provide a "STOP" button to immediately halt all telescope movement.
                    type="button"
                >
                    STOP
                </button>
                <button
                    id="btn-move-e"
                    className="navigator__btn e"
                    onMouseDown={() => handleStart('E')} onMouseUp={() => handleEnd('E')}
                    onMouseLeave={() => handleEnd('E')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('E'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('E'); }}
                    type="button"
                >
                    →
                </button>

                <button
                    id="btn-move-sw"
                    className="navigator__btn sw"
                    onMouseDown={() => handleStart('SW')} onMouseUp={() => handleEnd('SW')}
                    onMouseLeave={() => handleEnd('SW')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('SW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('SW'); }}
                    type="button"
                >
                    ↙
                </button>
                <button
                    id="btn-move-s"
                    className="navigator__btn s"
                    onMouseDown={() => handleStart('S')} onMouseUp={() => handleEnd('S')}
                    onMouseLeave={() => handleEnd('S')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('S'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('S'); }}
                    type="button"
                >
                    ↓
                </button>
                <button
                    id="btn-move-se"
                    className="navigator__btn se"
                    onMouseDown={() => handleStart('SE')} onMouseUp={() => handleEnd('SE')}
                    onMouseLeave={() => handleEnd('SE')}
                    onTouchStart={(e) => { e.preventDefault(); handleStart('SE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); handleEnd('SE'); }}
                    type="button"
                >
                    ↘
                </button>
            </div>

            <div id="slew-speed-control" className="slew-rate">
                {/* REQ: OBS-2.6: The display SHALL provide a slew rate selector with at least 4 speed levels. */}
                <div className="slew-rate__labels">
                    <span id="label-slew-speed">Slew Speed:</span>
                    <span id="label-slew-range">Min &rarr; Max</span>
                </div>
                <input
                    id="input-slew-rate-slider"
                    type="range"
                    min="0"
                    max="3"
                    step="1"
                    defaultValue="2"
                    onChange={handleRateChange}
                    className="slew-rate__slider"
                />
            </div>
        </div>
    );
};
