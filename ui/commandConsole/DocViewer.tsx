/**
 * @file DocViewer.tsx
 * @description Markdown documentation viewer for inspecting library architecture and notebook tutorials in the Command Console.
 */
import React from 'react';
import ReactMarkdown from 'react-markdown';

interface DocTopicItem {
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

export const DocViewer: React.FC<DocViewerProps> = ({
  topics,
  selectedTopicId,
  content,
  loading = false,
  onSelectTopic,
}) => {
  return (
    <div style={{ display: 'flex', height: '100%', width: '100%' }}>
      {/* Topics List Sub-pane */}
      <div style={{
        width: '220px',
        borderRight: '1px solid var(--term-border)',
        overflowY: 'auto',
        background: 'var(--cmd-bg-medium)',
        padding: '8px 4px',
      }}>
        <div style={{ padding: '6px 8px', fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
          Documentation Guides
        </div>
        {topics.map((t) => (
          <div
            key={t.id}
            onClick={() => onSelectTopic(t.id)}
            style={{
              padding: '6px 8px',
              borderRadius: '4px',
              fontSize: '12px',
              cursor: 'pointer',
              color: selectedTopicId === t.id ? '#fff' : 'var(--text-primary)',
              background: selectedTopicId === t.id ? 'var(--adwaita-blue)' : 'transparent',
              marginBottom: '2px',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
            title={t.title}
          >
            {t.title}
          </div>
        ))}
      </div>

      {/* Markdown Content Pane */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 24px' }}>
        {loading ? (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', marginTop: '40px' }}>
            Loading documentation topic...
          </div>
        ) : content ? (
          <div className="doc-viewer">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        ) : (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', marginTop: '40px' }}>
            Select a guide or tutorial from the left to read.
          </div>
        )}
      </div>
    </div>
  );
};
