import React, { useEffect, useState, useRef } from 'react';
import { fetchTargetObject, addTargetData, fetchTargetFrameHeader, fetchTargetFiles } from '../../common/services/targetService';
import { fetchAvailableCameras } from '../../common/services/systemService';
import { reportError } from '../../common/utils/reportError';
import { on as onEvent, emit as emitEvent } from '../../common/utils/eventBus';
import { CameraSelectionDialog } from './CameraSelectionDialog';
import { FrameFilterGroup, TargetDetails } from './targetDetails/TargetDetails';
import { SectionPanel } from '../../common/components/SectionPanel';
import { ListActions } from '../../common/components/ListActions';
import '../../common/styles/panels.css';

/**
 * Props for the TargetDetailsManager component.
 */
export interface TargetDetailsManagerProps {
  /** The ID of the currently selected target. */
  selectedTarget?: string;
  /** Primary catalog identifier (e.g., M31, NGC 1234). */
  catalogId?: string;
  /** Conversational name for the target (e.g., Andromeda Galaxy). */
  commonName?: string;
  /** Right Ascension of the target, shared from the context. */
  ra?: string;
  /** Declination of the target, shared from the context. */
  dec?: string;
  /** Callback fired when the catalog ID is edited. */
  onCatalogIdChange?: (value: string) => void;
  /** Callback fired when the common name is edited. */
  onCommonNameChange?: (value: string) => void;
  /** Callback to trigger persisting the target to disk. */
  onSaveTarget?: () => void;
  /** Callback to trigger deleting the target from the library. */
  onDeleteTarget?: () => void;
}

/**
 * Manager component for target details.
 * Orchestrates fetching target data, parsing FITS headers for exposure times,
 * grouping frames by filter, and handling new image additions via the system file picker.
 *
 * @param props - TargetDetailsManagerProps controlling the currently viewed target and edit handlers.
 */
export const TargetDetailsManager: React.FC<TargetDetailsManagerProps> = ({
  selectedTarget,
  catalogId,
  commonName,
  ra: propRa,
  dec: propDec,
  onCatalogIdChange,
  onCommonNameChange,
  onSaveTarget,
  onDeleteTarget,
}) => {
  const [camera, setCamera] = useState<string>('');
  const [telescope, setTelescope] = useState<string>('');
  const [expTime, setExpTime] = useState<string>('');
  const [filterGroups, setFilterGroups] = useState<FrameFilterGroup[]>([]);
  const [ra, setRa] = useState<string>('');
  const [dec, setDec] = useState<string>('');

  // Camera Selection State
  const [availableCameras, setAvailableCameras] = useState<string[]>([]);
  const [showCameraDialog, setShowCameraDialog] = useState<boolean>(false);
  const [selectedAddCamera, setSelectedAddCamera] = useState<string>('');

  const cacheRef = useRef<Map<string, unknown>>(new Map());

  // REQ: TGT-3: Target Metadata Editor
  const updateTargetState = React.useCallback(async (obj: any) => {
    setCamera(obj.mainCamera ?? '');
    setTelescope(obj.mainScope ?? '');
    setRa(obj.ra ?? '');
    setDec(obj.dec ?? '');

    const idVal = (obj.id ?? obj.ID ?? obj.name ?? selectedTarget ?? '') as string;
    const commonVal = (obj.common_name ?? obj.commonName ?? obj.common ?? '') as string;

    onCatalogIdChange?.(idVal);
    onCommonNameChange?.(commonVal);

    // Compute frame counts grouped by filter name and exposure duration
    const groupsMap: Record<string, Record<number, number>> = {};

    const processFrameList = (frameList: any[]) => {
      for (const frame of frameList) {
        let filterName = 'Light';
        if (frame.filter) {
          if (typeof frame.filter === 'string') {
            filterName = frame.filter;
          } else if (typeof frame.filter === 'object' && frame.filter.name) {
            filterName = frame.filter.name;
          }
        }
        if (!filterName || filterName.toUpperCase() === 'NONE') {
          filterName = 'Light';
        }

        const exp = typeof frame.exposure === 'number' ? frame.exposure : parseFloat(String(frame.exposure));
        if (!isNaN(exp) && exp > 0) {
          const roundedExp = Math.round(exp);
          if (!groupsMap[filterName]) {
            groupsMap[filterName] = {};
          }
          groupsMap[filterName][roundedExp] = (groupsMap[filterName][roundedExp] || 0) + 1;
        }
      }
    };

    if (Array.isArray(obj.frames) && obj.frames.length > 0) {
      processFrameList(obj.frames);
    }

    if (Object.keys(groupsMap).length === 0 && selectedTarget) {
      try {
        const res = await fetchTargetFiles(selectedTarget);
        if (res && Array.isArray(res.files)) {
          processFrameList(res.files);
        }
      } catch {
        // Ignore fetchTargetFiles error
      }
    }

    const parsedFilterGroups: FrameFilterGroup[] = Object.entries(groupsMap).map(([filterName, expMap]) => ({
      filterName,
      exposures: Object.entries(expMap)
        .sort(([expA], [expB]) => Number(expB) - Number(expA))
        .map(([expStr, count]) => ({
          duration: Number(expStr),
          count,
        })),
    }));

    setFilterGroups(parsedFilterGroups);

    const processedPath = obj.processedImage || obj.stackedImage || obj.stackedSpectralTarget || obj.processed_image || obj.stacked_image || obj.stacked_spectral_target;

    let exptimeSec: number | null = null;
    const isFits = typeof processedPath === 'string' && (processedPath.toLowerCase().endsWith('.fits') || processedPath.toLowerCase().endsWith('.fit'));

    if (processedPath && isFits && selectedTarget) {
      try {
        const entries = await fetchTargetFrameHeader(selectedTarget, processedPath);
        if (entries) {
          const exptimeEntry = entries.find(e => e.key.toUpperCase() === 'EXPTIME');
          if (exptimeEntry && exptimeEntry.value != null) {
            const parsed = parseFloat(String(exptimeEntry.value));
            if (!isNaN(parsed) && parsed > 0) {
              exptimeSec = parsed;
            }
          }
        }
      } catch {
        // Header fetch failed
      }
    }

    if (exptimeSec === null || exptimeSec === 0) {
      const exp = obj.exposureTime ?? obj.exposure_sec ?? obj.exposureSec ?? obj.exposure ?? obj.totalExposure ?? 0;
      exptimeSec = typeof exp === 'number' ? exp : parseFloat(String(exp)) || 0;
    }

    if (exptimeSec >= 3600) {
      setExpTime(`${(exptimeSec / 3600).toFixed(1)}h`);
    } else if (exptimeSec >= 60) {
      setExpTime(`${(exptimeSec / 60).toFixed(0)}m`);
    } else {
      setExpTime(`${exptimeSec}s`);
    }

    try {
      cacheRef.current.set(selectedTarget || '', obj);
    } catch {
      // Ignore cache.
    }
  }, [selectedTarget]);

  useEffect(() => {
    // Fetch available cameras on mount
    fetchAvailableCameras().then(cams => {
      setAvailableCameras(cams);
      if (cams.length > 0) setSelectedAddCamera(cams[0]);
    });

    // Clear cache when targets are updated elsewhere in the app.
    const detach = onEvent('targetsUpdated', () => {
      try {
        cacheRef.current.clear();
      } catch {
        // Ignore cache clear failures.
      }
    });

    let cancelled = false;
    if (!selectedTarget) {
      setCamera('');
      setTelescope('');
      setExpTime('');
      setRa('');
      setDec('');
      onCatalogIdChange?.('');
      onCommonNameChange?.('');
      return;
    }

    const cached = cacheRef.current.get(selectedTarget);
    if (cached) {
      const cachedObject = cached as any;
      updateTargetState(cachedObject);
    } else {
      fetchTargetObject(selectedTarget)
        .then((obj) => {
          if (cancelled || !obj) return;
          updateTargetState(obj);
        })
        .catch((err) => {
          reportError(err, 'TargetDetailsManager');
        });
    }

    return () => {
      cancelled = true;
      try {
        detach();
      } catch {
        // Ignore detacher failures.
      }
    };
  }, [selectedTarget, updateTargetState]);

  /** Opens a file picker and adds selected images to the target. */
  const processAddImage = async (): Promise<void> => {
    if (!selectedTarget) return;
    try {
      let paths: string[];

      if (window.astrometrics?.dialog?.openFile) {
        // Browser File objects don't expose a filesystem path under this
        // app's contextIsolation: true / nodeIntegration: false Electron
        // config, so the native dialog is the only way to get real
        // absolute paths the backend can resolve on disk.
        const filePaths = await window.astrometrics.dialog.openFile({
          properties: ['openFile', 'multiSelections'],
        });
        if (!filePaths || filePaths.length === 0) return;
        paths = filePaths;
      } else {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = '*/*';

        const promise: Promise<FileList | null> = new Promise((resolve) => {
          input.onchange = () => resolve(input.files);
          input.click();
        });

        const files = await promise;
        if (!files || files.length === 0) return;

        paths = Array.from(files).map((f) => f.name);
      }

      await addTargetData(paths, selectedTarget, selectedAddCamera);
      setShowCameraDialog(false);

      const obj = await fetchTargetObject(selectedTarget).catch(() => null);
      if (obj) {
        updateTargetState(obj);
      }
      emitEvent('targetsUpdated');
    } catch (err) {
      reportError(err, 'backend');
    }
  };

  const handleAddImageRequest = () => {
    setShowCameraDialog(true);
  };

  return (
    <div className="panel-group">
      <SectionPanel title="Information" className="flex-fill">
        <TargetDetails
          catalogId={catalogId}
          commonName={commonName}
          camera={camera}
          telescope={telescope}
          expTime={expTime}
          filterGroups={filterGroups}
          ra={propRa || ra}
          dec={propDec || dec}
          onCatalogIdChange={onCatalogIdChange}
          onCommonNameChange={onCommonNameChange}
        />
      </SectionPanel>

      <SectionPanel title="Target Controls" className="flex-auto">
        <ListActions>
          <button className="btn" onClick={handleAddImageRequest}>Add Processed Image</button>
          <button className="btn" onClick={onSaveTarget}>Save Target</button>
          <button className="btn" onClick={onDeleteTarget}>Delete Target</button>
        </ListActions>
      </SectionPanel>

      <CameraSelectionDialog
        isOpen={showCameraDialog}
        onClose={() => setShowCameraDialog(false)}
        onConfirm={processAddImage}
        availableCameras={availableCameras}
        selectedCamera={selectedAddCamera}
        onSelectCamera={setSelectedAddCamera}
      />
    </div>
  );
};
