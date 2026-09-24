/**
 * @file ConsoleSidebar.tsx
 * @description Sidebar for Command Console offering tabbed lists of Script Recipes, User Scripts, and Pipeline Runs.
 */
import React, { useState } from 'react';
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

interface ConsoleSidebarProps {
  recipes: ScriptRecipeItem[];
  userScripts: UserScriptItem[];
  pipelineRuns: ProcessingJob[];
  selectedRunId: string | null;
  onSelectRecipe: (recipe: ScriptRecipeItem) => void;
  onSelectUserScript: (script: UserScriptItem) => void;
  onSelectRun: (run: ProcessingJob) => void;
  onRefreshRuns: () => void;
  onNewScript: () => void;
}

export const ConsoleSidebar: React.FC<ConsoleSidebarProps> = ({
  recipes,
  userScripts,
  pipelineRuns,
  selectedRunId,
  onSelectRecipe,
  onSelectUserScript,
  onSelectRun,
  onRefreshRuns,
  onNewScript,
}) => {
  const [activeTab, setActiveTab] = useState<'scripts' | 'runs'>('scripts');

  return (
    <div className="console-sidebar">
      <div className="console-sidebar__tabs">
        <button
          className={`console-sidebar__tab ${activeTab === 'scripts' ? 'console-sidebar__tab--active' : ''}`}
          onClick={() => setActiveTab('scripts')}
          type="button"
        >
          Scripts & Recipes
        </button>
        <button
          className={`console-sidebar__tab ${activeTab === 'runs' ? 'console-sidebar__tab--active' : ''}`}
          onClick={() => {
            setActiveTab('runs');
            onRefreshRuns();
          }}
          type="button"
        >
          Pipeline Runs ({pipelineRuns.length})
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
        ) : (
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
        )}
      </div>
    </div>
  );
};
