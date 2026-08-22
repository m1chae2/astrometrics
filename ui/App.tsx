import React, { useState, useEffect, Suspense, Profiler, ProfilerOnRenderCallback } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusHeader } from './statusHeader/StatusHeader';
import { TargetProvider } from './common/context/TargetContext';
import { PlanningProvider } from './observationManager/context/PlanningContext';
import { TerminalProvider } from './statusHeader/context/TerminalContext';
import { RemoteStatusProvider } from './common/context/RemoteStatusContext';
import { AstrometricsProvider, useAstrometrics } from './common/context/AstrometricsContext';
import './App.css';

// Single shared cache for all shared-resource queries (see ui/common/queries/),
// so multiple views requesting the same data (e.g. the target list) share one
// in-flight request and one cached result instead of each fetching independently.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
    },
  },
});


// Lazy load main display components with named export handling.
const TargetDisplay = React.lazy(() =>
  import('./targetDisplay/TargetDisplay').then((m) => ({ default: m.TargetDisplay }))
);
const AstronomyDisplay = React.lazy(() =>
  import('./astronomyDisplay/AstronomyDisplay').then((m) => ({
    default: m.AstronomyDisplay,
  }))
);
const ImageProcessingDisplay = React.lazy(() =>
  import('./imageProcessingDisplay/ImageProcessingDisplay').then((m) => ({
    default: m.ImageProcessingDisplay,
  }))
);
const ObservatoryDisplay = React.lazy(() =>
  import('./observatoryDisplay/ObservatoryDisplay').then((m) => ({
    default: m.ObservatoryDisplay,
  }))
);
const ObservationManager = React.lazy(() =>
  import('./observationManager/ObservationManager').then((m) => ({
    default: m.ObservationManager,
  }))
);
const PlanetariumDisplay = React.lazy(() =>
  import('./planetariumDisplay/PlanetariumDisplay').then((m) => ({
    default: m.PlanetariumDisplay,
  }))
);

/**
 * Profiler callback — dev only. React strips onRender calls in production builds.
 * Logs any render that takes longer than one frame (>16ms) so slow components
 * are immediately visible in the DevTools console.
 */
const onRenderProfile: ProfilerOnRenderCallback = (
  id, phase, actualDuration, baseDuration
) => {
  if (actualDuration > 16) {
    console.warn(
      `[Profiler] %c${id}%c (${phase}) | actual: ${actualDuration.toFixed(1)}ms | base: ${baseDuration.toFixed(1)}ms`,
      'color: #f97316; font-weight: bold',
      'color: inherit'
    );
  }
};

// All mode panels, keyed by the `mode` value that makes them the active/visible
// one. Only panels the user has actually visited are mounted (see visitedModes
// below) — once visited, a panel stays mounted and is hidden via CSS when
// inactive, so switching back to it doesn't lose its local state or refetch
// its data. Adding a future display here automatically gets this same
// on-demand mounting for free; no other file needs to change.
const MODE_PANELS: { mode: string; id: string; Component: React.ComponentType }[] = [
  { mode: 'Image Viewer', id: 'TargetDisplay', Component: TargetDisplay },
  { mode: 'Astronomy Manager', id: 'AstronomyDisplay', Component: AstronomyDisplay },
  { mode: 'Planetarium', id: 'PlanetariumDisplay', Component: PlanetariumDisplay },
  { mode: 'Image Processing', id: 'ImageProcessingDisplay', Component: ImageProcessingDisplay },
  { mode: 'Observatory Manager', id: 'ObservatoryDisplay', Component: ObservatoryDisplay },
  { mode: 'Observation Manager', id: 'ObservationManager', Component: ObservationManager },
];

/**
 * Internal layout wrapper that handles dynamic mode switching,
 * URL parameter parsing, and WebSocket action routing (e.g. forced navigation).
 *
 * Uses `visitedModes` to mount heavy components lazily on first visit,
 * keeping them mounted (but hidden) to preserve state during navigation.
 */
const AppContent: React.FC = () => {
  const [mode, setMode] = useState<string>(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const urlMode = params.get('mode');
      if (urlMode) return urlMode;
      return window.localStorage.getItem('appMode') || 'Image Viewer';
    } catch {
      return 'Image Viewer';
    }
  });

  const { config } = useAstrometrics();

  // Tracks every mode the user has switched to during this session, so a
  // panel is mounted the first time its mode becomes active and then stays
  // mounted (hidden via CSS) rather than being mounted again from scratch.
  const [visitedModes, setVisitedModes] = useState<Set<string>>(() => new Set([mode]));
  useEffect(() => {
    setVisitedModes((previouslyVisitedModes) => {
      if (previouslyVisitedModes.has(mode)) return previouslyVisitedModes;
      const nextVisitedModes = new Set(previouslyVisitedModes);
      nextVisitedModes.add(mode);
      return nextVisitedModes;
    });
  }, [mode]);

  useEffect(() => {
    if (!config || !config['Frontend']) return;

    let modeAllowed = true;
    if (mode === 'Astronomy Manager' && config['Frontend']['enable_astronomy'] !== 'true') modeAllowed = false;
    if (mode === 'Planetarium' && config['Frontend']['enable_planetarium'] !== 'true') modeAllowed = false;
    if (mode === 'Observatory Manager' && config['Frontend']['enable_observatory'] !== 'true') modeAllowed = false;
    if (mode === 'Observation Manager' && config['Frontend']['enable_observation'] !== 'true') modeAllowed = false;

    if (!modeAllowed) {
      console.log(`[App] Mode '${mode}' is disabled in settings. Falling back to 'Image Viewer'.`);
      setMode('Image Viewer');
      try {
        window.localStorage.setItem('appMode', 'Image Viewer');
      } catch {
        // Ignore localStorage access failures (e.g. in private browsing)
      }
    }
  }, [mode, config]);

  useEffect(() => {
    // Track the socket action handler so we can remove the exact same reference on cleanup.
    let removeSocketAction: (() => void) | null = null;

    // 1. Initialize Remote Logging
    import('./common/utils/remoteLogger').then(({ setupGlobalErrorListener }) => {
      setupGlobalErrorListener();
    });

    // 2. Initialize Socket Control
    import('./common/utils/socketClient').then(({ socketClient }) => {
      socketClient.connect();

      const handleAction = (action: string, payload: any) => {
        if (action === 'navigate') {
          if (payload.mode) {
            setMode(payload.mode);
            window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: payload.mode }));
          }
        } else if (action === 'log') {
          window.dispatchEvent(new CustomEvent('astrometrics:log', { detail: payload }));
        } else if (action === 'refresh') {
          window.location.reload();
        }
      };

      socketClient.on('action', handleAction);
      removeSocketAction = () => socketClient.off('action', handleAction);
    });

    /** Handles custom mode change events. */
    const onModeChange = (e: Event): void => {
      const detail = (e as CustomEvent).detail;
      setMode(detail);
    };
    window.addEventListener('astrometrics:modeChange', onModeChange);

    return () => {
      removeSocketAction?.();
      window.removeEventListener('astrometrics:modeChange', onModeChange);
    };
  }, []);

  return (
    <div className="app">
      <TerminalProvider>
        <Profiler id="StatusHeader" onRender={onRenderProfile}>
          <StatusHeader />
        </Profiler>
        <div className="app__content">
          <Suspense
            fallback={<div className="app__loading">Loading component...</div>}
          >
            {MODE_PANELS.filter(({ mode: panelMode }) => visitedModes.has(panelMode)).map(
              ({ mode: panelMode, id, Component }) => (
                <div
                  key={id}
                  className={mode === panelMode ? 'app__mode-panel--visible' : 'app__mode-panel--hidden'}
                >
                  <Profiler id={id} onRender={onRenderProfile}>
                    <Component />
                  </Profiler>
                </div>
              )
            )}
          </Suspense>
        </div>
      </TerminalProvider>
    </div>
  );
};

/**
 * Main Application component.
 * Manages the current application mode and orchestrates the top-level layout.
 */
export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <AstrometricsProvider>
        <RemoteStatusProvider>
          <TargetProvider>
            <PlanningProvider>
              <AppContent />
            </PlanningProvider>
          </TargetProvider>
        </RemoteStatusProvider>
      </AstrometricsProvider>
    </QueryClientProvider>
  );
};

export default App;
