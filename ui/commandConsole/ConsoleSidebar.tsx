/**
 * @file ConsoleSidebar.tsx
 * @description Sidebar for Command Console offering tabbed lists of Script Recipes, User Scripts, and Pipeline Runs.
 */
import React, { useState, useMemo } from 'react';
import { ProcessingJob } from '../common/types/backendTypes';

export interface ScriptRecipeItem {
  id: string;
  name: string;
  filename: string;
  category: string;
  description: string;
  size_bytes: number;
}

export interface UserScriptItem {
  id: string;
  name: string;
  filename: string;
  size_bytes: number;
  modified_at: number;
}

export interface DocTopicItem {
  id: string;
  title: string;
  path: string;
  category: string;
}

interface ConsoleSidebarProps {
  recipes: ScriptRecipeItem[];
  userScripts: UserScriptItem[];
  pipelineRuns: ProcessingJob[];
  selectedRunId: string | null;
  docTopics?: DocTopicItem[];
  selectedDocId?: string | null;
  activeSidebarTab?: 'scripts' | 'runs' | 'docs';
  onTabChange?: (tab: 'scripts' | 'runs' | 'docs') => void;
  onSelectRecipe: (recipe: ScriptRecipeItem) => void;
  onSelectUserScript: (script: UserScriptItem) => void;
  onSelectRun: (run: ProcessingJob) => void;
  onSelectDocTopic?: (topicId: string) => void;
  onRefreshRuns: () => void;
  onNewScript: () => void;
}

export const ConsoleSidebar: React.FC<ConsoleSidebarProps> = ({
  recipes,
  userScripts,
  pipelineRuns,
  selectedRunId,
  docTopics = [],
  selectedDocId = null,
  activeSidebarTab,
  onTabChange,
  onSelectRecipe,
  onSelectUserScript,
  onSelectRun,
  onSelectDocTopic,
  onRefreshRuns,
  onNewScript,
}) => {
  const [internalTab, setInternalTab] = useState<'scripts' | 'runs' | 'docs'>('scripts');
  const [docFilter, setDocFilter] = useState('');
  const activeTab = activeSidebarTab !== undefined ? activeSidebarTab : internalTab;

  const categorizedDocs = useMemo(() => {
    const filter = docFilter.trim().toLowerCase();
    const filtered = docTopics.filter(
      (t) =>
        !filter ||
        t.title.toLowerCase().includes(filter) ||
        t.path.toLowerCase().includes(filter) ||
        (t.category && t.category.toLowerCase().includes(filter))
    );

    const categoryOrder = [
      'API Reference',
      'General',
      'Desktop Application',
      'user_interface',
      'Interactive Notebooks',
      'notebooks',
      'Architecture & Design',
      'library_design',
    ];

    const categoryLabels: Record<string, string> = {
      'API Reference': '⚡ Python API Reference',
      'General': 'Getting Started & Guides',
      'Desktop Application': 'Desktop Application',
      'user_interface': 'Desktop Application',
      'Interactive Notebooks': 'Interactive Notebooks',
      'notebooks': 'Interactive Notebooks',
      'Architecture & Design': 'Architecture & Design',
      'library_design': 'Architecture & Design',
    };

    const groups: { name: string; items: DocTopicItem[] }[] = [];
    const processedCats = new Set<string>();

    for (const catKey of categoryOrder) {
      if (processedCats.has(catKey)) continue;
      const label = categoryLabels[catKey] || catKey;

      const items = filtered.filter((t) => {
        const cat = t.category || 'General';
        return cat === catKey || categoryLabels[cat] === label;
      });

      if (items.length > 0) {
        for (const [k, v] of Object.entries(categoryLabels)) {
          if (v === label) processedCats.add(k);
        }
        processedCats.add(catKey);
        groups.push({ name: label, items });
      }
    }

    for (const t of filtered) {
      const cat = t.category || 'General';
      if (!processedCats.has(cat)) {
        processedCats.add(cat);
        const items = filtered.filter((x) => (x.category || 'General') === cat);
        groups.push({ name: cat, items });
      }
    }

    return groups;
  }, [docTopics, docFilter]);

  const handleTabSwitch = (tab: 'scripts' | 'runs' | 'docs') => {
    if (onTabChange) {
      onTabChange(tab);
    } else {
      setInternalTab(tab);
    }
  };

  return (
    <div className="console-sidebar">
      <div className="console-sidebar__tabs">
        <button
          className={`console-sidebar__tab ${activeTab === 'scripts' ? 'console-sidebar__tab--active' : ''}`}
          onClick={() => handleTabSwitch('scripts')}
          type="button"
        >
          Scripts & Recipes
        </button>
        <button
          className={`console-sidebar__tab ${activeTab === 'runs' ? 'console-sidebar__tab--active' : ''}`}
          onClick={() => {
            handleTabSwitch('runs');
            onRefreshRuns();
          }}
          type="button"
        >
          Pipeline Runs ({pipelineRuns.length})
        </button>
        <button
          className={`console-sidebar__tab ${activeTab === 'docs' ? 'console-sidebar__tab--active' : ''}`}
          onClick={() => handleTabSwitch('docs')}
          type="button"
        >
          Docs ({docTopics.length})
        </button>
      </div>

      <div className="console-panel__body">
        {activeTab === 'scripts' ? (
          <div className="console-sidebar__list">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 6px' }}>
              <span style={{ fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
                User Scripts
              </span>
              <button
                className="editor-btn"
                style={{ padding: '2px 6px', fontSize: '11px' }}
                onClick={onNewScript}
                type="button"
              >
                + New
              </button>
            </div>
            {userScripts.length === 0 ? (
              <div style={{ padding: '8px', fontSize: '12px', color: 'var(--text-dim)' }}>
                No saved scripts yet.
              </div>
            ) : (
              userScripts.map((s, index) => (
                <div
                  key={s.filename || s.id || `user-script-${index}`}
                  className="console-sidebar__item"
                  onClick={() => onSelectUserScript(s)}
                >
                  <div className="console-sidebar__item-title">
                    <span>{s.name}</span>
                    <span style={{ fontSize: '10px', color: 'var(--text-dim)' }}>{s.filename}</span>
                  </div>
                  <div className="console-sidebar__item-sub">
                    {Math.round(s.size_bytes / 1024)} KB
                  </div>
                </div>
              ))
            )}

            <div style={{ marginTop: '12px', padding: '4px 6px', fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
              Built-in Recipes
            </div>
            {recipes.map((r, index) => (
              <div
                key={r.id || r.filename || `recipe-${index}`}
                className="console-sidebar__item"
                onClick={() => onSelectRecipe(r)}
              >
                <div className="console-sidebar__item-title">
                  <span>{r.name}</span>
                  <span style={{ fontSize: '10px', color: 'var(--term-cyan)' }}>{r.category}</span>
                </div>
                <div className="console-sidebar__item-sub" title={r.description}>
                  {r.description || r.filename}
                </div>
              </div>
            ))}
          </div>
        ) : activeTab === 'runs' ? (
          <div className="console-sidebar__list">
            {pipelineRuns.length === 0 ? (
              <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-dim)' }}>
                No recent pipeline runs found.
              </div>
            ) : (
              pipelineRuns.map((job) => (
                <div
                  key={job.id}
                  className={`console-sidebar__item ${selectedRunId === job.id ? 'console-sidebar__item--active' : ''}`}
                  onClick={() => onSelectRun(job)}
                >
                  <div className="console-sidebar__item-title">
                    <span>{job.targetId || 'Target Run'}</span>
                    <span style={{
                      fontSize: '10px',
                      padding: '1px 5px',
                      borderRadius: '3px',
                      background: job.status === 'completed' ? 'rgba(38,162,105,0.2)' : 'rgba(233,84,32,0.2)',
                      color: job.status === 'completed' ? 'var(--ubuntu-green)' : 'var(--ubuntu-orange)',
                    }}>
                      {job.status}
                    </span>
                  </div>
                  <div className="console-sidebar__item-sub">
                    {job.jobType} &bull; {job.createdAt ? new Date(job.createdAt).toLocaleTimeString() : ''}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="console-sidebar__list">
            <div style={{ padding: '4px 6px', marginBottom: '4px' }}>
              <input
                type="text"
                style={{
                  width: '100%',
                  padding: '5px 8px',
                  fontSize: '11px',
                  background: 'var(--cmd-bg-dark, #181818)',
                  border: '1px solid var(--term-border, #333)',
                  borderRadius: '3px',
                  color: 'var(--text-primary, #fff)',
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
                placeholder="Filter documentation & APIs..."
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
              />
            </div>
            {categorizedDocs.length === 0 ? (
              <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-dim)' }}>
                {docFilter ? 'No matching documentation topics found.' : 'No documentation topics found.'}
              </div>
            ) : (
              categorizedDocs.map((group) => (
                <div key={group.name} style={{ marginBottom: '8px' }}>
                  <div
                    style={{
                      padding: '4px 6px',
                      fontSize: '11px',
                      fontWeight: 600,
                      color: group.name.includes('API Reference')
                        ? 'var(--term-cyan, #4ec9b0)'
                        : 'var(--text-dim, #888)',
                      letterSpacing: '0.5px',
                    }}
                  >
                    {group.name} ({group.items.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                    {group.items.map((topic) => (
                      <div
                        key={topic.id}
                        className={`console-sidebar__item ${selectedDocId === topic.id ? 'console-sidebar__item--active' : ''}`}
                        onClick={() => onSelectDocTopic && onSelectDocTopic(topic.id)}
                        title={topic.title}
                      >
                        <div className="console-sidebar__item-title">
                          <span>{topic.title}</span>
                        </div>
                        <div className="console-sidebar__item-sub">
                          {topic.path}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};
