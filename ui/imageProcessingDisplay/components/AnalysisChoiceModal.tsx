import React from 'react';
import { BaseModal } from '../../common/components/BaseModal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSelectFinalStack: () => void;
  onSelectSpectralStack: () => void;
}

export const AnalysisChoiceModal: React.FC<Props> = ({
  isOpen,
  onClose,
  onSelectFinalStack,
  onSelectSpectralStack
}) => {
  if (!isOpen) return null;

  return (
    <BaseModal title="Choose Stack to Analyze" isOpen={isOpen} onClose={onClose}>
      <div className="analysis-modal-content">
        <p>This target has both a Final Stack and a Spectral Stack available. Which one would you like to analyze?</p>
        <div className="analysis-modal-actions">
          <button
            className="btn analysis-modal-btn"
            onClick={() => {
              onSelectFinalStack();
              onClose();
            }}
          >
            Final Stack
          </button>
          <button
            className="btn btn--primary analysis-modal-btn"
            onClick={() => {
              onSelectSpectralStack();
              onClose();
            }}
          >
            Spectral Stack
          </button>
        </div>
      </div>
    </BaseModal>
  );
};
