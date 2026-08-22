import React, { useEffect, useRef } from 'react';
import { useTerminal } from './settings/hooks/useTerminal';

export interface TerminalProps {
    onClose?: () => void;
}

export interface TerminalRef {
    insertText: (text: string) => void;
}

/**
 * TerminalIntegration Component
 * Provides a Python-like terminal interface for system commands.
 */
export const TerminalIntegration = React.forwardRef<TerminalRef, TerminalProps>(({ onClose }, ref) => {
    const {
        output,
        input,
        setInput,
        handleKeyDown,
        handleLoadScript,
        sendCommand
    } = useTerminal();

    const outputRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    React.useImperativeHandle(ref, () => ({
        insertText: (text: string) => {
            setInput(prev => prev + text);
            inputRef.current?.focus();
        }
    }));

    useEffect(() => {
        if (outputRef.current) {
            outputRef.current.scrollTop = outputRef.current.scrollHeight;
        }
    }, [output]);

    return (
        <div className="terminal-integration">
            <div className="terminal-integration__header">
                <span>Python Terminal</span>
                <span style={{ fontSize: '0.8rem', opacity: 0.7, marginLeft: '1rem' }}>
                    Access backend services via: telescope, camera, target, etc.
                </span>
                <div className="terminal-integration__actions">
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="terminal-integration__file-input"
                        accept=".py"
                        onChange={handleLoadScript}
                    />
                    <button
                        className="terminal-integration__button"
                        onClick={() => fileInputRef.current?.click()}
                        type="button"
                    >
                        Load Script
                    </button>
                    <button
                        className="terminal-integration__button"
                        onClick={() => sendCommand('list_commands()')}
                        type="button"
                    >
                        List Commands
                    </button>
                    {onClose && (
                        <button className="terminal-integration__button" onClick={onClose}>Close</button>
                    )}
                </div>
            </div>
            <div
                ref={outputRef}
                className="terminal-integration__output"
            >
                {output.map((line, i) => (
                    <div key={i}>{line}</div>
                ))}
            </div>
            <div className="terminal-integration__input-container">
                <input
                    ref={inputRef}
                    className="terminal-integration__input"
                    type="text"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Type command..."
                    autoFocus
                />
            </div>
        </div>
    );
});
