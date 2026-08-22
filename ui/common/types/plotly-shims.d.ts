declare module 'plotly.js-dist-min' {
  const Plotly: unknown;
  export default Plotly;
}

declare module 'plotly.js' {
  const Plotly: unknown;
  export default Plotly;
}

// Also expose a minimal global Plotly namespace for code that uses Plotly.*
declare namespace Plotly {
  function newPlot(node: HTMLElement, data: unknown[], layout?: unknown, config?: unknown): Promise<unknown>;
  function react(node: HTMLElement, data: unknown[], layout?: unknown, config?: unknown): Promise<unknown>;
  function addTraces(node: HTMLElement, trace: unknown): Promise<unknown>;
  function deleteTraces(node: HTMLElement, inds: number | number[]): Promise<unknown>;
  const Fx: unknown;
}
