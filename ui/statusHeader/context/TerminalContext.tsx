import React, { createContext, useContext, useState, useEffect, useRef, useCallback, ReactNode } from 'react';
import { getBackendBase, withSessionToken } from '../../common/services/backendApi';
import { fetchSystemCompletions } from '../../common/services/systemService';
import { useToast } from '../../common/hooks/useToast';
import { on as onEvent } from '../../common/utils/eventBus';

interface TerminalContextValue {
    output: string[];
    input: string;
    setInput: React.Dispatch<React.SetStateAction<string>>;
    handleKeyDown: (e: React.KeyboardEvent) => void;
    handleLoadScript: (e: React.ChangeEvent<HTMLInputElement>) => void;
    sendCommand: (cmd: string) => void;
}

const TerminalContext = createContext<TerminalContextValue | undefined>(undefined);

export const TerminalProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [output, setOutput] = useState<string[]>([]);
    const [input, setInput] = useState('');
    const [history, setHistory] = useState<string[]>([]);
    const [historyIndex, setHistoryIndex] = useState(-1);

    const wsRef = useRef<WebSocket | null>(null);
    const lastTabTime = useRef<number>(0);
    const { show: showToast } = useToast();

    // WebSocket Setup
    useEffect(() => {
        let cancelled = false;
        let ws: WebSocket | null = null;

        // Defer connection by one tick so React StrictMode's immediate cleanup
        // can set `cancelled = true` and clear the timer before the socket is
        // created. On the real mount the timeout fires and we connect normally.
        const timerId = setTimeout(async () => {
            if (cancelled) return;

            let url = getBackendBase();
            if (!url) url = 'http://127.0.0.1:5000';
            url = url.replace(/^http/, 'ws');
            url = url.replace(/\/$/, '');
            url += '/ws/terminal';

            // The backend rejects handshakes without the session token.
            url = await withSessionToken(url);

            // Resolving the token is async, so the effect may have been torn
            // down (StrictMode remount, navigation) while it was in flight.
            if (cancelled) return;

            try {
                ws = new WebSocket(url);
                wsRef.current = ws;

                ws.onopen = () => {
                    setOutput((prev) => [...prev, '>>> Connected to Astrometrics Terminal']);
                };

                ws.onmessage = (event) => {
                    const msg = event.data;
                    if (msg.startsWith('Traceback')) {
                        showToast('Script Error', 'error');
                    }
                    setOutput((prev) => [...prev, msg]);
                };

                ws.onclose = (event) => {
                    // Avoid logging simple disconnects during development hot-reloads
                    if (event.code !== 1000 && event.code !== 1001) {
                        setOutput((prev) => [...prev, '>>> Disconnected']);
                    }
                };

                ws.onerror = () => {
                    if (ws && (ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED)) {
                        return;
                    }
                    setOutput((prev) => [...prev, '>>> Connection Error']);
                };
            } catch (e) {
                console.error(e);
                setOutput((prev) => [...prev, '>>> Failed to connect']);
            }
        }, 0);

        return () => {
            cancelled = true;
            clearTimeout(timerId);
            if (ws) {
                ws.onopen = null;
                ws.onmessage = null;
                ws.onclose = null;
                ws.onerror = null;
                if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                    ws.close();
                }
            }
        };
    }, [showToast]);

    // External Event Listener for piping logs
    useEffect(() => {
        const detach = onEvent('terminal:log', (payload: unknown) => {
            const msg = typeof payload === 'string' ? payload : JSON.stringify(payload);
            setOutput(prev => [...prev, msg]);
        });
        return () => detach();
    }, []);

    const sendCommand = useCallback((cmd: string) => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        wsRef.current.send(cmd);
        setOutput((prev) => [...prev, `> ${cmd}`]);
    }, []);

    // Completion Logic
    const longestCommonPrefix = (strs: string[]): string => {
        if (!strs.length) return '';
        let prefix = strs[0];
        for (let i = 1; i < strs.length; i++) {
            while (strs[i].indexOf(prefix) !== 0) {
                prefix = prefix.substring(0, prefix.length - 1);
                if (!prefix) return '';
            }
        }
        return prefix;
    };

    const handleKeyDown = async (e: React.KeyboardEvent) => {
        if (e.key === 'Tab') {
            e.preventDefault();
            if (!input || !input.trim()) return;

            const now = Date.now();
            const isDoubleTab = (now - lastTabTime.current) < 500;
            lastTabTime.current = now;

            try {
                const completions = await fetchSystemCompletions(input);
                if (completions.length === 1) {
                    setInput(completions[0]);
                } else if (completions.length > 1) {
                    if (isDoubleTab) {
                        const lcp = longestCommonPrefix(completions);
                        if (lcp && lcp.length > input.length) {
                            setInput(lcp);
                        }
                    } else {
                        setOutput(prev => [...prev, `> ${input}`, ...completions]);
                    }
                }
            } catch (err) {
                console.error("Completion failed", err);
            }
            return;
        }

        if (e.key === 'Enter') {
            if (!input || !input.trim()) return;

            if (input && input.trim() === 'clear') {
                setOutput([]);
                setInput('');
                setHistory((prev) => [...prev, input]);
                setHistoryIndex(-1);
                return;
            }

            sendCommand(input);
            setHistory((prev) => [...prev, input]);
            setHistoryIndex(-1);
            setInput('');
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (history.length > 0) {
                const idx = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
                setHistoryIndex(idx);
                setInput(history[idx]);
            }
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (historyIndex !== -1) {
                const idx = Math.min(history.length - 1, historyIndex + 1);
                if (historyIndex === history.length - 1) {
                    setHistoryIndex(-1);
                    setInput('');
                } else {
                    setHistoryIndex(idx);
                    setInput(history[idx]);
                }
            }
        }
    };

    const handleLoadScript = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (evt) => {
            const content = evt.target?.result as string;
            if (content) {
                sendCommand(content);
                showToast(`Loaded script: ${file.name}`, 'success');
            }
        };
        reader.readAsText(file);
        e.target.value = '';
    };

    const value = {
        output,
        input,
        setInput,
        handleKeyDown,
        handleLoadScript,
        sendCommand
    };

    return (
        <TerminalContext.Provider value={value}>
            {children}
        </TerminalContext.Provider>
    );
};

export const useTerminalContext = () => {
    const context = useContext(TerminalContext);
    if (!context) {
        throw new Error('useTerminalContext must be used within a TerminalProvider');
    }
    return context;
};
