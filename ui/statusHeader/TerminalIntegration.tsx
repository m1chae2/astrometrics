import React, { useEffect, useRef } from 'react';
import { useTerminal } from './settings/hooks/useTerminal';
import { Button } from '../common/components/Button';
import { FileInput } from '../common/components/FileInput';
import { TextInput } from '../common/components/TextInput';

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
                <span className="terminal-integration__hint">
                    Access backend services via: telescope, camera, target, etc.
                </span>
                <div className="terminal-integration__actions">
                    <FileInput
                        ref={fileInputRef}
                        className="terminal-integration__file-input"
                        accept=".py"
                        onChange={handleLoadScript}
                    />
                    <Button
                        variant="secondary"
                        size="small"
                        onClick={() => fileInputRef.current?.click()}
                    >
                        Load Script
                    </Button>
                    <Button
                        variant="secondary"
                        size="small"
                        onClick={() => sendCommand('list_commands()')}
                    >
                        List Commands
                    </Button>
                    {onClose && (
                        <Button variant="secondary" size="small" onClick={onClose}>Close</Button>
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
                <TextInput
                    ref={inputRef}
                    className="terminal-integration__input"
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
