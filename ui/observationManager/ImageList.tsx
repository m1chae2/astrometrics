import React from 'react';
import '../common/styles/tables.css'; // Shared table styles

interface Props {
    items: any[];
    onRemove: (id: string, uuid: string) => void;
    onEdit?: (item: any) => void;
}

export const ImageList: React.FC<Props> = ({ items, onRemove, onEdit }) => {
    return (
        <div className="image-list-wrapper">
            <table className="data-table">
                <thead>
                    <tr>
                        <th className="col-idx">#</th>
                        <th>Type / Info</th>
                        <th>Count</th>
                        <th>Exposure</th>
                        <th>Delay</th>
                        <th>Filter</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map((item, idx) => (
                        <tr key={item.id}>
                            <td className="col-idx">{idx + 1}</td>
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
                            <td>{item.count}</td>
                            <td>{item.exposure}s</td>
                            <td>{item.delay || 0}s</td>
                            <td>{item.filter}</td>
                            <td>
                                {onEdit && (
                                    <button
                                        onClick={() => onEdit(item)}
                                        className="icon-btn image-list__edit-btn"
                                        title="Edit Item"
                                    >
                                        ✎
                                    </button>
                                )}
                                <button
                                    onClick={() => onRemove(item.targetId, item.uuid || item.id)}
                                    className="icon-btn-danger image-list__remove-btn"
                                    title="Remove Item"
                                >
                                    ✕
                                </button>
                            </td>
                        </tr>
                    ))}
                    {Array.from({ length: Math.max(0, 5 - items.length) }).map((_, idx) => (
                        <tr key={`empty-${idx}`}>
                            <td className="col-idx">{items.length + idx + 1}</td>
                            <td>&nbsp;</td>
                            <td>&nbsp;</td>
                            <td>&nbsp;</td>
                            <td>&nbsp;</td>
                            <td>&nbsp;</td>
                            <td>&nbsp;</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};
