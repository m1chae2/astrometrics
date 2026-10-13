/**
 * @module DeepCatalogPrompt
 * @fileoverview First-launch prompt asking the user to download the deep-star catalog.
 *
 * The Planetarium draws faint stars from a copy of the Gaia DR3 catalog kept on
 * this computer. That copy is downloaded once, by a command-line script, and
 * until it has been the sky map shows only the bright bundled stars and the
 * user's own library. This dialog explains that and shows the command to run.
 * It does not start the download itself.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { DeepCatalogStatus } from '../../common/services/backendApi';

/** localStorage key remembering that the user chose "Don't remind me". */
export const DEEP_CATALOG_PROMPT_DISMISSED_KEY = 'planetariumDeepCatalogPromptDismissed';

/**
 * Props for DeepCatalogPrompt.
 */
interface Props {
  /** The catalog's status, or null while it is unknown (nothing is shown until it is known). */
  status: DeepCatalogStatus | null;
}

/**
 * Reads the saved "Don't remind me" choice.
 *
 * @returns {boolean} True if the user asked not to be reminded again.
 */
const readPromptDismissed = (): boolean => {
  try {
    return window.localStorage.getItem(DEEP_CATALOG_PROMPT_DISMISSED_KEY) === 'true';
  } catch {
    // Storage can be unavailable (private windows, blocked site data); treat as not dismissed.
    return false;
  }
};

/**
 * Dialog that offers the command for downloading the deep-star catalog.
 *
 * Appears when the catalog is missing or only partly downloaded. "Not now"
 * hides it until the next launch; "Don't remind me" hides it for good. It
 * never appears once the catalog is complete.
 *
 * @func DeepCatalogPrompt
 * @param {Props} props - Component props.
 * @returns {React.ReactElement | null} The dialog, or null when it should not be shown.
 */
export const DeepCatalogPrompt: React.FC<Props> = ({ status }) => {
  const [hiddenForNow, setHiddenForNow] = useState<boolean>(false);
  const [hiddenForGood, setHiddenForGood] = useState<boolean>(readPromptDismissed);
  const [copied, setCopied] = useState<boolean>(false);

  const shouldShow = status !== null && !status.complete && !hiddenForNow && !hiddenForGood;

  const hideForNow = useCallback(() => setHiddenForNow(true), []);

  const hideForGood = useCallback(() => {
    try {
      window.localStorage.setItem(DEEP_CATALOG_PROMPT_DISMISSED_KEY, 'true');
    } catch {
      // Not being able to save the choice only means the prompt comes back next launch.
    }
    setHiddenForGood(true);
  }, []);

  // Escape closes the dialog the same way "Not now" does.
  useEffect(() => {
    if (!shouldShow) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hideForNow();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [shouldShow, hideForNow]);

  const copyCommand = useCallback(async () => {
    if (!status) return;
    try {
      await navigator.clipboard.writeText(status.installCommand);
      setCopied(true);
    } catch {
      // Clipboard access can be denied; the command is on screen to select by hand.
    }
  }, [status]);

  if (!shouldShow || !status) return null;

  const isPartlyDownloaded = status.installed && status.pixels_total !== null;

  return (
    <div
      className="planetarium-deep-catalog-prompt"
      role="dialog"
      aria-labelledby="planetarium-deep-catalog-prompt-title"
    >
      <h3 id="planetarium-deep-catalog-prompt-title" className="planetarium-deep-catalog-prompt__title">
        Download the deep-star catalog
      </h3>
      <p className="planetarium-deep-catalog-prompt__text">
        The sky map draws faint stars from a copy of the Gaia DR3 catalog kept on this computer, so it never
        waits on the internet.{' '}
        {isPartlyDownloaded
          ? `Your copy is partly downloaded (${status.pixels_downloaded.toLocaleString()} of ${(status.pixels_total ?? 0).toLocaleString()} chunks), so stars in the missing parts are not shown yet.`
          : 'That copy has not been downloaded yet, so only the bright bundled stars and your own library stars are shown.'}
      </p>
      <p className="planetarium-deep-catalog-prompt__text">
        To {isPartlyDownloaded ? 'finish it' : 'download it'}, run this in a terminal, from the Astrometrics folder:
      </p>
      <div className="planetarium-deep-catalog-prompt__command-row">
        <code className="planetarium-deep-catalog-prompt__command">{status.installCommand}</code>
        <button type="button" className="planetarium-deep-catalog-prompt__button" onClick={copyCommand}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <p className="planetarium-deep-catalog-prompt__hint">
        It takes several hours and roughly 3 GB of disk. You can stop it with Ctrl-C at any time and run the same
        command again to carry on. Restart Astrometrics when it finishes.
      </p>
      <div className="planetarium-deep-catalog-prompt__actions">
        <button type="button" className="planetarium-deep-catalog-prompt__button" onClick={hideForNow}>
          Not now
        </button>
        <button type="button" className="planetarium-deep-catalog-prompt__button" onClick={hideForGood}>
          Don&apos;t remind me
        </button>
      </div>
    </div>
  );
};
