/**
 * @file DocViewer.tsx
 * @description Markdown documentation viewer for inspecting library architecture and notebook tutorials in the Command Console.
 * Intercepts internal and external links to prevent app reloading and allow seamless topic navigation.
 * An API reference line that ends in an anchor mark, such as `{#api-TargetCatalog-delete_images}`,
 * gets that anchor as its `id` (the mark itself is not shown), so other parts of the console can
 * jump to it.
 */
import React, { createContext, useContext, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';

import { Skeleton } from '../common/components/Skeleton';

/** One documentation topic in the picker. */
export interface DocTopicItem {
  id: string;
  title: string;
  path: string;
  category: string;
}

/** Props for DocViewer. */
interface DocViewerProps {
  /** Every documentation topic, for following links between topics. */
  topics: DocTopicItem[];
  /** The id of the topic shown, or null before one is picked. */
  selectedTopicId: string | null;
  /** The text of the topic shown, in Markdown. */
  content: string;
  /** True while the topic is read. */
  loading?: boolean;
  /** Called with the id of the topic a link opens. */
  onSelectTopic: (topicId: string) => void;
  /** The anchor of the entry to light up for a moment, or null for none. */
  highlightedAnchorId?: string | null;
}

/** The anchor mark at the end of an API reference line, such as ` {#api-TargetCatalog-delete_images}`. */
const ANCHOR_MARK = /\s*\{#(api-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)?)\}\s*$/;

/**
 * Takes the anchor mark off the end of a heading or list item.
 *
 * @param children The rendered children of the heading or list item.
 * @returns The anchor, if the last piece of text ended with one, and the children without the mark.
 */
export function splitEntryAnchor(children: React.ReactNode): { anchorId: string | null; children: React.ReactNode } {
  const pieces = React.Children.toArray(children);
  const last = pieces[pieces.length - 1];
  if (typeof last !== 'string') return { anchorId: null, children };
  const match = last.match(ANCHOR_MARK);
  if (!match || match.index === undefined) return { anchorId: null, children };
  const rest = last.slice(0, match.index);
  return { anchorId: match[1], children: rest ? [...pieces.slice(0, -1), rest] : pieces.slice(0, -1) };
}

/** The props React Markdown passes to a custom element, with the syntax-tree node it adds. */
type MarkdownElementProps<T extends HTMLElement> = React.HTMLAttributes<T> & { node?: unknown };

/**
 * The anchor of the entry to light up. A context, so the renderers stay the same and the page is
 * not redrawn from scratch (which would lose the find-in-page marks) when it changes.
 */
const HighlightedAnchorContext = createContext<string | null>(null);

/**
 * Makes a renderer for a heading or list item that turns a trailing anchor mark into the element's id.
 *
 * @param Tag The element to draw: `h3`, `h4` or `li`.
 * @returns The renderer for React Markdown.
 */
function anchoredElement(Tag: 'h3' | 'h4' | 'li') {
  const AnchoredElement = ({ node: _node, children, className, ...props }: MarkdownElementProps<HTMLElement>) => {
    const highlightedAnchorId = useContext(HighlightedAnchorContext);
    const { anchorId, children: shown } = splitEntryAnchor(children);
    const classes = [
      className,
      anchorId ? 'doc-viewer__entry' : '',
      anchorId && anchorId === highlightedAnchorId ? 'doc-viewer__entry--flash' : '',
    ].filter(Boolean).join(' ');
    return (
      <Tag {...props} id={anchorId ?? props.id} className={classes || undefined}>
        {shown}
      </Tag>
    );
  };
  AnchoredElement.displayName = `Anchored(${Tag})`;
  return AnchoredElement;
}

/** The renderers for the elements that can hold an anchor mark. */
const ANCHORED_ELEMENTS = { h3: anchoredElement('h3'), h4: anchoredElement('h4'), li: anchoredElement('li') };

/**
 * Normalizes relative target links against known documentation topics.
 *
 * Handles formats like:
 * - `Installation.md`
 * - `library_design/Astrometrics_Library_Architecture.md`
 * - `../user_interface/user_guides/User_Manual.md`
 * - `api/astrometricslib.rst`
 *
 * @param href The target anchor link string.
 * @param currentTopicId Currently displayed topic ID.
 * @param topics Array of available documentation topics.
 * @returns Matching topic ID if found, otherwise null.
 */
function resolveTopicId(href: string, currentTopicId: string | null, topics: DocTopicItem[]): string | null {
  if (!href || href.startsWith('#') || href.startsWith('http://') || href.startsWith('https://')) {
    return null;
  }

  // Strip leading anchor fragments (e.g. "path/to/doc.md#section-1")
  const pathWithoutAnchor = href.split('#')[0].trim();
  if (!pathWithoutAnchor) return null;

  const stripExt = (p: string) => p.replace(/\.(rst|md|html)$/i, '');
  const normTarget = stripExt(pathWithoutAnchor);

  // 1. Exact ID or path match (with or without extension)
  const exactMatch = topics.find(
    (t) =>
      t.id === pathWithoutAnchor ||
      t.path === pathWithoutAnchor ||
      stripExt(t.id) === normTarget ||
      stripExt(t.path) === normTarget
  );
  if (exactMatch) return exactMatch.id;

  // 2. Relative path resolution against current topic folder
  if (currentTopicId) {
    const currentParts = currentTopicId.split('/');
    currentParts.pop(); // remove current filename to get directory
    const targetParts = pathWithoutAnchor.split('/');

    for (const part of targetParts) {
      if (part === '.' || part === '') {
        continue;
      } else if (part === '..') {
        if (currentParts.length > 0) currentParts.pop();
      } else {
        currentParts.push(part);
      }
    }
    const resolvedPath = currentParts.join('/');
    const normResolved = stripExt(resolvedPath);
    const resolvedMatch = topics.find(
      (t) =>
        t.id === resolvedPath ||
        t.path === resolvedPath ||
        stripExt(t.id) === normResolved ||
        stripExt(t.path) === normResolved
    );
    if (resolvedMatch) return resolvedMatch.id;

    // Direct check for relative API stubs
    if (normResolved.startsWith('api/') || normResolved.startsWith('generated/')) {
      return normResolved.startsWith('generated/') ? `api/${normResolved}` : normResolved;
    }
  }

  // 3. Fallback: match by filename stem
  const targetStem = normTarget.split('/').pop()?.toLowerCase();
  if (targetStem) {
    const filenameMatch = topics.find((t) => {
      const topicStem = stripExt(t.path).split('/').pop()?.toLowerCase();
      const idStem = stripExt(t.id).split('/').pop()?.toLowerCase();
      return topicStem === targetStem || idStem === targetStem;
    });
    if (filenameMatch) return filenameMatch.id;
  }

  // 4. Direct check for generated or direct API paths
  if (normTarget.startsWith('api/') || normTarget.startsWith('generated/')) {
    return normTarget.startsWith('generated/') ? `api/${normTarget}` : normTarget;
  }

  return null;
}

/**
 * Converts MyST / Sphinx admonition syntax (:::{note} ... :::) into blockquotes
 * so standard markdown parsers render them cleanly.
 *
 * @param rawMarkdown The original markdown document string.
 * @returns Cleaned markdown string.
 */
function preprocessAdmonitions(rawMarkdown: string): string {
  if (!rawMarkdown) return '';
  return rawMarkdown
    .replace(/:::{(\w+)}[ \t]*\n([\s\S]*?):::/g, (_match, type, body) => {
      const title = type.charAt(0).toUpperCase() + type.slice(1);
      const quotedBody = body
        .trim()
        .split('\n')
        .map((line: string) => `> ${line}`)
        .join('\n');
      return `> **${title}:**\n${quotedBody}\n`;
    });
}

/**
 * Draws one documentation topic from its Markdown text.
 *
 * @param props See `DocViewerProps`.
 * @returns The page, a loading placeholder, or a hint to pick a topic.
 */
export const DocViewer: React.FC<DocViewerProps> = ({
  topics,
  selectedTopicId,
  content,
  loading = false,
  onSelectTopic,
  highlightedAnchorId = null,
}) => {
  const processedContent = useMemo(() => preprocessAdmonitions(content), [content]);

  // Custom link renderer to intercept clicks and prevent app reload
  const markdownComponents = useMemo(() => ({
    ...ANCHORED_ELEMENTS,
    a: ({ href, children, node: _node, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { node?: unknown }) => {
      const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        if (!href) return;

        // Prevent default browser navigation (which unloads the Electron app)
        e.preventDefault();

        // 1. External link: open in user's OS browser
        if (href.startsWith('http://') || href.startsWith('https://')) {
          window.open(href, '_blank', 'noopener,noreferrer');
          return;
        }

        // 2. Anchor jump within same document
        if (href.startsWith('#')) {
          const targetId = href.slice(1);
          const element = document.getElementById(targetId) ||
            document.querySelector(`[name="${targetId}"]`) ||
            document.querySelector(`h1, h2, h3, h4, h5, h6`);
          if (element) {
            element.scrollIntoView({ behavior: 'smooth' });
          }
          return;
        }

        // 3. Internal doc link: resolve against topic catalog
        const resolvedId = resolveTopicId(href, selectedTopicId, topics);
        if (resolvedId) {
          onSelectTopic(resolvedId);
        } else {
          console.warn(`[DocViewer] Unable to resolve link to documentation topic: ${href}`);
        }
      };

      return (
        <a href={href} onClick={handleClick} {...props}>
          {children}
        </a>
      );
    },
  }), [selectedTopicId, topics, onSelectTopic]);

  return (
    <div className="doc-viewer-container">
      {loading ? (
        <Skeleton variant="list" count={8} lines={3} label="Loading the documentation topic" />
      ) : processedContent ? (
        <div className="doc-viewer">
          <HighlightedAnchorContext.Provider value={highlightedAnchorId}>
            <ReactMarkdown components={markdownComponents}>{processedContent}</ReactMarkdown>
          </HighlightedAnchorContext.Provider>
        </div>
      ) : (
        <div className="doc-viewer__empty">
          Pick a topic, or search the documentation, to read it here.
        </div>
      )}
    </div>
  );
};
