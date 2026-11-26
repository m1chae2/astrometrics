/**
 * @file CodeEditor.tsx
 * @description The Command Console's code editor, built on CodeMirror 6 with Python colors.
 * Ctrl+Enter (Cmd+Enter on a Mac) runs the script. The Run and Save buttons live in the
 * console's toolbar, not here. Long lines wrap onto the next line, so a narrow window
 * needs no sideways scrolling.
 */
import React from 'react';
import CodeMirror, { EditorView } from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';

/** The editor's extensions: Python colors, and long lines wrapped to the editor's width. */
const EDITOR_EXTENSIONS = [python(), EditorView.lineWrapping];

/** Props for CodeEditor. */
interface CodeEditorProps {
  /** The code shown in the editor. */
  value: string;
  /** Called with the new code after each change. */
  onChange: (value: string) => void;
  /** Called when you press Ctrl+Enter, to run the script. */
  onRun: () => void;
}

/**
 * Shows the code editor.
 *
 * @param props See `CodeEditorProps`.
 * @returns The editor, filling its panel.
 */
export const CodeEditor: React.FC<CodeEditorProps> = ({ value, onChange, onRun }) => {
  /**
   * Runs the script on Ctrl+Enter (Cmd+Enter on a Mac). The key is caught on its way down to
   * the editor, so CodeMirror does not also add a blank line.
   *
   * @param event The key press.
   */
  const handleKeyDownCapture = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      event.stopPropagation();
      onRun();
    }
  };

  return (
    <div className="code-editor-pane" onKeyDownCapture={handleKeyDownCapture}>
      <CodeMirror
        value={value}
        height="100%"
        theme="dark"
        extensions={EDITOR_EXTENSIONS}
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
  );
};
