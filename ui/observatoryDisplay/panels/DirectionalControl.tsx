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
                    onMouseDown={() => onStartMove('NW')} onMouseUp={() => onStopMove('NW')}
                    onMouseLeave={() => onStopMove('NW')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('NW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('NW'); }}
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
                    onMouseDown={() => onStartMove('N')} onMouseUp={() => onStopMove('N')}
                    onMouseLeave={() => onStopMove('N')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('N'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('N'); }}
                    type="button"
                >
                    ↑
                </button>
                <button
                    id="btn-move-ne"
                    className="navigator__btn ne"
                    onMouseDown={() => onStartMove('NE')} onMouseUp={() => onStopMove('NE')}
                    onMouseLeave={() => onStopMove('NE')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('NE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('NE'); }}
                    type="button"
                >
                    ↗
                </button>

                <button
                    id="btn-move-w"
                    className="navigator__btn w"
                    onMouseDown={() => onStartMove('W')} onMouseUp={() => onStopMove('W')}
                    onMouseLeave={() => onStopMove('W')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('W'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('W'); }}
                    type="button"
                >
                    ←
                </button>
                <button
                    id="btn-manual-stop"
                    className="navigator__stop"
                    onClick={onStop}
                    // REQ: OBS-2.5: The display SHALL provide a "STOP" button to immediately halt all telescope movement.
                    type="button"
                >
                    STOP
                </button>
                <button
                    id="btn-move-e"
                    className="navigator__btn e"
                    onMouseDown={() => onStartMove('E')} onMouseUp={() => onStopMove('E')}
                    onMouseLeave={() => onStopMove('E')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('E'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('E'); }}
                    type="button"
                >
                    →
                </button>

                <button
                    id="btn-move-sw"
                    className="navigator__btn sw"
                    onMouseDown={() => onStartMove('SW')} onMouseUp={() => onStopMove('SW')}
                    onMouseLeave={() => onStopMove('SW')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('SW'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('SW'); }}
                    type="button"
                >
                    ↙
                </button>
                <button
                    id="btn-move-s"
                    className="navigator__btn s"
                    onMouseDown={() => onStartMove('S')} onMouseUp={() => onStopMove('S')}
                    onMouseLeave={() => onStopMove('S')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('S'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('S'); }}
                    type="button"
                >
                    ↓
                </button>
                <button
                    id="btn-move-se"
                    className="navigator__btn se"
                    onMouseDown={() => onStartMove('SE')} onMouseUp={() => onStopMove('SE')}
                    onMouseLeave={() => onStopMove('SE')}
                    onTouchStart={(e) => { e.preventDefault(); onStartMove('SE'); }}
                    onTouchEnd={(e) => { e.preventDefault(); onStopMove('SE'); }}
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
