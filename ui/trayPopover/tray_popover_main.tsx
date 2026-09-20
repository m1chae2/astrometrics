/**
 * @fileoverview Entry point for the Astrometrics tray popover React application.
 *
 * This is a lightweight, standalone React root that is completely separate from
 * the main application bundle. It only mounts the TrayPopover component and
 * wraps it in the minimum required context providers (AstrometricsProvider for
 * live WebSocket telemetry, and QueryClientProvider for any shared queries).
 *
 * Kept intentionally minimal — none of the main application mode panels,
 * Plotly, or FITS processing code are loaded here.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AstrometricsProvider } from '../common/context/AstrometricsContext';
import { TrayPopover } from './TrayPopover';
import '../common/styles/theme.css';
import './trayPopover.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
    },
  },
});

const rootElement = document.getElementById('tray-popover-root') as HTMLElement;

// Escape key dismisses the popover by sending a hide action through the IPC bridge.
document.addEventListener('keydown', (e: KeyboardEvent) => {
  if (e.key === 'Escape') {
    window.astrometrics?.tray?.sendAction('hide-popover');
  }
});

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AstrometricsProvider>
        <TrayPopover />
      </AstrometricsProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
