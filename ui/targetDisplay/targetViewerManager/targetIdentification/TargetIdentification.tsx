import React, { useState, useEffect } from 'react';

/** Props for the TargetIdentification component. */
interface Props {
  /** The catalog ID of the target. */
  catalogId?: string;
  /** The common name of the target. */
  commonName?: string;
  /** The Right Ascension coordinate. */
  ra?: string;
  /** The Declination coordinate. */
  dec?: string;
  /** Callback when the catalog ID input changes. */
  onCatalogIdChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  /** Callback when the common name input changes. */
  onCommonNameChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  /** Callback when the RA input changes. */
  onRaChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  /** Callback when the DEC input changes. */
  onDecChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
}

/**
 * Form component for editing target identification and coordinates.
 * Supports both controlled and uncontrolled usage for flexibility.
 */
export const TargetIdentification: React.FC<Props> = ({
  catalogId = '',
  commonName = '',
  ra = '',
  dec = '',
  onCatalogIdChange,
  onCommonNameChange,
  onRaChange,
  onDecChange,
}) => {
  // Support both controlled and uncontrolled usage: if parent doesn't
  // provide handlers, keep local state so the inputs remain editable.
  const [localCatalogId, setLocalCatalogId] = useState<string>(catalogId);
  const [localCommonName, setLocalCommonName] = useState<string>(commonName);
  const [localRa, setLocalRa] = useState<string>(ra);
  const [localDec, setLocalDec] = useState<string>(dec);

  useEffect(() => {
    setLocalCatalogId(catalogId);
  }, [catalogId]);

  useEffect(() => {
    setLocalCommonName(commonName);
  }, [commonName]);

  useEffect(() => {
    setLocalRa(ra);
  }, [ra]);

  useEffect(() => {
    setLocalDec(dec);
  }, [dec]);

  /** Handles change in the catalog ID input. */
  const handleCatalogChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    setLocalCatalogId(e.target.value);
    onCatalogIdChange?.(e);
  };

  /** Handles change in the common name input. */
  const handleCommonNameChange = (
    e: React.ChangeEvent<HTMLInputElement>
  ): void => {
    setLocalCommonName(e.target.value);
    onCommonNameChange?.(e);
  };

  /** Handles change in the Right Ascension input. */
  const handleRaChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    setLocalRa(e.target.value);
    onRaChange?.(e);
  };

  /** Handles change in the Declination input. */
  const handleDecChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    setLocalDec(e.target.value);
    onDecChange?.(e);
  };

  return (
    <div className="target-identification-frame">
      <div className="target-identification">
        <form
          className="target-identification__grid"
          onSubmit={(e) => e.preventDefault()}
        >
          <div className="target-identification__column">
            <div className="target-identification__pair">
              <label htmlFor="catalog-id" className="target-identification__label">
                Catalog ID:
              </label>
              <input
                type="text"
                id="catalog-id"
                name="catalog-id"
                className="target-input"
                value={onCatalogIdChange ? catalogId : localCatalogId}
                onChange={onCatalogIdChange ? onCatalogIdChange : handleCatalogChange}
              />
            </div>
            <div className="target-identification__pair">
              <label htmlFor="input-target-ra" className="target-identification__label">
                RA:
              </label>
              <input
                type="text"
                id="input-target-ra"
                name="ra"
                className="target-input"
                value={onRaChange ? ra : localRa}
                onChange={onRaChange ? onRaChange : handleRaChange}
              />
            </div>
          </div>
          <div className="target-identification__column">
            <div className="target-identification__pair">
              <label htmlFor="common-name" className="target-identification__label">
                Common Name:
              </label>
              <input
                type="text"
                id="common-name"
                name="common-name"
                className="target-input"
                value={onCommonNameChange ? commonName : localCommonName}
                onChange={
                  onCommonNameChange ? onCommonNameChange : handleCommonNameChange
                }
              />
            </div>
            <div className="target-identification__pair">
              <label htmlFor="input-target-dec" className="target-identification__label">
                DEC:
              </label>
              <input
                type="text"
                id="input-target-dec"
                name="dec"
                className="target-input"
                value={onDecChange ? dec : localDec}
                onChange={onDecChange ? onDecChange : handleDecChange}
              />
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
