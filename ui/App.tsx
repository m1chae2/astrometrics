import React, { useState, useEffect, Suspense, Profiler, ProfilerOnRenderCallback, Activity } from 'react';
import { PanelErrorBoundary } from './common/components/PanelErrorBoundary';
import { callBackend } from './common/services/backendApi';
import { isAffectedByMeasurement, QualityMeasuredPayload } from './common/queries/qualityQueryKeys';
import { UI_EVENTS } from './common/types/backendTypes';
import {
  PACKAGE_LIBRARY_QUERY_KEY,
  CURRENT_SESSION_QUERY_KEY,
  PACKAGE_REFRESH_NOTICE_QUERY_KEY,
  PackageRefreshCounts,
} from './common/queries/packageLibraryQueryKeys';
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
import { IngestionJobsProvider } from './common/context/IngestionJobsContext';
import { RemoteStatusProvider } from './common/context/RemoteStatusContext';
import { AstrometricsProvider, useAstrometrics } from './common/context/AstrometricsContext';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from './common/constants/displayFlags';
import { normalizeAppMode } from './common/utils/normalizeAppMode';
import { useIsModeReady } from './common/utils/appBootReadiness';
import { postHandoff } from './common/utils/displayHandoffStore';
import { COMMAND_CONSOLE_EVENT_KIND } from './common/utils/commandConsoleEvents';
// Listens for "Show these frames" from the start, so the Image Viewer also
// knows which mode to go back to (see citedFramesStore.ts).
import './imageViewerDisplay/citedFrames/citedFramesStore';
import './App.css';

import { Skeleton } from './common/components/Skeleton';
// Single shared cache for all shared-resource queries (see ui/common/queries/),
// so multiple views requesting the same data (e.g. the target list) share one
// in-flight request and one cached result instead of each fetching independently.
/**
 * Builds the app's query client with its shared freshness rules.
 * Exported so tests can count requests under the same rules.
 *
 * @return A new query client.
 */
export const createAppQueryClient = (): QueryClient => new QueryClient({
  defaultOptions: {
    queries: {
      // Data read in the last 30 seconds counts as fresh, so coming back to a
      // display does not read it all again. Every action that changes data
      // invalidates the queries it affects, and polls use refetchInterval,
      // so a user's own change is never hidden by this.
      staleTime: 30_000,
      // Alt-tabbing back to the app does not refetch. Fresh data comes from
      // polls, socket events and invalidation.
      refetchOnWindowFocus: false,
    },
  },
});
const queryClient = createAppQueryClient();


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
 * Socket actions the Command Console shows: what an AI agent ran in the
 * terminal, a plot it made, and an editor change.
 */
const COMMAND_CONSOLE_ACTIONS: ReadonlySet<string> = new Set([
  'terminal:agent_exec',
  'terminal:plot_generated',
  'editor:sync',
]);

/**
 * When another window asks this one to show something and names no display,
 * these actions go to the display shown here. (A target picked from the tray
 * menu opens in Image Processing.)
 */
const DEFAULT_DISPLAY_FOR_REMOTE_ACTION: Readonly<Record<string, string>> = {
  targetSelected: 'Image Processing',
};

/**
 * The displays that may be paused while hidden (wrapped in React's `Activity`).
 *
 * A paused display keeps its state, but React stops rendering it and runs the
 * cleanup of all its effects (timers, socket listeners, query subscriptions,
 * animation loops). When it is shown again the effects start over. A display is
 * safe to pause when (a) nothing sends it a window event while it is hidden
 * (requests go through the hand-off store, `displayHandoffStore.ts`, which keeps
 * them until the display runs), (b) no background job runs inside it (frame
 * downloads run in `IngestionJobsContext`), (c) it does not free things in an
 * effect cleanup that its state still uses (the Image Viewer's pictures live in
 * `targetImageCache.ts`), and (d) no effect throws away the user's state when it
 * starts again. Pausing also stops work nobody can see: the Planetarium's
 * drawing loop, the Observatory Manager's status polling, and the sky map's
 * arrow-key listeners.
 *
 * Image Processing is included. Its effects that cleared the selected frame, the
 * analysis result, the checked files and the log now run only when the target
 * really changes (`useOnValueChange`), and its reads run again when it is shown.
 */
export const PAUSABLE_MODES: ReadonlySet<string> = new Set([
  'Image Viewer',
  'Astronomy Manager',
  'Planetarium',
  'Observatory Manager',
  'Observation Manager',
  'Image Processing',
  'Command Console',
]);

/**
 * One display's slot in the app. Shows it, or hides it with CSS and (for the
 * displays in `PAUSABLE_MODES`) pauses it with `Activity`.
 *
 * A display is never paused before it has reported that its first data is
 * loaded. The startup splash screen waits for that report, and a paused
 * display does not run its effects, so pausing early would hang the splash.
 *
 * @param props.panelMode The mode this slot belongs to.
 * @param props.activeMode The mode the user is looking at.
 * @param props.children The display.
 */
export const ModePanel: React.FC<{ panelMode: string; activeMode: string; children: React.ReactNode }> = ({
  panelMode,
  activeMode,
  children,
}) => {
  const isActive = activeMode === panelMode;
  const hasLoaded = useIsModeReady(panelMode);
  const paused = PAUSABLE_MODES.has(panelMode) && !isActive && hasLoaded;
  return (
    <div className={isActive ? 'app__mode-panel--visible' : 'app__mode-panel--hidden'}>
      <Activity mode={paused ? 'hidden' : 'visible'}>
        <PanelErrorBoundary panelName={panelMode}>
          <DeferWhileHidden active={isActive}>{children}</DeferWhileHidden>
        </PanelErrorBoundary>
      </Activity>
    </div>
  );
};

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
        } else if (COMMAND_CONSOLE_ACTIONS.has(action)) {
          // Agent terminal output and editor syncs are for the Command Console.
          // They wait in the hand-off store (in order) so none is lost while
          // the console is hidden or paused.
          postHandoff('Command Console', COMMAND_CONSOLE_EVENT_KIND, { action, payload }, { queue: true });
        } else if (action === 'refresh') {
          window.location.reload();
        } else if (action === 'catalog:changed') {
          // The backend rescanned the stellar catalog (see StellarService's
          // dataset-version check) because a write happened somewhere --
          // refetch now; these catalog queries have no polling timer (see catalogQueryPolicy).
          queryClient.invalidateQueries({ queryKey: ['astronomyList'] });
          queryClient.invalidateQueries({ queryKey: ['astronomyCount'] });
          queryClient.invalidateQueries({ queryKey: ['targetDataAvailability'] });
          queryClient.invalidateQueries({ queryKey: ['spectralClassSummary'] });
          queryClient.invalidateQueries({ queryKey: ['starsBySpectralClass'] });
        } else if (action === UI_EVENTS['plan:changed']) {
          // A plan write happened (maybe in another window): read the plans again.
          void queryClient.invalidateQueries({ queryKey: CURRENT_SESSION_QUERY_KEY });
          if (handlePlanChanged(queryClient, payload as PlanChangedPayload)) {
            setPlanNotice(PLAN_UPDATED_FROM_ANOTHER_WINDOW);
            if (planNoticeTimer) clearTimeout(planNoticeTimer);
            planNoticeTimer = setTimeout(() => setPlanNotice(''), 5000);
          }
        } else if (action === UI_EVENTS['packages:changed']) {
          // A package was created, edited, reopened, deleted or promoted: read the library again.
          queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
          queryClient.invalidateQueries({ queryKey: CURRENT_SESSION_QUERY_KEY });
          const packagesPayload = payload as ({ kind?: string } & PackageRefreshCounts) | undefined;
          if (packagesPayload?.kind === 'refreshed') {
            // The library shows "Suggestions updated: ..." from the newest refresh result.
            queryClient.setQueryData(PACKAGE_REFRESH_NOTICE_QUERY_KEY, { ...packagesPayload, receivedAt: Date.now() });
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
        // Another window asked this one to show something. It goes through the
        // hand-off store like a local request, so a hidden display gets it too.
        const display: string | undefined =
          intent?.targetDisplay ?? (action ? DEFAULT_DISPLAY_FOR_REMOTE_ACTION[action] : undefined);
        if (action && display) {
          postHandoff(display, action, payload ?? intent?.payload, { element: intent?.targetElement });
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
            fallback={<Skeleton variant="block" label="Loading this display" />}
          >
            {MODE_PANELS.filter(({ mode: panelMode }) => visitedModes.has(panelMode)).map(
              ({ mode: panelMode, id, Component }) => (
                <ModePanel key={id} panelMode={panelMode} activeMode={mode}>
                  <Profiler id={id} onRender={onRenderProfile}>
                    <Component />
                  </Profiler>
                </ModePanel>
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
              <IngestionJobsProvider>
                <PlanningTimeProvider>
                  <PlanningProvider>
                    <AppContent />
                  </PlanningProvider>
                </PlanningTimeProvider>
              </IngestionJobsProvider>
            </TargetProvider>
          </HandoffProvider>
        </RemoteStatusProvider>
      </AstrometricsProvider>
    </QueryClientProvider>
  );
};

export default App;
