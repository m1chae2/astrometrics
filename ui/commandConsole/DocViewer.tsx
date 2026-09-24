/**
 * @file DocViewer.tsx
 * @description Markdown documentation viewer for inspecting library architecture and notebook tutorials in the Command Console.
 * Intercepts internal and external links to prevent app reloading and allow seamless topic navigation.
 */
import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';

export interface DocTopicItem {
  id: string;
  title: string;
  path: string;
  category: string;
}

interface DocViewerProps {
  topics: DocTopicItem[];
  selectedTopicId: string | null;
  content: string;
  loading?: boolean;
  onSelectTopic: (topicId: string) => void;
}

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

export const DocViewer: React.FC<DocViewerProps> = ({
  topics,
  selectedTopicId,
  content,
  loading = false,
  onSelectTopic,
}) => {
  const processedContent = useMemo(() => preprocessAdmonitions(content), [content]);

  // Custom link renderer to intercept clicks and prevent app reload
  const markdownComponents = useMemo(() => ({
    a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
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
        <div style={{ color: 'var(--text-dim)', textAlign: 'center', marginTop: '60px', width: '100%' }}>
          Loading documentation topic...
        </div>
      ) : processedContent ? (
        <div className="doc-viewer">
          <ReactMarkdown components={markdownComponents}>{processedContent}</ReactMarkdown>
        </div>
      ) : (
        <div style={{ color: 'var(--text-dim)', textAlign: 'center', marginTop: '60px', width: '100%' }}>
          Select a guide or tutorial from the Docs tab on the left to read.
        </div>
      )}
    </div>
  );
};
