/**
 * @module useTargetPackage
 * @fileoverview Gives the Planetarium its two package actions: "Add to observation package" and
 * "Send framing to package". Both work on the package of the chosen target. When the target has no
 * package yet, the backend makes one (`plan:create_package`), and it also adds a missing catalog
 * target to the library. A refusal stays as visible text until the user dismisses it.
 */

import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { BackendError } from '../../common/services/backendError';
import { useToast } from '../../common/hooks/useToast';
import { PACKAGE_LIBRARY_QUERY_KEY } from '../../common/queries/packageLibraryQueryKeys';
import { PackageStatus } from '../../common/types/backendTypes';
import type { Framing, ObservationPackage } from '../../common/types/backendTypes';

/** The package found or made for a target, and whether this call made it. */
interface EnsuredPackage {
  /** The package. */
  package: ObservationPackage;
  /** True when this call created it. */
  wasCreated: boolean;
}

/**
 * Chooses the package to use from a target's packages: a planned one, else a suggested one.
 *
 * @param packages The target's packages, in the order the backend returned them.
 * @returns The first planned package, else the first suggested one, else null.
 */
function pickExistingPackage(packages: ObservationPackage[]): ObservationPackage | null {
  return (
    packages.find((candidate) => candidate.status === PackageStatus.PLANNED) ??
    packages.find((candidate) => candidate.status === PackageStatus.SUGGESTED) ??
    null
  );
}

/**
 * Provides the package actions for one target.
 *
 * @param targetName The chosen target's name, or null when none is chosen.
 * @param isLibraryTarget Whether the target is already in the library (only changes the toast words).
 * @returns The two actions, the working flag, and the refusal text with its dismiss function.
 */
export function useTargetPackage(targetName: string | null, isLibraryTarget: boolean) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [isWorking, setIsWorking] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  /** Finds the target's package, or makes one. Throws what the backend refuses with. */
  const ensurePackage = useCallback(async (target: string): Promise<EnsuredPackage> => {
    const packages = await callBackend('plan:list_packages', { target }, { silent: true });
    const existing = pickExistingPackage(Array.isArray(packages) ? packages : []);
    if (existing) return { package: existing, wasCreated: false };
    const created = await callBackend('plan:create_package', { target }, { silent: true });
    void queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
    return { package: created, wasCreated: true };
  }, [queryClient]);

  /** Adds the target to the observation packages, or says it already has one. */
  const addToPackage = useCallback(async (): Promise<void> => {
    if (targetName === null || isWorking) return;
    setIsWorking(true);
    setRefusal(null);
    try {
      const result = await ensurePackage(targetName);
      if (!result.wasCreated) {
        const statusWord = result.package.status === PackageStatus.SUGGESTED ? 'suggested' : 'planned';
        toast.show(`${targetName} already has a ${statusWord} package`, 'info');
      } else if (isLibraryTarget) {
        toast.show(`Added ${targetName} to the observation packages`, 'success');
      } else {
        toast.show(`${targetName} is now a library target and has a package`, 'success');
      }
    } catch (error) {
      setRefusal(`Could not add ${targetName} to the observation packages: ${(error as Error).message}`);
    } finally {
      setIsWorking(false);
    }
  }, [targetName, isWorking, isLibraryTarget, ensurePackage, toast]);

  /** Writes the framing to the target's package, making the package first when needed. */
  const sendFramingToPackage = useCallback(async (framing: Framing): Promise<void> => {
    if (targetName === null || isWorking) return;
    setIsWorking(true);
    setRefusal(null);
    try {
      const result = await ensurePackage(targetName);
      await callBackend(
        'plan:edit_package',
        {
          package_id: result.package.id,
          framing: { ...framing },
          expected_revision: result.package.revision,
        },
        { silent: true },
      );
      void queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
      toast.show(`Sent the framing to the package of ${targetName}`, 'success');
    } catch (error) {
      if (error instanceof BackendError && error.code === 'conflict') {
        setRefusal(`The package of ${targetName} changed in another window. Nothing was sent. Try again.`);
      } else {
        setRefusal(`The package did not accept the framing: ${(error as Error).message}`);
      }
    } finally {
      setIsWorking(false);
    }
  }, [targetName, isWorking, ensurePackage, queryClient, toast]);

  const dismissRefusal = useCallback(() => setRefusal(null), []);

  return { isWorking, refusal, addToPackage, sendFramingToPackage, dismissRefusal };
}
