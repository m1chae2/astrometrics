import React, { useState, useEffect } from 'react';
import { BaseModal } from './BaseModal';
import { SectionPanel } from './SectionPanel';
import { EmptyState } from './EmptyState';
import { fetchTargetFrameHeader } from '../services/targetService';
import { FitsHeaderEntry } from '../types/backendTypes';
import { useToast } from '../hooks/useToast';
import '../styles/layout.css';

interface FitsHeaderModalProps {
    isOpen: boolean;
    onClose: () => void;
    targetId: string | null;
    filePath: string | null;
}

/**
 * Modal component to display FITS header information.
 * Shares common infrastructure with IngestFramesModal (BaseModal, SectionPanel).
 */
export const FitsHeaderModal: React.FC<FitsHeaderModalProps> = ({
    isOpen,
    onClose,
    targetId,
    filePath
}) => {
    const [headerData, setHeaderData] = useState<FitsHeaderEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const toast = useToast();

    useEffect(() => {
        if (isOpen && filePath && targetId) {
            setLoading(true);
            setError(null);
            fetchTargetFrameHeader(targetId, filePath)
                .then(data => {
                    if (data) {
                        setHeaderData(data);
                    } else {
                        setError('Could not load header data.');
                        toast.show('Failed to load FITS header.', 'error');
                    }
                })
                .catch(err => {
                    setError('Could not load header data.');
                    toast.show(String(err), 'error');
                })
                .finally(() => {
                    setLoading(false);
                });
        } else {
            setHeaderData([]);
            setSearchTerm('');
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, filePath, targetId]);

    const filteredHeader = headerData.filter(entry =>
        entry.key.toLowerCase().includes(searchTerm.toLowerCase()) ||
        String(entry.value ?? '').toLowerCase().includes(searchTerm.toLowerCase()) ||
        (entry.comment ?? '').toLowerCase().includes(searchTerm.toLowerCase())
    );

    const footerButtons = (
        <button className="btn btn--secondary" onClick={onClose}>Close</button>
    );

    const filename = filePath ? filePath.split(/[/\\]/).pop() : 'Unknown File';

    return (
        <BaseModal
            isOpen={isOpen}
            onClose={onClose}
            title={`FITS Header: ${filename}`}
            footer={footerButtons}
            className="modal--medium"
        >
            <div className="modal-layout modal-layout--col">
                <SectionPanel
                    title="Header Keywords"
                    headerContent={
                        <input
                            type="text"
                            className="entry fits-header-search"
                            placeholder="Filter..."
                            value={searchTerm}
                            onChange={e => setSearchTerm(e.target.value)}
                        />
                    }
                >
                    {loading ? (
                        <div className="loading-container">Loading header data...</div>
                    ) : error ? (
                        <EmptyState variant="error" message={error} />
                    ) : (
                        <div className="fits-header-container">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th className="fits-header-key">Keyword</th>
                                        <th className="fits-header-val">Value</th>
                                        <th>Comment</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredHeader.map((entry, index) => (
                                        <tr key={`${entry.key}-${index}`}>
                                            <td className="text-highlight"><b>{entry.key}</b></td>
                                            <td className="text-success">{entry.value}</td>
                                            <td className="text-muted">{entry.comment}</td>
                                        </tr>
                                    ))}
                                    {filteredHeader.length === 0 && (
                                        <tr>
                                            <td colSpan={3} className="text-center">No matching keywords found.</td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    )}
                </SectionPanel>
            </div>
        </BaseModal>
    );
};
