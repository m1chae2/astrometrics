// Lightweight Plot utilities used by multiple viewers
// Exported to keep Plotly-related plumbing in one place.
export type PlotlyTrace = {
  x: number[];
  y: number[];
  // 'scattergl' hands rendering to WebGL (via Plotly's regl-based gl2d
  // pipeline) instead of SVG. Do not use it: regl compiles attribute-setter
  // functions with new Function()/eval() at runtime, which the app's CSP
  // (script-src with no 'unsafe-eval', see electron/main.js) blocks outright
  // -- the trace silently falls back to Plotly's "WebGL is not supported"
  // placeholder instead of rendering. Use 'scatter' (SVG) for everything.
  type?: 'scatter' | 'scattergl';
  mode?: 'lines' | 'markers' | 'lines+markers';
  line?: { color?: string; width?: number; shape?: string; dash?: string };
  marker?: { color?: string; size?: number; symbol?: string };
  hoverinfo?: string;
  showlegend?: boolean;
  name?: string;
};


export type PlotlyLayout = {
  autosize?: boolean;
  paper_bgcolor?: string;
  plot_bgcolor?: string;
  font?: { color?: string; size?: number };
  xaxis?: {
    title?: { text: string; font?: { size?: number } };
    color?: string;
    gridcolor?: string;
    zerolinecolor?: string;
    tickfont?: { size?: number };
    automargin?: boolean;
  };
  yaxis?: {
    title?: { text: string; font?: { size?: number } };
    color?: string;
    gridcolor?: string;
    zerolinecolor?: string;
    tickfont?: { size?: number };
    automargin?: boolean;
    autorange?: 'reversed' | boolean;
  };
  margin?: { l?: number; r?: number; t?: number; b?: number };
  showlegend?: boolean;
  hovermode?: string;
  shapes?: any[];
};

export type PlotlyStatic = {
  newPlot(node: HTMLElement, data: PlotlyTrace[], layout?: PlotlyLayout, config?: unknown): Promise<unknown>;
  react(node: HTMLElement, data: PlotlyTrace[], layout?: PlotlyLayout, config?: unknown): Promise<unknown>;
  addTraces(node: HTMLElement, trace: PlotlyTrace | PlotlyTrace[]): Promise<unknown>;
  deleteTraces(node: HTMLElement, inds: number | number[]): Promise<unknown>;
  purge(node: HTMLElement): void;
};

export type PlotNodeLike = {
  data?: Array<{ name?: string }>;
  on?: (ev: string, cb: (e: unknown) => void) => void;
  removeListener?: (ev: string, cb: (e: unknown) => void) => void;
  addEventListener?: (ev: string, cb: (e: unknown) => void) => void;
  removeEventListener?: (ev: string, cb: (e: unknown) => void) => void;
};

export async function loadPlotly(): Promise<PlotlyStatic> {
  // Plotly is loaded via <script> tag in index.html to avoid massive build times
  // with Vite/Rollup transforming the minified bundle.
  // We wait for it to be available on window.

  if (typeof window === 'undefined') {
    throw new Error('loadPlotly can only be run in a browser environment');
  }

  if ((window as any).Plotly) {
    console.info('Plotly loaded from global window object (cached).');
    return (window as any).Plotly;
  }

  // Poll for it just in case it's racing (unlikely with header script)
  console.info('Waiting for Plotly to be available on window object...');
  return new Promise((resolve, reject) => {
    let retries = 0;
    const interval = setInterval(() => {
      if (typeof window === 'undefined') {
        clearInterval(interval);
        reject(new Error('Window environment torn down before Plotly loaded.'));
        return;
      }
      if ((window as any).Plotly) {
        clearInterval(interval);
        console.info(`Plotly loaded successfully after ${retries * 100}ms.`);
        resolve((window as any).Plotly);
      }
      retries++;
      if (retries > 50) { // 5 seconds
        clearInterval(interval);
        console.error('Plotly failed to load from global window object after 5 seconds.');
        reject(new Error('Plotly failed to load from global window object.'));
      }
    }, 100);
  });
}

export const getVar = (name: string, fallback: string) => {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name);
    const v = (raw || '').trim();
    return v || fallback;
  } catch {
    return fallback;
  }
};

export const getVarAsNum = (name: string, fallback: number) => {
  const val = getVar(name, '');
  if (!val) return fallback;
  const parsed = parseInt(val, 10);
  return isNaN(parsed) ? fallback : parsed;
};

// Shared layout builder matching "Guiding Trends" / "Control Panel" dark style
export const buildLayout = (overrides?: {
  xTitle?: string;
  yTitle?: string;
  xTitleFont?: any;
  yTitleFont?: any;
  margin?: any;
  autosize?: boolean;
}): PlotlyLayout => {
  const commonFontSize = getVarAsNum('--label-font-size', 12);
  const titleFontSize = commonFontSize;
  const tickFontSize = commonFontSize - 1;

  return {
    autosize: overrides?.autosize ?? true,
    paper_bgcolor: 'rgba(0,0,0,0)', // Transparent to blend with panel
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: getVar('--plot-font', '#fff'), size: commonFontSize },
    xaxis: {
      title: { text: overrides?.xTitle ?? 'Val', font: { size: titleFontSize, ...overrides?.xTitleFont } },
      color: getVar('--plot-font', '#ffffff'),
      gridcolor: '#444',
      zerolinecolor: '#444',
      tickfont: { size: tickFontSize },
      automargin: true
    },
    yaxis: {
      title: { text: overrides?.yTitle ?? 'Val', font: { size: titleFontSize, ...overrides?.yTitleFont } },
      color: getVar('--plot-font', '#ffffff'),
      gridcolor: '#444',
      zerolinecolor: '#444',
      tickfont: { size: tickFontSize },
      automargin: true
    },
    margin: { l: 60, r: 20, t: 20, b: 50, ...overrides?.margin },
    showlegend: false,
    hovermode: 'closest'
  };
};

// Clear selection traces whose trace.name begins with `__selection__:`.
export const clearPlotSelectionTraces = (node: HTMLDivElement | null, Plotly: PlotlyStatic | null, layout?: unknown, config?: unknown) => {
  if (!node || !Plotly) return;
  try {
    const plotNode = node as unknown as PlotNodeLike;
    if (!plotNode.data || plotNode.data.length === 0) return;
    const toDelete: number[] = [];
    plotNode.data.forEach((t: { name?: string } | undefined, i: number) => {
      if (t && typeof t.name === 'string' && t.name.startsWith('__selection__:')) toDelete.push(i);
    });
    if (toDelete.length === 0) return;
    try {
      const remaining = (plotNode.data || []).filter((t) => !(t && typeof t.name === 'string' && t.name.startsWith('__selection__:')));
      Plotly.react(node, remaining as unknown as PlotlyTrace[], layout ?? {}, config ?? {}).catch(() => {
        try { Plotly.deleteTraces(node, toDelete).catch(() => { }); } catch { /* ignore */ }
      });
    } catch {
      try { Plotly.deleteTraces(node, toDelete).catch(() => { }); } catch { /* ignore */ }
    }
  } catch { /* ignore */ }
};

// clickHandlerRef is an object with a `current` property that stores the last attached handler.
export const attachClickHandler = (node: HTMLDivElement | null, onClick: (e: unknown) => void, clickHandlerRef?: { current: ((e: unknown) => void) | null } | null) => {
  if (!node) return;
  try {
    const plotNode = node as unknown as PlotNodeLike;
    if (clickHandlerRef && clickHandlerRef.current) {
      try {
        const prev = clickHandlerRef.current as (e: unknown) => void;
        if (typeof plotNode.removeListener === 'function') plotNode.removeListener('plotly_click', prev);
        else if (plotNode.removeEventListener) plotNode.removeEventListener('plotly_click', prev);
      } catch { /* ignore */ }
    }
    if (typeof plotNode.on === 'function') plotNode.on('plotly_click', onClick);
    else if (plotNode.addEventListener) plotNode.addEventListener('plotly_click', onClick);
    if (clickHandlerRef) clickHandlerRef.current = onClick;
  } catch { /* ignore */ }
};

export const detachClickHandler = (node: HTMLDivElement | null, clickHandlerRef?: { current: ((e: unknown) => void) | null } | null) => {
  if (!node) return;
  try {
    const plotNode = node as unknown as PlotNodeLike;
    if (clickHandlerRef && clickHandlerRef.current) {
      const prev = clickHandlerRef.current as (e: unknown) => void;
      try { if (typeof plotNode.removeListener === 'function') plotNode.removeListener('plotly_click', prev); else if (plotNode.removeEventListener) plotNode.removeEventListener('plotly_click', prev); } catch { /* ignore */ }
      if (clickHandlerRef) clickHandlerRef.current = null;
    }
  } catch { /* ignore */ }
};

export const findNearestIndex = (arr: number[], v: number) => {
  if (!arr || arr.length === 0) return -1;
  let lo = 0; let hi = arr.length - 1;
  if (v <= arr[0]) return 0;
  if (v >= arr[hi]) return hi;
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    if (arr[mid] === v) return mid;
    if (arr[mid] < v) lo = mid; else hi = mid;
  }
  return (Math.abs(arr[lo] - v) <= Math.abs(arr[hi] - v)) ? lo : hi;
};

export const getSelectionTraceName = (id: string) => `__selection__:${id}`;

export const isSelectionTraceName = (name?: string) => typeof name === 'string' && name.startsWith('__selection__:');

export const computeTooltipPosition = (container: HTMLElement | null, clientX?: number, clientY?: number) => {
  let left = 8; let top = 8;
  if (container && clientX && clientY) {
    const rect = container.getBoundingClientRect();
    left = clientX - rect.left + 8;
    top = clientY - rect.top - 28;
  }
  return { left: Math.max(4, left), top: Math.max(4, top) };
};
