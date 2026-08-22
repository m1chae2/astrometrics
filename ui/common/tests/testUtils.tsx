
import React, { ReactElement } from 'react';
import { render, RenderOptions } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ToastProvider } from '../components/ToastProvider';
import { TargetProvider } from '../context/TargetContext';
import userEvent from '@testing-library/user-event';
// Import other providers as needed, e.g., ImageProcessingProvider
// Since ImageProcessingProvider might not be exported as a standalone provider in the codebase yet,
// we might need to wrap it or mock the context if we can't easily instantiate it.
// However, the goal is to use REAL implementation.
// Let's assume for now we just wrap with ToastProvider and Router.

interface ExtendedRenderOptions extends Omit<RenderOptions, 'queries'> {
    route?: string;
    initialState?: Record<string, any>; // Placeholder for future state injection
}

export const renderWithProviders = (
    ui: ReactElement,
    { route = '/', ...options }: ExtendedRenderOptions = {}
) => {
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter initialEntries={[route]}>
            <ToastProvider>
                <TargetProvider>
                    {children}
                </TargetProvider>
            </ToastProvider>
        </MemoryRouter>
    );

    return {
        user: userEvent.setup(),
        ...render(ui, { wrapper: Wrapper, ...options }),
    };
};

export * from '@testing-library/react';
