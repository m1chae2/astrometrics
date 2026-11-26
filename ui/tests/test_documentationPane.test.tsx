/**
 * @file test_documentationPane.test.tsx
 * @description Tests of the Command Console's Documentation tab: the API entry anchors, find-in-page
 * (Ctrl+F) with its "n of m" count, Enter and Shift+Enter, and Escape, the key staying with other
 * panels, and opening one entry with `focusEntry`.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { DocumentationPane, findTextRanges, type DocFocusEntry } from '../commandConsole/DocumentationPane';
import { splitEntryAnchor, type DocTopicItem } from '../commandConsole/DocViewer';

/** Two API topics, enough for the picker and for opening an entry. */
const TOPICS: DocTopicItem[] = [
  { id: 'api/astrometricslib', title: 'Astrometrics Library', path: 'api/astrometricslib.rst', category: 'API Reference' },
  { id: 'api/wayfindinglib', title: 'Wayfinding Library', path: 'api/wayfindinglib.rst', category: 'API Reference' },
];

/** A page like the generated reference: a class heading, two method headings and a module list. */
const PAGE = [
  '# Astrometrics Library',
  '',
  '### `TargetCatalog` {#api-TargetCatalog}',
  '',
  'The target catalog. Use get to read a target.',
  '',
  '#### `TargetCatalog.get(self, target_id: str) -> Target | None` {#api-TargetCatalog-get}',
  '',
  'Read one target.',
  '',
  '#### `TargetCatalog.delete_images(self, paths: list[str]) -> ImageDeletionResult` {#api-TargetCatalog-delete_images}',
  '',
  'Delete files from disk.',
  '',
  '- **`NotFoundError`**: Raised when a name is not found. {#api-exceptions-NotFoundError}',
].join('\n');

/**
 * Draws the Documentation tab with the sample page.
 *
 * @param overrides Props to change.
 * @returns The render result.
 */
function renderPane(overrides: Partial<React.ComponentProps<typeof DocumentationPane>> = {}) {
  const props: React.ComponentProps<typeof DocumentationPane> = {
    topics: TOPICS,
    selectedTopicId: 'api/astrometricslib',
    content: PAGE,
    loading: false,
    onSelectTopic: vi.fn(),
    ...overrides,
  };
  return { ...render(<DocumentationPane {...props} />), props };
}

/**
 * Presses Ctrl+F on the document, as the browser sends it.
 */
function pressControlF() {
  fireEvent.keyDown(document, { key: 'f', ctrlKey: true });
}

describe('DocumentationPane', () => {
  let scrollSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy as unknown as Element['scrollIntoView'];
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('gives every reference entry its anchor as an id and hides the mark', () => {
    const { container } = renderPane();
    const ids = Array.from(container.querySelectorAll('[id^="api-"]')).map((element) => element.id);

    expect(ids).toEqual(['api-TargetCatalog', 'api-TargetCatalog-get', 'api-TargetCatalog-delete_images', 'api-exceptions-NotFoundError']);
    expect(new Set(ids).size).toBe(ids.length);
    expect(container.textContent).not.toContain('{#');
    expect(container.querySelector('#api-TargetCatalog-get')?.tagName).toBe('H4');
    expect(container.querySelector('#api-exceptions-NotFoundError')?.tagName).toBe('LI');
  });

  it('takes the anchor mark off only when it ends the text', () => {
    expect(splitEntryAnchor(['text ', 'more {#api-A-b}'])).toEqual({ anchorId: 'api-A-b', children: ['text ', 'more'] });
    expect(splitEntryAnchor('no mark here').anchorId).toBeNull();
    expect(splitEntryAnchor('a {#api-A-b} in the middle').anchorId).toBeNull();
  });

  it('finds matches across text and code pieces, ignoring case', () => {
    const root = document.createElement('div');
    root.innerHTML = '<p>Target <code>catalog</code> and target</p>';

    expect(findTextRanges(root, 'TARGET')).toHaveLength(2);
    expect(findTextRanges(root, 'get cat')).toHaveLength(1);
    expect(findTextRanges(root, '  ')).toHaveLength(0);
  });

  it('opens find-in-page on Ctrl+F and counts the matches in the open topic', () => {
    renderPane();
    pressControlF();

    const field = screen.getByRole('searchbox', { name: 'Find in this topic' });
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: 'target' } });

    // "TargetCatalog" in four headings, "target" twice in the class text and once in "Read one target", and "target_id".
    const status = screen.getByText(/of \d+$/);
    const total = Number(status.textContent?.split(' of ')[1]);
    expect(total).toBeGreaterThan(3);
    expect(status.textContent).toBe(`1 of ${total}`);
    expect(scrollSpy).toHaveBeenCalled();
    expect(screen.getByRole('searchbox', { name: 'Search all documentation topics' })).toHaveAttribute('placeholder', 'Search all topics...');
  });

  it('moves with Enter and Shift+Enter, going round at the ends', () => {
    renderPane();
    pressControlF();
    const field = screen.getByRole('searchbox', { name: 'Find in this topic' });
    fireEvent.change(field, { target: { value: 'delete' } });

    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
    expect(screen.getByText('2 of 2')).toBeInTheDocument();

    fireEvent.change(field, { target: { value: 'nothing like this' } });
    expect(screen.getByText('No matches')).toBeInTheDocument();
  });

  it('marks the matches with the highlight API when the browser has it', () => {
    const registry = new Map<string, unknown>();
    vi.stubGlobal('Highlight', class { ranges: Range[]; constructor(...ranges: Range[]) { this.ranges = ranges; } });
    vi.stubGlobal('CSS', { highlights: registry });
    try {
      renderPane();
      pressControlF();
      fireEvent.change(screen.getByRole('searchbox', { name: 'Find in this topic' }), { target: { value: 'delete' } });

      expect((registry.get('doc-find-match') as { ranges: Range[] }).ranges).toHaveLength(2);
      expect((registry.get('doc-find-current') as { ranges: Range[] }).ranges).toHaveLength(1);

      fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Find in this topic' }), { key: 'Escape' });
      expect(registry.size).toBe(0);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('closes and clears on Escape', () => {
    renderPane();
    pressControlF();
    const field = screen.getByRole('searchbox', { name: 'Find in this topic' });
    fireEvent.change(field, { target: { value: 'target' } });
    fireEvent.keyDown(field, { key: 'Escape' });

    expect(screen.queryByRole('searchbox', { name: 'Find in this topic' })).not.toBeInTheDocument();
    pressControlF();
    expect(screen.getByRole('searchbox', { name: 'Find in this topic' })).toHaveValue('');
  });

  it('leaves Ctrl+F alone while another panel, such as the editor, has the focus', () => {
    renderPane();
    const editor = document.createElement('textarea');
    document.body.appendChild(editor);
    editor.focus();
    try {
      pressControlF();
      expect(screen.queryByRole('searchbox', { name: 'Find in this topic' })).not.toBeInTheDocument();
    } finally {
      editor.remove();
    }
  });

  it('opens the topic of a focusEntry request, scrolls to the entry and lights it up for a moment', () => {
    vi.useFakeTimers();
    const onSelectTopic = vi.fn();
    const entry: DocFocusEntry = { topicId: 'api/astrometricslib', anchorId: 'api-TargetCatalog-delete_images', nonce: 1 };
    const { rerender, container, props } = renderPane({ selectedTopicId: 'api/wayfindinglib', content: '# Wayfinding', onSelectTopic, focusEntry: entry });

    expect(onSelectTopic).toHaveBeenCalledWith('api/astrometricslib');
    rerender(<DocumentationPane {...props} selectedTopicId="api/astrometricslib" content={PAGE} />);

    const target = container.querySelector('#api-TargetCatalog-delete_images');
    expect(scrollSpy.mock.contexts).toContain(target);
    expect(target).toHaveClass('doc-viewer__entry--flash');

    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(target).not.toHaveClass('doc-viewer__entry--flash');

    // The same entry again, with a new nonce, lights it up again.
    rerender(<DocumentationPane {...props} selectedTopicId="api/astrometricslib" content={PAGE} focusEntry={{ ...entry, nonce: 2 }} />);
    expect(container.querySelector('#api-TargetCatalog-delete_images')).toHaveClass('doc-viewer__entry--flash');
  });
});
