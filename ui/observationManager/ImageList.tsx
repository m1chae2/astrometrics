/**
 * @file ImageList.tsx
 * @description The Image List table: the package's exposure rows, one per line, with the frame
 * type, count, exposure, delay and filter, and buttons to edit or remove a row. Numbers line up on
 * the right and times read "60 s". A package with no rows says how to add one.
 */
import React from 'react';
import '../common/styles/tables.css'; // Shared table styles

interface Props {
    items: any[];
    onRemove: (id: string, uuid: string) => void;
    onEdit?: (item: any) => void;
}

/**
 * Shows the exposure rows of the package.
 * @param props The rows and the edit and remove callbacks.
 * @return The table.
 */
export const ImageList: React.FC<Props> = ({ items, onRemove, onEdit }) => {
    return (
        <div className="image-list-wrapper">
            <table className="data-table image-list__table" aria-label="Exposure rows">
                <thead>
                    <tr>
                        <th className="col-idx image-list__number">#</th>
                        <th>Type</th>
                        <th className="image-list__number">Count</th>
                        <th className="image-list__number">Exposure</th>
                        <th className="image-list__number">Delay</th>
                        <th>Filter</th>
                        <th className="image-list__actions" aria-label="Actions" />
                    </tr>
                </thead>
                <tbody>
                    {items.map((item, idx) => (
                        <tr key={item.id}>
                            <td className="col-idx image-list__number">{idx + 1}</td>
                            <td>
                                <div className="image-list-info">
                                    <span className={item.name ? 'text-bold' : ''}>
                                        {item.name || item.type}
                                    </span>
                                    {item.coordinates && (
                                        <span className="text-muted">
                                            RA:{item.coordinates.ra.toFixed(3)} Dec:{item.coordinates.dec.toFixed(3)}
                                        </span>
                                    )}
                                    {item.name && (
                                        <span className="text-desc">
                                            {item.type}
                                        </span>
                                    )}
                                </div>
                            </td>
                            <td className="image-list__number">{item.count}</td>
                            <td className="image-list__number">{item.exposure} s</td>
                            <td className="image-list__number">{item.delay || 0} s</td>
                            <td>{item.filter}</td>
                            <td className="image-list__actions">
                                {onEdit && (
                                    <button
                                        type="button"
                                        onClick={() => onEdit(item)}
                                        className="icon-btn image-list__edit-btn"
                                        title="Edit row"
                                        aria-label={`Edit row ${idx + 1}`}
                                    >
                                        ✎
                                    </button>
                                )}
                                <button
                                    type="button"
                                    onClick={() => onRemove(item.targetId, item.uuid || item.id)}
                                    className="icon-btn-danger image-list__remove-btn"
                                    title="Remove row"
                                    aria-label={`Remove row ${idx + 1}`}
                                >
                                    ✕
                                </button>
                            </td>
                        </tr>
                    ))}
                    {items.length === 0 && (
                        <tr>
                            <td colSpan={7} className="image-list__empty">
                                No exposure rows yet. Add one with the Image Planner above.
                            </td>
                        </tr>
                    )}
                </tbody>
            </table>
        </div>
    );
};
