/**
 * @file DocumentationPane.tsx
 * @description The Command Console's Documentation tab. A compact toolbar holds a topic picker
 * (topics grouped by kind, such as the Python API reference) and a search box that looks
 * through the full text of every topic. While you search, the matches show as a list in place
 * of the page; picking one opens it.
 *
 * Ctrl+F (Cmd+F on a Mac) opens a find field that looks only through the open topic. It marks
 * every match, shows "n of m", and moves between matches with Enter and Shift+Enter. Escape
 * closes it. Another part of the console can also open a topic at one API entry (`focusEntry`).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { callBackend, type ActionRegistry } from '../common/services/backendApi';
import { PopoverSelect, type PopoverSelectOption } from '../common/components/PopoverSelect';
import { TextInput } from '../common/components/TextInput';
import { SelectableList, type SelectableItem } from '../common/components/SelectableList';
import { Button } from '../common/components/Button';
import { Toolbar, ToolbarGroup, ToolbarLabel } from '../common/components/Toolbar';
import { DocViewer, type DocTopicItem } from './DocViewer';
import { prefersReducedMotion } from '../common/utils/reducedMotion';
import './documentationPane.css';

/** One search match: a topic with a short piece of its text around the match. */
type DocSearchMatch = ActionRegistry['docs:search_topics']['response'][number];

/** How long to wait after the last key press before searching, in milliseconds. */
const SEARCH_DELAY_MS = 250;

/** How long an entry opened with `focusEntry` stays lit, in milliseconds. Matches the fade in documentationPane.css. */
const ENTRY_HIGHLIGHT_MS = 2000;

/** The name of the CSS highlight that marks every find-in-page match. */
const FIND_MATCH_HIGHLIGHT = 'doc-find-match';

/** The name of the CSS highlight that marks the current find-in-page match. */
const FIND_CURRENT_HIGHLIGHT = 'doc-find-current';

/** An API entry to open: the topic that holds it, the anchor id of the entry, and a number that changes on each request. */
export interface DocFocusEntry {
  /** The id of the topic that holds the entry, such as `api/astrometricslib`. */
  topicId: string;
  /** The anchor id of the entry, such as `api-TargetCatalog-delete_images`. */
  anchorId: string;
  /** A new number for each request, so asking for the same entry twice opens it again. */
  nonce: number;
}

/** The order of the topic groups in the picker. Topic kinds not named here come after them. */
const CATEGORY_ORDER = ['Python API Reference', 'Getting Started & Guides', 'Interactive Notebooks', 'Desktop Application', 'Architecture & Design'];

/** The group heading for each topic kind the backend reports. */
const CATEGORY_LABELS: Record<string, string> = {
  'API Reference': 'Python API Reference',
  General: 'Getting Started & Guides',
  'Desktop Application': 'Desktop Application',
  user_interface: 'Desktop Application',
  'Interactive Notebooks': 'Interactive Notebooks',
  notebooks: 'Interactive Notebooks',
  'Architecture & Design': 'Architecture & Design',
  library_design: 'Architecture & Design',
};

/**
 * Turns the topics into picker choices, grouped under headings in a fixed order.
 *
 * @param topics Every documentation topic.
 * @returns The picker choices, sorted by group.
 */
export function buildTopicOptions(topics: DocTopicItem[]): PopoverSelectOption[] {
  const groupOf = (topic: DocTopicItem) => CATEGORY_LABELS[topic.category || 'General'] ?? topic.category;
  const rank = (group: string) => {
    const index = CATEGORY_ORDER.indexOf(group);
    return index === -1 ? CATEGORY_ORDER.length : index;
  };
  return topics
    .map((topic, index) => ({ topic, index, group: groupOf(topic) }))
    .sort((first, second) => rank(first.group) - rank(second.group)
      || first.group.localeCompare(second.group)
      || first.index - second.index)
    .map(({ topic, group }) => ({ id: topic.id, label: topic.title, title: topic.path, group }));
}

/**
 * Finds every place a piece of text appears in the text of an element, ignoring case.
 * A match may run across several text pieces, such as plain text and code.
 *
 * @param root The element to look through.
 * @param query The text to find. Blank text finds nothing.
 * @returns One range for each match, in page order.
 */
export function findTextRanges(root: Node, query: string): Range[] {
  const wanted = query.toLowerCase();
  if (!wanted.trim()) return [];
  const pieces: { node: Text; start: number }[] = [];
  let fullText = '';
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    pieces.push({ node: text, start: fullText.length });
    fullText += text.data;
  }
  const lowered = fullText.toLowerCase();
  // Finds the text piece and the offset within it for a position in the joined text.
  const locate = (position: number, isEnd: boolean) => {
    let index = pieces.length - 1;
    while (index > 0 && (isEnd ? pieces[index].start >= position : pieces[index].start > position)) index -= 1;
    return { node: pieces[index].node, offset: position - pieces[index].start };
  };
  const ranges: Range[] = [];
  for (let found = lowered.indexOf(wanted); found !== -1; found = lowered.indexOf(wanted, found + wanted.length)) {
    const start = locate(found, false);
    const end = locate(found + wanted.length, true);
    const range = document.createRange();
    range.setStart(start.node, start.offset);
    range.setEnd(end.node, end.offset);
    ranges.push(range);
  }
  return ranges;
}

/**
 * Marks the find-in-page matches with the CSS Custom Highlight API, which colors text without
 * changing the page. Does nothing where the browser has no such API.
 *
 * @param ranges Every match.
 * @param current The index of the current match, or -1 for none.
 */
function paintFindHighlights(ranges: Range[], current: number): void {
  if (typeof CSS === 'undefined' || !('highlights' in CSS) || typeof Highlight === 'undefined') return;
  CSS.highlights.delete(FIND_MATCH_HIGHLIGHT);
  CSS.highlights.delete(FIND_CURRENT_HIGHLIGHT);
  if (ranges.length === 0) return;
  CSS.highlights.set(FIND_MATCH_HIGHLIGHT, new Highlight(...ranges));
  if (current >= 0 && current < ranges.length) {
    CSS.highlights.set(FIND_CURRENT_HIGHLIGHT, new Highlight(ranges[current]));
  }
}

/**
 * Scrolls an element into view, if the browser can.
 *
 * @param element The element to show.
 * @param block Where in the view to put it.
 */
function scrollElementIntoView(element: Element | null, block: ScrollLogicalPosition): void {
  if (element && typeof element.scrollIntoView === 'function') {
    element.scrollIntoView({ block, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }
}

/** Props for DocumentationPane. */
interface DocumentationPaneProps {
  /** Every documentation topic. */
  topics: DocTopicItem[];
  /** The id of the topic shown, or null before one is picked. */
  selectedTopicId: string | null;
  /** The text of the topic shown, in Markdown. */
  content: string;
  /** True while the topic is read. */
  loading: boolean;
  /** Called with the id of the topic to show. */
  onSelectTopic: (topicId: string) => void;
  /** An API entry to open, scroll to and light up for a moment. A new `nonce` asks again. */
  focusEntry?: DocFocusEntry;
}

/**
 * Shows the Documentation tab: the toolbar, the find field when it is open, then the search
 * matches or the topic's page.
 *
 * @param props See `DocumentationPaneProps`.
 * @returns The Documentation tab's contents.
 */
export const DocumentationPane: React.FC<DocumentationPaneProps> = ({
  topics,
  selectedTopicId,
  content,
  loading,
  onSelectTopic,
  focusEntry,
}) => {
  const [searchText, setSearchText] = useState('');
  const [searchMatches, setSearchMatches] = useState<DocSearchMatch[] | null>(null);
  const [findOpen, setFindOpen] = useState(false);
  const [findText, setFindText] = useState('');
  const [findRanges, setFindRanges] = useState<Range[]>([]);
  const [findIndex, setFindIndex] = useState(0);
  const [findFocusRequest, setFindFocusRequest] = useState(0);
  const [pendingEntry, setPendingEntry] = useState<DocFocusEntry | null>(null);
  const [highlightedAnchorId, setHighlightedAnchorId] = useState<string | null>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);
  const findInputRef = useRef<HTMLInputElement>(null);
  const topicOptions = useMemo(() => buildTopicOptions(topics), [topics]);

  // Searches the full text of every topic on the backend, a moment after the last key press.
  useEffect(() => {
    const query = searchText.trim();
    if (!query) {
      setSearchMatches(null);
      return;
    }
    let cancelled = false;
    setSearchMatches(null);
    const timer = setTimeout(() => {
      callBackend('docs:search_topics', { query })
        .then((matches) => {
          if (!cancelled) setSearchMatches(matches || []);
        })
        .catch(() => {
          if (!cancelled) setSearchMatches([]);
        });
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [searchText]);

  const isSearching = searchText.trim() !== '';

  /** Opens the find field and puts the cursor in it, with its text selected. */
  const openFind = useCallback(() => {
    setFindOpen(true);
    setSearchText('');
    setFindFocusRequest((count) => count + 1);
  }, []);

  // Puts the cursor in the find field once it is drawn, with its text selected.
  useEffect(() => {
    if (findFocusRequest === 0) return;
    findInputRef.current?.focus();
    findInputRef.current?.select();
  }, [findFocusRequest]);

  /** Closes the find field and takes the marks off the page. */
  const closeFind = useCallback(() => {
    setFindOpen(false);
    setFindText('');
    paneRef.current?.focus();
  }, []);

  // Ctrl+F (Cmd+F on a Mac) opens find-in-page, but only when the key press is meant for this
  // tab: the focus is inside it, or on nothing at all. The editor and other panels keep the key.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 'f') return;
      const focused = document.activeElement;
      const focusIsHere = !focused || focused === document.body || !!paneRef.current?.contains(focused);
      if (!focusIsHere) return;
      event.preventDefault();
      openFind();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [openFind]);

  // Finds the matches again whenever the text to find or the page changes.
  useEffect(() => {
    const root = viewerRef.current;
    const ranges = findOpen && root && !isSearching && !loading ? findTextRanges(root, findText) : [];
    setFindRanges(ranges);
    setFindIndex(0);
  }, [findOpen, findText, content, loading, isSearching]);

  // Marks the matches, and scrolls the current one into view.
  useEffect(() => {
    paintFindHighlights(findRanges, findIndex);
    const current = findRanges[findIndex];
    if (current) {
      const container = current.startContainer;
      scrollElementIntoView(container instanceof Element ? container : container.parentElement, 'center');
    }
  }, [findRanges, findIndex]);

  // Takes the marks off when the tab closes.
  useEffect(() => () => paintFindHighlights([], -1), []);

  /**
   * Moves to the next or previous match, going round at either end.
   *
   * @param step 1 for the next match, -1 for the previous one.
   */
  const stepFind = (step: number) => {
    if (findRanges.length === 0) return;
    setFindIndex((index) => (index + step + findRanges.length) % findRanges.length);
  };

  /**
   * Handles Enter, Shift+Enter and Escape in the find field.
   *
   * @param event The key press.
   */
  const onFindKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      stepFind(event.shiftKey ? -1 : 1);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closeFind();
    }
  };

  // A new focusEntry request opens its topic; the next effect scrolls once the page is drawn.
  const focusNonce = focusEntry?.nonce;
  useEffect(() => {
    if (!focusEntry) return;
    setSearchText('');
    setPendingEntry(focusEntry);
    if (focusEntry.topicId !== selectedTopicId) onSelectTopic(focusEntry.topicId);
    // Only a new nonce is a new request; the other values are read as they are now.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusNonce]);

  // Scrolls to the requested entry once its topic is drawn, and lights it up for a moment.
  useEffect(() => {
    if (!pendingEntry || loading || isSearching || selectedTopicId !== pendingEntry.topicId) return;
    const element = viewerRef.current?.querySelector(`[id="${pendingEntry.anchorId}"]`) ?? null;
    if (!element && !content) return;
    setPendingEntry(null);
    if (!element) return;
    scrollElementIntoView(element, 'start');
    setHighlightedAnchorId(pendingEntry.anchorId);
  }, [pendingEntry, loading, isSearching, selectedTopicId, content]);

  // Turns the light off after the fade.
  useEffect(() => {
    if (!highlightedAnchorId) return;
    const timer = setTimeout(() => setHighlightedAnchorId(null), ENTRY_HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [highlightedAnchorId]);

  const matchRows: SelectableItem[] = useMemo(
    () => (searchMatches ?? []).map((match) => ({
      id: match.id,
      value: match.id,
      label: match.title,
      subtitle: match.snippet ? `…${match.snippet}…` : match.path,
      tooltip: match.path,
    })),
    [searchMatches],
  );

  const openTopic = (topicId: string) => {
    setSearchText('');
    onSelectTopic(topicId);
  };

  const findCountLabel = findText.trim() === ''
    ? ''
    : findRanges.length === 0 ? 'No matches' : `${findIndex + 1} of ${findRanges.length}`;

  return (
    <div className="documentation-pane" ref={paneRef} tabIndex={-1}>
      <Toolbar className="command-console__tab-toolbar" ariaLabel="Documentation">
        <ToolbarGroup>
          <ToolbarLabel>Topic</ToolbarLabel>
          <PopoverSelect
            label="Topic"
            size="compact"
            options={topicOptions}
            selectedId={selectedTopicId}
            onSelect={(topicId) => topicId && openTopic(topicId)}
            placeholder="Choose a topic"
          />
        </ToolbarGroup>
        <ToolbarGroup>
          <TextInput
            type="search"
            size="small"
            aria-label="Search all documentation topics"
            placeholder="Search all topics..."
            title="Looks through every topic and lists the ones that match"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
          <Button
            variant="ghost"
            size="small"
            onClick={openFind}
            title="Find in this topic (Ctrl+F)"
            aria-label="Find in this topic"
          >
            Find
          </Button>
        </ToolbarGroup>
      </Toolbar>
      {findOpen && (
        <div className="documentation-pane__find" role="search" aria-label="Find in this topic">
          <TextInput
            ref={findInputRef}
            type="search"
            size="small"
            width="fill"
            aria-label="Find in this topic"
            placeholder="Find in this topic"
            value={findText}
            onChange={(event) => setFindText(event.target.value)}
            onKeyDown={onFindKeyDown}
          />
          <span className="documentation-pane__find-count" aria-live="polite">{findCountLabel}</span>
          <Button variant="ghost" size="small" onClick={() => stepFind(-1)} disabled={findRanges.length === 0} title="Previous match (Shift+Enter)">
            Previous
          </Button>
          <Button variant="ghost" size="small" onClick={() => stepFind(1)} disabled={findRanges.length === 0} title="Next match (Enter)">
            Next
          </Button>
          <Button variant="ghost" size="small" onClick={closeFind} title="Close (Escape)">
            Close
          </Button>
        </div>
      )}
      {isSearching ? (
        <SelectableList
          className="documentation-pane__matches"
          items={matchRows}
          selectedId={selectedTopicId ?? undefined}
          onSelect={openTopic}
          isLoading={searchMatches === null}
          loadingMessage="Searching the documentation"
          emptyMessage="No topics match the search."
        />
      ) : (
        <div className="documentation-pane__viewer" ref={viewerRef}>
          <DocViewer
            topics={topics}
            selectedTopicId={selectedTopicId}
            content={content}
            loading={loading}
            onSelectTopic={onSelectTopic}
            highlightedAnchorId={highlightedAnchorId}
          />
        </div>
      )}
    </div>
  );
};
