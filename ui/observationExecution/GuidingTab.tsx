/**
 * @fileoverview The "Guiding" tab: the guiding trends and the guiding target
 * (bullseye) plots, each with a text alternative that says what it shows.
 */

import React from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { GuidingTargetPlot, GuidingTrendsPlot } from './panels/GuidingStatusPanel';

/**
 * The guiding tab.
 * @return The tab's content.
 */
export const GuidingTab: React.FC = () => {
    const { telemetry } = useTelescopeStatus();
    const history = telemetry.guidingHistory || [];
    return (
        <div className="guiding-tab">
            <SectionPanel title="Guiding Trends" className="panel--guiding-trends">
                <p className="visually-hidden" id="guiding-trends-description">
                    Line chart of the guiding error along right ascension (dRA) and declination (dDEC) over time,
                    with the guide star signal, rolling error and correction pulses. It shows {history.length} samples.
                </p>
                <div role="group" aria-labelledby="guiding-trends-description" className="guiding-tab__plot">
                    <GuidingTrendsPlot history={history} />
                </div>
            </SectionPanel>
            <SectionPanel title="Guiding Target" className="panel--guiding-target">
                <p className="visually-hidden" id="guiding-target-description">
                    Scatter plot of each guiding correction as a point, dRA against dDEC, with circles that mark
                    the size of the error. Points near the center mean good guiding. It shows {history.length} samples.
                </p>
                <div role="group" aria-labelledby="guiding-target-description" className="guiding-tab__plot">
                    <GuidingTargetPlot history={history} />
                </div>
            </SectionPanel>
        </div>
    );
};
