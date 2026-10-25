import React, { useState, useEffect, Suspense, Profiler, ProfilerOnRenderCallback } from 'react';
import { callBackend } from './common/services/backendApi';
import { isAffectedByMeasurement, QualityMeasuredPayload } from './common/queries/qualityQueryKeys';
import { UI_EVENTS } from './common/types/backendTypes';
import { emergencyParkMount } from './common/services/telescope/emergencyPark';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusHeader } from './statusHeader/StatusHeader';
import { TitleBar } from './titleBar/TitleBar';
import { TargetProvider, DeferWhileHidden } from './common/context/TargetContext';
import { HandoffProvider } from './common/context/HandoffContext';
import { PlanningProvider } from './observationManager/context/PlanningContext';
import { PlanningTimeProvider } from './common/context/PlanningTimeContext';
import { CurrentTargetAnnouncer } from './common/components/CurrentTargetAnnouncer';
import { handlePlanChanged, PLAN_UPDATED_FROM_ANOTHER_WINDOW, PlanChangedPayload } from './common/utils/planChanged';
import { TerminalProvider } from './statusHeader/context/TerminalContext';
import { RemoteStatusProvider } from './common/context/RemoteStatusContext';
import { AstrometricsProvider, useAstrometrics } from './common/context/AstrometricsContext';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from './common/constants/displayFlags';
import { normalizeAppMode } from './common/utils/normalizeAppMode';
// Listens for "Show in calibration library" from the start, so a request sent
// before the (lazy) Observatory Manager has loaded is kept for it.
import './observatoryManager/calibration/calibrationRowRequestStore';
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
const ImageViewerDisplay = React.lazy(() =>
  import('./imageViewerDisplay/ImageViewerDisplay').then((m) => ({ default: m.ImageViewerDisplay }))
);
const AstronomyManager = React.lazy(() =>
  import('./astronomyManager/AstronomyManager').then((m) => ({
    default: m.AstronomyManager,
  }))
);
const ImageProcessingDisplay = React.lazy(() =>
  import('./imageProcessingDisplay/ImageProcessingDisplay').then((m) => ({
    default: m.ImageProcessingDisplay,
  }))
);
const ObservatoryManager = React.lazy(() =>
  import('./observatoryManager/ObservatoryManager').then((m) => ({
    default: m.ObservatoryManager,
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
const CommandConsole = React.lazy(() =>
  import('./commandConsole/CommandConsole').then((m) => ({
    default: m.CommandConsole,
  }))
);

/**
 * Slowest render time (ms) that goes unreported. 50 ms is about three frames:
 * long enough to be felt as a stutter. The old limit of one frame (16 ms) printed
 * a console warning for most ordinary renders, which buried real problems and
 * looked like errors. Set `localStorage.profilerThresholdMs` (for example to
 * 16) to see more while hunting for slow components.
 */
const DEFAULT_PROFILER_THRESHOLD_MS = 50;

const profilerThresholdMs = (): number => {
  try {
    const stored = Number(window.localStorage.getItem('profilerThresholdMs'));
    return stored > 0 ? stored : DEFAULT_PROFILER_THRESHOLD_MS;
  } catch {
    return DEFAULT_PROFILER_THRESHOLD_MS;
  }
};

/**
 * Profiler callback — dev only. React strips onRender calls in production builds.
 * Logs any render slower than `profilerThresholdMs()` so slow components
 * are visible in the DevTools console.
 */
const onRenderProfile: ProfilerOnRenderCallback = (
  id, phase, actualDuration, baseDuration
) => {
  if (actualDuration > profilerThresholdMs()) {
    console.info(
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
  { mode: 'Image Viewer', id: 'ImageViewerDisplay', Component: ImageViewerDisplay },
  { mode: 'Astronomy Manager', id: 'AstronomyManager', Component: AstronomyManager },
  { mode: 'Planetarium', id: 'PlanetariumDisplay', Component: PlanetariumDisplay },
  { mode: 'Image Processing', id: 'ImageProcessingDisplay', Component: ImageProcessingDisplay },
  { mode: 'Observatory Manager', id: 'ObservatoryManager', Component: ObservatoryManager },
  { mode: 'Observation Manager', id: 'ObservationManager', Component: ObservationManager },
  { mode: 'Command Console', id: 'CommandConsole', Component: CommandConsole },
];

/**
 * Internal layout wrapper that handles dynamic mode switching,
 * URL parameter parsing, and WebSocket action routing (e.g. forced navigation).
 *
 * Uses `visitedModes` to mount heavy components lazily on first visit,
 * keeping them mounted (but hidden) to preserve state during navigation.
 */
// Whether this window is an auxiliary/secondary display (opened via "Open in
// new window") rather than the main window. Read once at module scope since
// it's fixed for the lifetime of a given window (its URL query string never
// changes).
const isAuxWindow = (() => {
  try {
    return Boolean(new URLSearchParams(window.location.search).get('windowId'));
  } catch {
    return false;
  }
})();

const AppContent: React.FC = () => {
  const [mode, setMode] = useState<string>(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const urlMode = params.get('mode');
      if (urlMode) return normalizeAppMode(urlMode);
      if (isAuxWindow) return 'Image Processing';
      return normalizeAppMode(window.localStorage.getItem('appMode') || 'Image Viewer');
    } catch {
      return 'Image Viewer';
    }
  });

  const { config } = useAstrometrics();
  const [planNotice, setPlanNotice] = useState<string>('');

  // Tracks every mode that's been mounted (and thus stays mounted, hidden via
  // CSS, rather than remounting from scratch when revisited). The main
  // window force-mounts every mode immediately at boot, so the splash screen
  // (gated on every mode reporting its data loaded — see
  // ui/common/utils/appBootReadiness.ts) covers the whole app, not just the
  // first view shown. Auxiliary windows keep the original on-demand
  // behavior: they only ever show one mode, so mounting the rest would be
  // pure waste.
  const [visitedModes, setVisitedModes] = useState<Set<string>>(
    () => (isAuxWindow ? new Set([mode]) : new Set(MODE_PANELS.map((p) => p.mode)))
  );
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
    if (isDisplayEnabled(config, mode)) return;

    const fallbackMode = DISPLAY_DEFINITIONS.find((d) => isDisplayEnabled(config, d.mode))?.mode || 'Image Viewer';
    console.log(`[App] Mode '${mode}' is disabled in settings. Falling back to '${fallbackMode}'.`);
    setMode(fallbackMode);
    try {
      window.localStorage.setItem('appMode', fallbackMode);
    } catch {
      // Ignore localStorage access failures (e.g. in private browsing)
    }
  }, [mode, config]);

  useEffect(() => {
    // Track the socket action handler so we can remove the exact same reference on cleanup.
    let removeSocketAction: (() => void) | null = null;
    let planNoticeTimer: ReturnType<typeof setTimeout> | null = null;

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
            const navigateMode = normalizeAppMode(payload.mode);
            setMode(navigateMode);
            window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: navigateMode }));
          }
        } else if (action === 'handoff') {
          // The shared target and planning time are adopted by HandoffProvider,
          // TargetProvider and PlanningTimeProvider from this same event. Nothing
          // else is needed here, and no window event is sent, so a received
          // target is never published back.
        } else if (action === 'log') {
          window.dispatchEvent(new CustomEvent('astrometrics:log', { detail: payload }));
        } else if (action === 'refresh') {
          window.location.reload();
        } else if (action === 'catalog:changed') {
          // The backend rescanned the stellar catalog (see StellarService's
          // dataset-version check) because a write happened somewhere --
          // refetch now instead of waiting on these queries' own polling
          // fallback (see useTargetDataAvailabilityQuery, useSpectralClassSummaryQuery).
          queryClient.invalidateQueries({ queryKey: ['targetDataAvailability'] });
          queryClient.invalidateQueries({ queryKey: ['spectralClassSummary'] });
          queryClient.invalidateQueries({ queryKey: ['starsBySpectralClass'] });
        } else if (action === UI_EVENTS['plan:changed']) {
          // A plan write happened (maybe in another window): read the plans again.
          if (handlePlanChanged(queryClient, payload as PlanChangedPayload)) {
            setPlanNotice(PLAN_UPDATED_FROM_ANOTHER_WINDOW);
            if (planNoticeTimer) clearTimeout(planNoticeTimer);
            planNoticeTimer = setTimeout(() => setPlanNotice(''), 5000);
          }
        } else if (action === UI_EVENTS['quality:measured']) {
          // A measurement job stored new quality rows (after a sync, an
          // ingestion, or when asked for): the stored quality queries for that
          // target or those nights read again, and so do queries that name
          // neither.
          queryClient.invalidateQueries({
            predicate: (query) => isAffectedByMeasurement(query.queryKey, payload as QualityMeasuredPayload),
          });
        }
      };

      socketClient.on('action', handleAction);
      removeSocketAction = () => socketClient.off('action', handleAction);
    });

    /** Handles custom mode change events. */
    const onModeChange = (e: Event): void => {
      const rawDetail = (e as CustomEvent).detail;
      const detail = normalizeAppMode(rawDetail);
      setMode(detail);
      try {
        const isAuxWindow = Boolean(new URLSearchParams(window.location.search).get('windowId'));
        if (!isAuxWindow) {
          window.localStorage.setItem('appMode', detail);
        }
      } catch {
        // Ignore localStorage access failures
      }
      callBackend(
        'handoff:update_state',
        { active_mode: detail, origin_device: 'desktop' },
        { silent: true },
      ).catch(() => {});
    };
    window.addEventListener('astrometrics:modeChange', onModeChange);

    // Native OS Integration: Listen to mode switches from Ubuntu dock / Windows Jump List / Tray
    let removeNavMode: (() => void) | undefined;
    if (window.astrometrics?.app?.onNavigateMode) {
      removeNavMode = window.astrometrics.app.onNavigateMode((navMode: string) => {
        if (navMode) {
          const detail = normalizeAppMode(navMode);
          setMode(detail);
          window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail }));
        }
      });
    }

    // Native OS Integration: Listen to remote actions routed from other windows
    let removeRemoteAction: (() => void) | undefined;
    if (window.astrometrics?.app?.onRemoteAction) {
      removeRemoteAction = window.astrometrics.app.onRemoteAction((data: any) => {
        const { action, payload, intent } = data || {};
        if (action) {
          window.dispatchEvent(new CustomEvent(`astrometrics:${action}`, { detail: payload }));
        }
        if (intent) {
          window.dispatchEvent(new CustomEvent('astrometrics:navigationIntent', { detail: intent }));
        }
      });
    }

    // Native OS Integration: Emergency park telescope command from tray
    const onEmergencyPark = (): void => {
      void emergencyParkMount();
    };
    window.addEventListener('emergency-park-mount', onEmergencyPark);

    return () => {
      if (planNoticeTimer) clearTimeout(planNoticeTimer);
      removeSocketAction?.();
      removeNavMode?.();
      removeRemoteAction?.();
      window.removeEventListener('astrometrics:modeChange', onModeChange);
      window.removeEventListener('emergency-park-mount', onEmergencyPark);
    };
  }, []);

  // Update dynamic tray menu and report active mode to Electron main process
  useEffect(() => {
    if (window.astrometrics?.app?.updateTrayStatus) {
      window.astrometrics.app.updateTrayStatus({ activeMode: mode });
    }
    if (window.astrometrics?.app?.reportWindowMode) {
      window.astrometrics.app.reportWindowMode(mode);
    }
  }, [mode]);

  return (
    <div className="app">
      <TitleBar />
      <CurrentTargetAnnouncer />
      <div
        aria-live="polite"
        role="status"
        data-testid="plan-notice"
        style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }}
      >
        {planNotice}
      </div>
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
                  <DeferWhileHidden active={mode === panelMode}>
                    <Profiler id={id} onRender={onRenderProfile}>
                      <Component />
                    </Profiler>
                  </DeferWhileHidden>
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
          <HandoffProvider>
            <TargetProvider>
              <PlanningTimeProvider>
                <PlanningProvider>
                  <AppContent />
                </PlanningProvider>
              </PlanningTimeProvider>
            </TargetProvider>
          </HandoffProvider>
        </RemoteStatusProvider>
      </AstrometricsProvider>
    </QueryClientProvider>
  );
};

export default App;
