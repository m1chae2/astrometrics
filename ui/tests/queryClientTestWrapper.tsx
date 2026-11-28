/**
 * @file queryClientTestWrapper.tsx
 * @description A test helper that gives a hook or component its own fresh React Query cache, so
 * answers kept by one test never leak into the next. It is not a test file itself.
 */

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/** A fresh query cache and a wrapper component that provides it. */
export interface QueryClientTestWrapper {
    /** The cache the wrapper provides, for tests that look inside it. */
    queryClient: QueryClient;
    /** Wraps the rendered hook or component in the cache's provider. */
    wrapper: React.FC<{ children: React.ReactNode }>;
}

/**
 * Makes a fresh query cache and a wrapper for `renderHook` or `render`.
 * @param queryClient The cache to use. A new one is made when none is given.
 * @return The cache and the wrapper.
 */
export function createQueryClientTestWrapper(queryClient: QueryClient = new QueryClient()): QueryClientTestWrapper {
    /**
     * Provides the test's query cache to its children.
     * @param props The children to wrap.
     * @return The children inside the provider.
     */
    const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    return { queryClient, wrapper };
}
