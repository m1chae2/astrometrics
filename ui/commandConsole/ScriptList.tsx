/**
 * @file ScriptList.tsx
 * @description The Command Console's left column: your saved scripts and the examples (the
 * tutorial notebooks and example scripts; the backend calls them recipes), on two tabs with one
 * search box. Examples are grouped
 * under a heading per library. The column folds to a thin strip, the same way the left column
 * of Image Processing, the Astronomy Manager and the Planetarium does; the strip shows the name
 * of the open script.
 */
import React, { useMemo, useState } from 'react';
import { RadioListFiltering } from '../common/radioList/RadioListFiltering';
import { CollapsibleColumn, useCollapsibleColumn } from '../common/components/CollapsibleColumn';
import { SectionPanel } from '../common/components/SectionPanel';
import { SelectableList, type SelectableItem, type SelectableItemBadge } from '../common/components/SelectableList';
import { Tabs } from '../common/components/Tabs';
import '../common/styles/manager.css';
import type { ActionRegistry } from '../common/services/backendApi';

/** One script you saved, as `terminal:list_scripts` lists it. */
export type UserScriptItem = ActionRegistry['terminal:list_scripts']['response'][number];

/** One recipe (a tutorial notebook or example script), as `terminal:list_recipes` lists it. */
export type RecipeItem = ActionRegistry['terminal:list_recipes']['response'][number];

/** The start of a list id that stands for one of your saved scripts. */
const USER_SCRIPT_ID_PREFIX = 'script:';

/** The start of a list id that stands for a recipe. */
const RECIPE_ID_PREFIX = 'recipe:';

/**
 * Builds the list id of one of your saved scripts.
 *
 * @param filename The script's file name, such as "my_analysis.py".
 * @returns The list id, such as "script:my_analysis.py".
 */
export function userScriptListId(filename: string): string {
  return `${USER_SCRIPT_ID_PREFIX}${filename}`;
}

/**
 * Builds the list id of a recipe.
 *
 * @param recipeId The recipe's id from `terminal:list_recipes`.
 * @returns The list id, such as "recipe:light_curve".
 */
export function recipeListId(recipeId: string): string {
  return `${RECIPE_ID_PREFIX}${recipeId}`;
}

/** The two tabs of the script list. */
export type ScriptListTab = 'mine' | 'recipes';

/** One group of rows under a small heading, such as the Astrometrics recipes. */
export interface ScriptListGroup {
  /** The group's key, such as "astrometrics". */
  key: string;
  /** The heading over the group, or an empty string for a list with no headings. */
  heading: string;
  /** The rows of the group. */
  rows: SelectableItem[];
}

/** The badge on a notebook row: the file's real extension. */
const NOTEBOOK_BADGE: SelectableItemBadge = { label: '.ipynb', tone: 'info', title: 'Jupyter notebook' };

/** The badge on an example script row: the file's real extension. */
const PYTHON_SCRIPT_BADGE: SelectableItemBadge = { label: '.py', title: 'Python script' };

/** The library headings for the recipe groups, in the order they are shown. */
const RECIPE_LIBRARY_HEADINGS: Array<[string, string]> = [
  ['astrometrics', 'Astrometrics'],
  ['wayfinding', 'Wayfinding'],
];

/**
 * Tells whether a row's name, second line or badge meaning (such as "Jupyter notebook") holds the search
 * text (case does not matter).
 *
 * @param row The row.
 * @param needle The search text, in lower case and trimmed.
 * @returns True when the row matches, or when there is no search text.
 */
function rowMatches(row: SelectableItem, needle: string): boolean {
  if (!needle) return true;
  return row.label.toLowerCase().includes(needle)
    || (row.subtitle ?? '').toLowerCase().includes(needle)
    || (row.badges ?? []).some((badge) => (badge.title ?? badge.label).toLowerCase().includes(needle));
}

/**
 * Builds the rows of the My scripts tab: newest change first, then A to Z.
 *
 * @param userScripts Your saved scripts.
 * @param filterText The text typed in the search box.
 * @returns The rows to show.
 */
export function buildUserScriptRows(userScripts: UserScriptItem[], filterText: string): SelectableItem[] {
  const needle = filterText.trim().toLowerCase();
  return [...userScripts]
    .sort((first, second) => (second.modified_at ?? 0) - (first.modified_at ?? 0) || first.name.localeCompare(second.name))
    .map((script) => ({
      id: userScriptListId(script.filename),
      value: userScriptListId(script.filename),
      label: script.name,
      subtitle: script.filename,
      tooltip: `${script.name} (${script.filename})`,
    }))
    .filter((row) => rowMatches(row, needle));
}

/**
 * Builds the groups of the Examples tab: one group per library (Astrometrics, then Wayfinding,
 * then any other), each with the notebooks in their numbered order and then the scripts. The
 * second line of a row says whether it is a notebook or a script.
 *
 * @param recipes The recipes.
 * @param filterText The text typed in the search box.
 * @returns The groups that have at least one matching row.
 */
export function buildRecipeGroups(recipes: RecipeItem[], filterText: string): ScriptListGroup[] {
  const needle = filterText.trim().toLowerCase();
  const knownKeys = RECIPE_LIBRARY_HEADINGS.map(([key]) => key);
  const otherKeys = [...new Set(recipes.map((recipe) => recipe.category || 'other'))]
    .filter((key) => !knownKeys.includes(key))
    .sort();
  const headings: Array<[string, string]> = [
    ...RECIPE_LIBRARY_HEADINGS,
    ...otherKeys.map((key): [string, string] => [key, key.charAt(0).toUpperCase() + key.slice(1)]),
  ];
  const kindRank = (recipe: RecipeItem) => (recipe.kind === 'notebook' ? 0 : 1);
  return headings
    .map(([key, heading]) => ({
      key,
      heading,
      rows: recipes
        .filter((recipe) => (recipe.category || 'other') === key)
        .sort((first, second) => kindRank(first) - kindRank(second)
          || first.filename.localeCompare(second.filename, undefined, { numeric: true }))
        .map((recipe) => ({
          id: recipeListId(recipe.id),
          value: recipeListId(recipe.id),
          label: recipe.name,
          // A short badge says what kind of file it is; the long name may wrap instead.
          badges: [recipe.kind === 'notebook' ? NOTEBOOK_BADGE : PYTHON_SCRIPT_BADGE],
          tooltip: recipe.description ? `${recipe.name}: ${recipe.description}` : recipe.name,
        }))
        .filter((row) => rowMatches(row, needle)),
    }))
    .filter((group) => group.rows.length > 0);
}

/** Props for ScriptList. */
interface ScriptListProps {
  /** Your saved scripts. */
  userScripts: UserScriptItem[];
  /** The recipes. */
  recipes: RecipeItem[];
  /** The list id of the script open in the editor, if it came from the list. */
  openItemId?: string;
  /** The name of the open script, shown on the folded strip. Empty for a new, unsaved script. */
  openScriptName: string;
  /** Called when you pick one of your saved scripts. */
  onOpenUserScript: (script: UserScriptItem) => void;
  /** Called when you pick a recipe. */
  onOpenRecipe: (recipe: RecipeItem) => void;
  /** True until the first read of both lists ends. */
  isLoading: boolean;
  /** True when the lists could not be read. */
  isError: boolean;
  /** Reads the lists again. */
  onRetry: () => void;
}

/**
 * Shows the script list, or the thin strip when you fold it. Picking a row opens that script
 * or notebook in the editor. The search box filters the tab that is shown.
 *
 * @param props See `ScriptListProps`.
 * @returns The left column of the Command Console.
 */
export const ScriptList: React.FC<ScriptListProps> = ({
  userScripts,
  recipes,
  openItemId,
  openScriptName,
  onOpenUserScript,
  onOpenRecipe,
  isLoading,
  isError,
  onRetry,
}) => {
  const [filterText, setFilterText] = useState('');
  const [activeTab, setActiveTab] = useState<ScriptListTab>('mine');
  // When another script opens (a notebook saved as your own script, say), show the tab that holds it.
  const [shownOpenItemId, setShownOpenItemId] = useState(openItemId);
  if (openItemId !== shownOpenItemId) {
    setShownOpenItemId(openItemId);
    if (openItemId?.startsWith(USER_SCRIPT_ID_PREFIX)) setActiveTab('mine');
    else if (openItemId?.startsWith(RECIPE_ID_PREFIX)) setActiveTab('recipes');
  }
  // The whole column folds to a thin strip; the choice is remembered for this viewer.
  const [collapsed, setCollapsed] = useCollapsibleColumn('astrometrics:commandConsole:scriptListCollapsed');
  const userRows = useMemo(() => buildUserScriptRows(userScripts, filterText), [userScripts, filterText]);
  const recipeGroups = useMemo(() => buildRecipeGroups(recipes, filterText), [recipes, filterText]);
  const recipeRowCount = recipeGroups.reduce((total, group) => total + group.rows.length, 0);

  const handleSelect = (listId: string) => {
    if (listId.startsWith(USER_SCRIPT_ID_PREFIX)) {
      const filename = listId.slice(USER_SCRIPT_ID_PREFIX.length);
      const script = userScripts.find((candidate) => candidate.filename === filename);
      if (script) onOpenUserScript(script);
    } else if (listId.startsWith(RECIPE_ID_PREFIX)) {
      const recipeId = listId.slice(RECIPE_ID_PREFIX.length);
      const recipe = recipes.find((candidate) => candidate.id === recipeId);
      if (recipe) onOpenRecipe(recipe);
    }
  };

  const isSearching = filterText.trim() !== '';
  const groups: ScriptListGroup[] = activeTab === 'mine'
    ? [{ key: 'mine', heading: '', rows: userRows }]
    : recipeGroups;
  const emptyMessage = isSearching
    ? 'Nothing matches the search.'
    : activeTab === 'mine' ? 'No saved scripts yet. Open an example and save it, or press New.' : 'No examples found.';

  return (
    <CollapsibleColumn
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
      stripTitle="Scripts"
      selectedLabel={openScriptName || 'New script'}
      showLabel="Show script list"
    >
      <div className="manager panel-group command-console__script-column">
        <SectionPanel
          title="Scripts"
          className="flex-fill flex-col manager__list-panel"
          onTitleClick={() => setCollapsed(true)}
          titleButtonLabel="Hide script list"
        >
          <div className="command-console__panel-tabs">
          <Tabs
            tabs={[
              { id: 'mine', label: 'My scripts', count: userRows.length },
              { id: 'recipes', label: 'Examples', count: recipeRowCount },
            ]}
            selectedId={activeTab}
            onChange={(tabId) => setActiveTab(tabId as ScriptListTab)}
            ariaLabel="Script list"
            idPrefix="command-console-scripts"
            size="compact"
          />
          </div>
          <RadioListFiltering
            options={[]}
            selectedOption=""
            onOptionChange={() => {}}
            filterText={filterText}
            onFilterTextChange={setFilterText}
            placeholder={activeTab === 'mine' ? 'Search my scripts...' : 'Search examples...'}
          />
          <div className="command-console__script-groups">
            {groups.length === 0 || (groups.length === 1 && groups[0].rows.length === 0) ? (
              <SelectableList
                items={[]}
                onSelect={handleSelect}
                isLoading={isLoading}
                loadingMessage="Loading scripts"
                emptyMessage={emptyMessage}
                isError={isError}
                errorMessage="Could not load the scripts."
                onRetry={onRetry}
              />
            ) : (
              groups.map((group) => (
                <div key={group.key} className="command-console__script-group">
                  {group.heading && (
                    <div className="command-console__script-group-heading">{group.heading}</div>
                  )}
                  <SelectableList
                    className="command-console__script-group-list"
                    items={group.rows}
                    selectedId={openItemId}
                    onSelect={handleSelect}
                    wrapLabels={activeTab === 'recipes'}
                  />
                </div>
              ))
            )}
          </div>
        </SectionPanel>
      </div>
    </CollapsibleColumn>
  );
};
