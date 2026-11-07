/**
 * @file CodeEditor.tsx
 * @description Code editor component built on CodeMirror 6 with Python syntax support and action toolbar.
 */
import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';
import { Button } from '../common/components/Button';

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  onSave?: () => void;
  onClear?: () => void;
  filename?: string;
  isRunning?: boolean;
}

export const CodeEditor: React.FC<CodeEditorProps> = ({
  value,
  onChange,
  onRun,
  onSave,
  onClear,
  filename = 'untitled.py',
  isRunning = false,
}) => {
  return (
    <div className="code-editor-pane">
      <div className="code-editor-toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {filename}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          {onClear && (
            <Button variant="secondary" size="small" onClick={onClear}>
              Clear
            </Button>
          )}
          {onSave && (
            <Button variant="secondary" size="small" onClick={onSave}>
              Save Script
            </Button>
          )}
          <Button
            variant="primary"
            size="small"
            onClick={onRun}
            disabled={isRunning}
          >
            {isRunning ? 'Running...' : 'Run Script (Ctrl+Enter)'}
          </Button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        <CodeMirror
          value={value}
          height="100%"
          theme="dark"
          extensions={[python()]}
          onChange={(val) => onChange(val)}
          basicSetup={{
            lineNumbers: true,
            highlightActiveLineGutter: true,
            highlightSpecialChars: true,
            history: true,
            foldGutter: true,
            drawSelection: true,
            dropCursor: true,
            allowMultipleSelections: true,
            indentOnInput: true,
            syntaxHighlighting: true,
            bracketMatching: true,
            closeBrackets: true,
            autocompletion: true,
            rectangularSelection: true,
            crosshairCursor: true,
            highlightActiveLine: true,
            highlightSelectionMatches: true,
            closeBracketsKeymap: true,
            defaultKeymap: true,
            searchKeymap: true,
            historyKeymap: true,
            foldKeymap: true,
            completionKeymap: true,
            lintKeymap: true,
          }}
        />
      </div>
    </div>
  );
};
