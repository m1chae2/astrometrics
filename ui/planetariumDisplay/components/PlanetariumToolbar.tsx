/**
 * @module PlanetariumToolbar
 * @fileoverview Floating overlay-toggle toolbar for the Planetarium viewport.
 *
 * Renders a row of labelled checkboxes for toggling sky map overlays, a Date and
 * Time button to open the simulation time modal, and a live FOV readout.
 * The session picker is the shared SessionPicker. The row is the shared compact
 * Toolbar, so it has the same text size, gaps and dividers as the other
 * displays' toolbars (such as the FITS Viewer bar).
 */

import React from 'react';
import { Popover } from '../../common/components/Popover';
import { PlanningTimeIndicator } from '../../common/components/PlanningTimeIndicator';
import { SessionPicker, type SessionPickerOption } from '../../common/components/SessionPicker';
import { Button } from '../../common/components/Button';
import { Checkbox } from '../../common/components/Checkbox';
import { Slider } from '../../common/components/Slider';
import { Toolbar, ToolbarDivider, ToolbarGroup, ToolbarLabel, ToolbarValue } from '../../common/components/Toolbar';

/**
 * Props for PlanetariumToolbar.
 *
 * Each overlay has a paired boolean state and setter. The naming convention is
 * `show<Layer>` / `onToggle<Layer>`.
 */
interface Props {
  /** Show star catalog overlay. */
  showStars: boolean;
  onToggleStars: (value: boolean) => void;
  /** Show sensor FOV outline. */
  showFOV: boolean;
  onToggleFOV: (value: boolean) => void;
  /** Show local horizon and ground shading. */
  showEnvironment: boolean;
  onToggleEnvironment: (value: boolean) => void;
  /** Show RA/Dec coordinate grid. */
  showGrid: boolean;
  onToggleGrid: (value: boolean) => void;
  /** Show cataloged (library) objects. */
  showCatalog: boolean;
  onToggleCatalog: (value: boolean) => void;
  /** Show bundled constellation stick-figure lines. */
  showConstellations: boolean;
  onToggleConstellations: (value: boolean) => void;
  /** Show telescope pointing crosshair. */
  showTelescope: boolean;
  onToggleTelescope: (value: boolean) => void;
  /** Show mount tracking mechanical risk heatmap overlay. */
  showTrackingRisk?: boolean;
  onToggleTrackingRisk?: (value: boolean) => void;
  /** List of past observing sessions available for review. */
  availableSessions?: import('../../common/types/backendTypes').AlignmentSessionSummary[];
  /** Currently selected historical session identifier. */
  selectedSessionId?: string | null;
  /** Callback when user selects a different session. */
  onSelectSession?: (sessionId: string | null) => void;
  /** Show each target's stack picture on the sky (the Images layer). */
  showImages?: boolean;
  onToggleImages?: (value: boolean) => void;
  /** Opacity of the stack pictures, from 0 to 1. */
  imagesOpacity?: number;
  onImagesOpacityChange?: (value: number) => void;
  /** Current field of view in degrees, displayed as a readout. */
  currentFOV: number;
  /** Opens the date/time simulation modal. */
  onOpenTimeModal: () => void;
}

/**
 * Glassmorphic floating toolbar for toggling Planetarium overlay layers.
 *
 * Positioned absolutely at the top-right of the viewport container.
 * Groups passive celestial layers into a dropdown popover while keeping active
 * rig overlays and diagnostics immediately accessible.
 *
 * @func PlanetariumToolbar
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered toolbar.
 */
export const PlanetariumToolbar: React.FC<Props> = ({
  showStars,
  onToggleStars,
  showFOV,
  onToggleFOV,
  showEnvironment,
  onToggleEnvironment,
  showGrid,
  onToggleGrid,
  showCatalog,
  onToggleCatalog,
  showConstellations,
  onToggleConstellations,
  showTelescope,
  onToggleTelescope,
  showTrackingRisk = false,
  onToggleTrackingRisk,
  availableSessions = [],
  selectedSessionId = null,
  onSelectSession,
  currentFOV,
  onOpenTimeModal,
  showImages = false,
  onToggleImages,
  imagesOpacity = 0.8,
  onImagesOpacityChange,
}) => {
  /**
   * Builds the display label for a historical session option, e.g.
   * "2026-09-24 (8 targets, 1 sync)".
   */
  const getSessionLabel = (s: NonNullable<Props['availableSessions']>[number]): string => {
    const paStr = s.polarErrorArcsec !== null && s.polarErrorArcsec !== undefined
      ? ` • PA: ${(s.polarErrorArcsec / 60).toFixed(1)}'`
      : '';
    const counts: string[] = [];
    if (s.targetCount) {
      counts.push(`${s.targetCount} target${s.targetCount === 1 ? '' : 's'}`);
    }
    counts.push(`${s.syncCount} sync${s.syncCount === 1 ? '' : 's'}`);
    return `${s.sessionDate} (${counts.join(', ')}${paStr})`;
  };

  // The shared picker offers "All Sessions (Cumulative)" first, but only when there are sessions to add up.
  // Listed under two small headings, like the Layers menu: the whole-history choices, then each night.
  const sessionOptions: SessionPickerOption[] = [
    ...(availableSessions.length > 0 ? [{ id: 'all', label: 'All Sessions (Cumulative)', group: 'Selection' }] : []),
    ...availableSessions.map((s) => ({ id: s.sessionId, label: getSessionLabel(s), group: 'Nights' })),
  ];

  const passiveLayers = [
    { label: 'Stars', checked: showStars, onChange: onToggleStars },
    { label: 'Constellations', checked: showConstellations, onChange: onToggleConstellations },
    { label: 'Coordinate Grid', checked: showGrid, onChange: onToggleGrid },
    { label: 'Deep Catalog', checked: showCatalog, onChange: onToggleCatalog },
    { label: 'Ground Horizon', checked: showEnvironment, onChange: onToggleEnvironment },
    ...(onToggleTrackingRisk ? [{ label: 'Tracking Risk Heatmap', checked: showTrackingRisk, onChange: onToggleTrackingRisk }] : []),
  ];

  const rigOverlays = [
    { label: 'Telescope', checked: showTelescope, onChange: onToggleTelescope },
    { label: 'FOV Outline', checked: showFOV, onChange: onToggleFOV },
    ...(onToggleImages ? [{ label: 'Images', checked: showImages, onChange: onToggleImages }] : []),
  ];

  return (
    <Toolbar className="planetarium-toolbar" ariaLabel="Sky map">
      {/* Group 1: Passive Sky Layers Popover */}
      <Popover
        className="planetarium-toolbar__popover-container"
        trigger={({ isOpen, toggle }) => (
          <Button
            variant="secondary"
            size="small"
            active={isOpen}
            onClick={toggle}
            aria-expanded={isOpen}
            title="Toggle passive sky background and catalog layers"
          >
            <span>Layers</span>
            <span className="planetarium-toolbar__chevron">{isOpen ? '▲' : '▼'}</span>
          </Button>
        )}
      >
        {() => (
          <div className="planetarium-toolbar__popover-menu">
            <div className="planetarium-toolbar__popover-header">
              Sky Background
            </div>
            {passiveLayers.map(({ label, checked, onChange }) => (
              <Checkbox
                key={label}
                size="compact"
                label={label}
                checked={checked}
                onChange={(e) => onChange(e.target.checked)}
              />
            ))}
            {onImagesOpacityChange && (
              <>
                <div className="planetarium-toolbar__popover-header">
                  Stack Images
                </div>
                <label className="planetarium-toolbar__slider-row">
                  <span>Opacity</span>
                  <Slider
                    min="5"
                    max="100"
                    step="5"
                    value={Math.round(imagesOpacity * 100)}
                    disabled={!showImages}
                    aria-label="Image opacity"
                    onChange={(event) => onImagesOpacityChange(Number(event.target.value) / 100)}
                  />
                  <span className="planetarium-toolbar__slider-value">{Math.round(imagesOpacity * 100)}%</span>
                </label>
              </>
            )}
          </div>
        )}
      </Popover>

      <ToolbarDivider />

      {/* Group 2: Observation & Hardware Overlays */}
      <ToolbarGroup>
        {rigOverlays.map(({ label, checked, onChange }) => (
          <Checkbox
            key={label}
            size="compact"
            label={label}
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
          />
        ))}
      </ToolbarGroup>

      {/* Group 3: Session Selector — alignment is always active, so this is always shown. */}
      <ToolbarDivider />
      <ToolbarGroup>
        <ToolbarLabel>Session</ToolbarLabel>
        <SessionPicker
          label="Session"
          size="compact"
          liveLabel="No Session Selected"
          liveGroup="Selection"
          options={sessionOptions}
          selectedId={selectedSessionId}
          onSelect={(sessionId) => onSelectSession?.(sessionId)}
        />
      </ToolbarGroup>

      <ToolbarDivider />

      {/* Group 4: Time & Readouts */}
      <ToolbarGroup>
        <PlanningTimeIndicator size="compact" />
        <Button
          variant="secondary"
          size="small"
          onClick={onOpenTimeModal}
          title="Configure simulation date and time"
        >
          Date &amp; Time
        </Button>
      </ToolbarGroup>

      <ToolbarDivider />

      <ToolbarGroup>
        <ToolbarLabel>FOV</ToolbarLabel>
        <ToolbarValue ariaLabel={`Field of view ${currentFOV.toFixed(1)} degrees`} title="Field of view">
          {currentFOV.toFixed(1)}&deg;
        </ToolbarValue>
      </ToolbarGroup>
    </Toolbar>
  );
};
