/**
 * @file PlanningContext.tsx
 * @description Provides a global context for managing imaging sequence planning.
 * This allows the imaging plan to be shared across components and persisted during session navigation.
 * REQ: OBM-1: Session Planning
 */
import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';

export interface PlanItem {
    id: string;
    type: string;
    count: number;
    exposure: number;
    filter: string;
    delay: number;
    coordinates?: {
        ra: number;
        dec: number;
    };
    dither?: {
        enabled: boolean;
        everyN: number;
        pixels: number;
    };
    name?: string;
}

export interface PlanningContextValue {
    planItems: PlanItem[];
    addItem: (type: string, count: number, exposure: number, filter: string, delay: number) => void;
    addItems: (items: PlanItem[]) => void;
    updateItem: (id: string, type: string, count: number, exposure: number, filter: string, delay: number) => void;
    removeItem: (id: string) => void;
    clearItems: () => void;
    setItems: (items: PlanItem[]) => void;
}

const PlanningContext = createContext<PlanningContextValue | undefined>(undefined);

export const PlanningProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [planItems, setPlanItems] = useState<PlanItem[]>([]);

    /**
     * Adds a single item to the imaging plan.
     * REQ: OBM-2.7: The display SHALL allow adding configured items to a "Plan List" before committing to the queue.
     */
    const addItem = useCallback((type: string, count: number, exposure: number, filter: string, delay: number) => {
        const newItem: PlanItem = {
            id: crypto.randomUUID(),
            type,
            count,
            exposure,
            filter,
            delay,
        };
        setPlanItems((prev) => [...prev, newItem]);
    }, []);

    /**
     * Adds multiple items to the imaging plan (e.g. from a mosaic).
     * REQ: OBM-3.5: The display SHALL automatically generate sequence items for each mosaic panel based on current image configuration.
     */
    const addItems = useCallback((items: PlanItem[]) => {
        setPlanItems((prev) => [...prev, ...items]);
    }, []);

    const updateItem = useCallback((id: string, type: string, count: number, exposure: number, filter: string, delay: number) => {
        setPlanItems((prev) => prev.map(item =>
            item.id === id ? { ...item, type, count, exposure, filter, delay } : item
        ));
    }, []);

    const removeItem = useCallback((id: string) => {
        setPlanItems((prev) => prev.filter((item) => item.id !== id));
    }, []);

    const clearItems = useCallback(() => {
        setPlanItems([]);
    }, []);

    const setItems = useCallback((items: PlanItem[]) => {
        setPlanItems(items);
    }, []);

    const value = {
        planItems,
        addItem,
        addItems,
        updateItem,
        removeItem,
        clearItems,
        setItems,
    };

    return (
        <PlanningContext.Provider value={value}>
            {children}
        </PlanningContext.Provider>
    );
};

export const usePlanningContext = () => {
    const context = useContext(PlanningContext);
    if (context === undefined) {
        throw new Error('usePlanningContext must be used within a PlanningProvider');
    }
    return context;
};
