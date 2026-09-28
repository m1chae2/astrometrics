/**
 * @file TerminalPane.tsx
 * @description Interactive Python REPL terminal pane with discovery banner, command history, tab completion, and agent badges.
 */
import React, { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { callBackend } from '../common/services/backendApi';

export interface TerminalEntry {
  id: string;
  command: string;
  stdout?: string;
  stderr?: string;
  source?: 'user' | 'agent' | 'system';
  executionTimeMs?: number;
}

interface TerminalPaneProps {
  entries: TerminalEntry[];
  onExecute: (command: string) => void;
  onClear?: () => void;
  onReset?: () => void;
  isRunning?: boolean;
}

/**
 * Return the longest prefix shared by every string in `values`.
 */
function longestCommonPrefix(values: string[]): string {
  if (values.length === 0) return '';
  let prefix = values[0];
  for (const value of values.slice(1)) {
    while (!value.startsWith(prefix)) {
      prefix = prefix.slice(0, -1);
      if (!prefix) return '';
    }
  }
  return prefix;
}

export const TerminalPane: React.FC<TerminalPaneProps> = ({
  entries,
  onExecute,
  onClear,
  onReset,
  isRunning = false,
}) => {
  const [input, setInput] = useState('');
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [completionSuggestions, setCompletionSuggestions] = useState<string[]>([]);
  const commandHistoryRef = useRef<string[]>([]);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [entries]);

  const handleKeyDown = async (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed) return;

      commandHistoryRef.current.push(trimmed);
      setHistoryIndex(null);
      setInput('');
      setCompletionSuggestions([]);
      onExecute(trimmed);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const history = commandHistoryRef.current;
      if (history.length === 0) return;

      const nextIndex = historyIndex === null ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(nextIndex);
      setInput(history[nextIndex]);
      setCompletionSuggestions([]);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const history = commandHistoryRef.current;
      if (historyIndex === null) return;

      const nextIndex = historyIndex + 1;
      if (nextIndex >= history.length) {
        setHistoryIndex(null);
        setInput('');
      } else {
        setHistoryIndex(nextIndex);
        setInput(history[nextIndex]);
      }
      setCompletionSuggestions([]);
    } else if (e.key === 'Tab') {
      e.preventDefault();
      if (!input.trim()) return;
      try {
        const completions = await callBackend('terminal:completions', { text: input });
        if (!completions || completions.length === 0) {
          setCompletionSuggestions([]);
        } else if (completions.length === 1) {
          setInput(completions[0]);
          setCompletionSuggestions([]);
        } else {
          // Multiple matches: complete as far as they agree (like a shell)
          // and list the rest so another Tab (or a click) can pick one.
          const commonPrefix = longestCommonPrefix(completions);
          if (commonPrefix.length > input.length) {
            setInput(commonPrefix);
          }
          setCompletionSuggestions(completions);
        }
      } catch (err) {
        console.warn('Tab completion failed:', err);
      }
    } else if (e.key === 'Escape') {
      setCompletionSuggestions([]);
    }
  };

  return (
    <div className="terminal-pane" onClick={() => inputRef.current?.focus()}>
      <div className="terminal-banner">
        <div className="terminal-banner__title">Astrometrics Command Console (Python 3.14)</div>
        <div>Scientific environment connected to observatory hardware & processing pipelines.</div>
        <div className="terminal-banner__cheatsheet">
          <div><code>targets</code>: Target catalog</div>
          <div><code>stars</code>: Stellar catalog</div>
          <div><code>jobs</code>: Job logs & metrics</div>
          <div><code>telescope</code>: Mount control</div>
          <div><code>imaging</code>: Camera sequencer</div>
          <div><code>np</code>: NumPy</div>
          <div><code>plt</code>: Matplotlib figure Agg</div>
          <div><code>help</code> / <code>doc(obj)</code></div>
        </div>
      </div>

      {(onClear || onReset) && (
        <div className="terminal-toolbar">
          {onClear && (
            <button
              className="editor-btn"
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              type="button"
              title="Clear the terminal history shown above (variables are kept)"
            >
              Clear
            </button>
          )}
          {onReset && (
            <button
              className="editor-btn"
              onClick={(e) => {
                e.stopPropagation();
                onReset();
              }}
              type="button"
              title="Discard console-local variables and restore built-in objects (history is kept)"
            >
              Reset Workspace
            </button>
          )}
        </div>
      )}

      <div className="terminal-history" ref={scrollContainerRef}>
        {entries.map((entry) => (
          <div key={entry.id} className="terminal-entry">
            <div className="terminal-entry__cmd">
              <span className="terminal-entry__prompt">&gt;&gt;&gt;</span>
              {entry.source === 'agent' && (
                <span className="terminal-entry__badge">[AI Agent]</span>
              )}
              <span>{entry.command}</span>
            </div>
            {entry.stdout && <div className="terminal-entry__stdout">{entry.stdout}</div>}
            {entry.stderr && <div className="terminal-entry__stderr">{entry.stderr}</div>}
          </div>
        ))}
        {isRunning && (
          <div className="terminal-entry">
            <div style={{ color: 'var(--text-dim)', fontStyle: 'italic', paddingLeft: '18px' }}>
              Executing command...
            </div>
          </div>
        )}
      </div>

      {completionSuggestions.length > 0 && (
        <div className="terminal-completions">
          {completionSuggestions.map((suggestion) => (
            <span
              key={suggestion}
              className="terminal-completions__item"
              onClick={() => {
                setInput(suggestion);
                setCompletionSuggestions([]);
                inputRef.current?.focus();
              }}
            >
              {suggestion}
            </span>
          ))}
        </div>
      )}

      <div className="terminal-input-bar">
        <span className="terminal-input-bar__prompt">&gt;&gt;&gt;</span>
        <input
          ref={inputRef}
          type="text"
          className="terminal-input-bar__input"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setCompletionSuggestions([]);
          }}
          onKeyDown={handleKeyDown}
          placeholder="Type Python expression or command... (Tab completes, Up/Down recalls history)"
          autoFocus
        />
      </div>
    </div>
  );
};
