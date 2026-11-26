/**
 * @file TerminalPane.tsx
 * @description The Command Console's terminal: runs one Python line at a time. It shows one
 * short line about the commands, the history of commands and output, Tab completion, and a
 * badge on lines an AI agent ran. While code runs, a line at the bottom names what runs and how
 * long it has run; Ctrl+C anywhere in the terminal stops it.
 */
import React, { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { callBackend } from '../common/services/backendApi';
import { TextInput } from '../common/components/TextInput';
import { TERMINAL_INTRO, formatElapsed } from './terminalCommands';

/** How often the running line updates its elapsed time, in milliseconds. */
const ELAPSED_TICK_MS = 1000;

/** One command in the terminal history, with its output. */
export interface TerminalEntry {
  id: string;
  command: string;
  stdout?: string;
  stderr?: string;
  source?: 'user' | 'agent' | 'system';
  executionTimeMs?: number;
}

/** The code running now, as the running line shows it. */
export interface RunningCode {
  /** What runs: the script's name, or "command" for a typed line. */
  label: string;
  /** When it started, in milliseconds since 1970 (`Date.now()`). */
  startedAt: number;
}

/** Props for TerminalPane. */
interface TerminalPaneProps {
  /** The commands run so far and their output, oldest first. */
  entries: TerminalEntry[];
  /** Called with a line you typed and pressed Enter on. */
  onExecute: (command: string) => void;
  /** The code running now, or null. While it runs, Enter does not send another line. */
  running?: RunningCode | null;
  /** Called on Ctrl+C while code runs, to stop it. */
  onStop?: () => void;
}

/**
 * Shows the running line: what runs, for how long, and how to stop it.
 *
 * @param props The running code.
 * @returns The line, updated every second.
 */
const RunningLine: React.FC<{ running: RunningCode }> = ({ running }) => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="terminal-running" role="status">
      <span className="terminal-running__dot" aria-hidden="true" />
      <span className="terminal-running__label">Running {running.label}</span>
      <span className="terminal-running__time">{formatElapsed(now - running.startedAt)}</span>
      <span className="terminal-running__hint">Ctrl+C to stop</span>
    </div>
  );
};

/**
 * Returns the longest start that every string in `values` shares.
 *
 * @param values The strings to compare.
 * @returns The shared start, or an empty string.
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

/**
 * Shows the terminal: a banner, the history of commands and output, and the entry line.
 *
 * @param props See `TerminalPaneProps`.
 * @returns The terminal.
 */
export const TerminalPane: React.FC<TerminalPaneProps> = ({
  entries,
  onExecute,
  running = null,
  onStop,
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
      // One run at a time: the line stays in the box until the running code ends.
      if (!trimmed || running) return;

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

  /**
   * Stops the running code on Ctrl+C, anywhere in the terminal. With text selected, Ctrl+C
   * copies it as usual instead.
   *
   * @param event The key press.
   */
  const handlePaneKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!running || !event.ctrlKey || event.key.toLowerCase() !== 'c') return;
    const pageSelection = window.getSelection?.()?.toString() ?? '';
    const target = event.target as HTMLInputElement;
    const inputSelection = target === inputRef.current && target.selectionStart !== target.selectionEnd;
    if (pageSelection || inputSelection) return;
    event.preventDefault();
    onStop?.();
  };

  return (
    <div className="terminal-pane" onClick={() => inputRef.current?.focus()} onKeyDown={handlePaneKeyDown}>
      <div className="terminal-intro">{TERMINAL_INTRO}</div>

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
      </div>

      {running && <RunningLine running={running} />}

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
        <TextInput
          ref={inputRef}
          appearance="inline"
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
