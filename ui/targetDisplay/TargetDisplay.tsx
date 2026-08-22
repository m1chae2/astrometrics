import React, { useCallback, useState } from 'react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { ListActions } from '../common/components/ListActions';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { TargetViewerManager } from './targetViewerManager/TargetViewerManager';
import { TargetDetailsManager } from './targetDetailsManager/TargetDetailsManager';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetActions } from '../common/hooks/useTargetActions';
import { on as onEvent, emit as emitEvent } from '../common/utils/eventBus';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useTargetImage } from './hooks/useTargetImage';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import './targetDisplay.css';

/**
 * Main component for the target display view.
 * Coordinates target selection, viewing, and detail management.
 * REQ: TGT-1: Target Management
 * REQ: TGT-2: Target Visualization
 * REQ: TGT-3: Target Metadata Editor
 */
export const TargetDisplay: React.FC = () => {
  const {
    selectedTarget,
    setSelectedTarget,
    pendingTarget,
    setPendingTarget,
    reloadKey,
    forceReload,
    catalogId,
    setCatalogId,
    commonName,
    setCommonName,
    raShared,
    setRaShared,
    decShared,
    setDecShared,
  } = useTargetContext();

  const { remoteTargets } = useRemoteStatusContext();
  const {
    items,
    filterOptions,
    selectedFilterOption,
    setFilterOption,
    filterText,
    setFilterText,
    highlightedIds,
  } = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, setSelectedTarget, remoteTargets, false);

  const { saveTarget, confirmDeleteTarget } = useTargetActions();

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  /** Callback to finalize selection once an image is successfully loaded. */
  const handleImageLoaded = useCallback((target: string) => {
    setSelectedTarget(target);
    setPendingTarget('');
  }, [setSelectedTarget, setPendingTarget]);

  const { imageUrl, imageBlob, loading, error } = useTargetImage(
    pendingTarget || selectedTarget,
    handleImageLoaded
  );

  const activeTarget = pendingTarget || selectedTarget;

  const handleDeleteTarget = () => {
    // REQ: TGT-1.4: The display SHALL allow deletion of existing targets from the catalog.
    if (activeTarget) setShowDeleteConfirm(true);
  };

  const confirmDelete = async () => {
    await confirmDeleteTarget();
    setShowDeleteConfirm(false);
  };

  const leftPanel = (
    <RadioListManager
      title="Targets"
      // REQ: TGT-1.1: The display SHALL present a selectable list of all targets in the catalog.
      items={items}
      selectedId={selectedTarget}
      pendingId={pendingTarget}
      onSelect={setPendingTarget}
      filterOptions={filterOptions}
      selectedFilterOption={selectedFilterOption}
      onFilterOptionChange={setFilterOption}
      // REQ: TGT-1.2: The display SHALL allow filtering of the target list by text search.
      filterText={filterText}
      onFilterTextChange={setFilterText}
      highlightedIds={highlightedIds}
    />
  );

  const centerPanel = (
    // REQ: TGT-2.1: The display SHALL present the most recently captured image for the selected target.
    <TargetViewerManager
      imageUrl={imageUrl}
      imageBlob={imageBlob}
      loading={loading}
      error={error}
      selectedTarget={selectedTarget}
    />
  );

  const rightPanel = (
    <TargetDetailsManager
      selectedTarget={activeTarget}
      catalogId={catalogId}
      commonName={commonName}
      ra={raShared}
      dec={decShared}
      onCatalogIdChange={setCatalogId}
      onCommonNameChange={setCommonName}
      onSaveTarget={saveTarget}
      onDeleteTarget={handleDeleteTarget}
    />
  );

  return (
    <>
      <GenericDisplayLayout
        className="target-display-container"
        leftPanel={leftPanel}
        centerPanel={centerPanel}
        rightPanel={rightPanel}
      />
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Target"
        message={`Are you sure you want to delete ${activeTarget}?`}
        onConfirm={confirmDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </>
  );
};
