// Entry point for the Astrometrics frontend application.
// This file bootstraps React and mounts the top-level <App /> into the
// DOM element with id="root". It intentionally keeps logic minimal so
// the app structure and routing live inside `src/App.tsx`.
import React from 'react';
import ReactDOM from 'react-dom/client';
import './common/styles/index.css';

import App from './App';
import { ToastProvider } from './common/components/ToastProvider';

import { ErrorBoundary } from './components/ErrorBoundary';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
