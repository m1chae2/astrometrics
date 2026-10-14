/**
 * @file test_docViewer.test.tsx
 * @description Unit tests for DocViewer component, verifying markdown rendering,
 * link interception, external URL handling, internal topic navigation, and admonition formatting.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DocViewer, DocTopicItem } from '../commandConsole/DocViewer';

describe('DocViewer Component', () => {
  const sampleTopics: DocTopicItem[] = [
    {
      id: 'api/index',
      title: 'Python API Reference',
      path: 'api/index.rst',
      category: 'API Reference',
    },
    {
      id: 'api/astrometricslib',
      title: 'Astrometrics Library (astrometricslib)',
      path: 'api/astrometricslib.rst',
      category: 'API Reference',
    },
    {
      id: 'api/wayfindinglib',
      title: 'Wayfinding Library (wayfindinglib)',
      path: 'api/wayfindinglib.rst',
      category: 'API Reference',
    },
    {
      id: 'Getting_Started.md',
      title: 'Getting Started',
      path: 'Getting_Started.md',
      category: 'General',
    },
    {
      id: 'Installation.md',
      title: 'Installation',
      path: 'Installation.md',
      category: 'General',
    },
    {
      id: 'library_design/Astrometrics_Library_Architecture.md',
      title: 'Library Architecture',
      path: 'library_design/Astrometrics_Library_Architecture.md',
      category: 'library_design',
    },
    {
      id: 'user_interface/user_guides/User_Manual.md',
      title: 'User Manual',
      path: 'user_interface/user_guides/User_Manual.md',
      category: 'user_interface',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Test rendering document content and preprocessing Sphinx/MyST admonitions into blockquotes.
   */
  it('renders content with transformed admonitions and headings', () => {
    const markdownContent = `
# Getting Started

Introduction text.

:::{note}
Remember to configure your mount properly.
:::
`;
    render(
      <DocViewer
        topics={sampleTopics}
        selectedTopicId="Getting_Started.md"
        content={markdownContent}
        onSelectTopic={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { level: 1, name: /Getting Started/i })).toBeInTheDocument();
    expect(screen.getByText(/Remember to configure your mount properly/i)).toBeInTheDocument();
  });

  /**
   * Test internal link interception: clicking a relative link resolves to a known topic ID
   * and invokes onSelectTopic rather than navigating or reloading the browser.
   */
  it('intercepts internal links and calls onSelectTopic', () => {
    const onSelectTopicMock = vi.fn();
    const markdownContent = `
[Read Installation Guide](Installation.md)
[Check User Manual](user_interface/user_guides/User_Manual.md)
`;

    render(
      <DocViewer
        topics={sampleTopics}
        selectedTopicId="Getting_Started.md"
        content={markdownContent}
        onSelectTopic={onSelectTopicMock}
      />
    );

    const installLink = screen.getByText('Read Installation Guide');
    fireEvent.click(installLink);
    expect(onSelectTopicMock).toHaveBeenCalledWith('Installation.md');

    const manualLink = screen.getByText('Check User Manual');
    fireEvent.click(manualLink);
    expect(onSelectTopicMock).toHaveBeenCalledWith('user_interface/user_guides/User_Manual.md');
  });

  /**
   * Test Python API Reference link resolution: links pointing to .rst files
   * (e.g. api/astrometricslib.rst or generated/astrometricslib.Astrometrics.rst)
   * resolve correctly to the API topic.
   */
  it('resolves Python API Reference .rst links correctly', () => {
    const onSelectTopicMock = vi.fn();
    const markdownContent = `
[Astrometrics Library](api/astrometricslib.rst)
[Wayfinding Library](api/wayfindinglib.rst)
[Python API Reference](api/index.rst)
[Astrometrics Class](generated/astrometricslib.Astrometrics.rst)
`;

    render(
      <DocViewer
        topics={sampleTopics}
        selectedTopicId="Getting_Started.md"
        content={markdownContent}
        onSelectTopic={onSelectTopicMock}
      />
    );

    fireEvent.click(screen.getByText('Astrometrics Library'));
    expect(onSelectTopicMock).toHaveBeenCalledWith('api/astrometricslib');

    fireEvent.click(screen.getByText('Wayfinding Library'));
    expect(onSelectTopicMock).toHaveBeenCalledWith('api/wayfindinglib');

    fireEvent.click(screen.getByText('Python API Reference'));
    expect(onSelectTopicMock).toHaveBeenCalledWith('api/index');

    fireEvent.click(screen.getByText('Astrometrics Class'));
    expect(onSelectTopicMock).toHaveBeenCalledWith('api/generated/astrometricslib.Astrometrics');
  });

  /**
   * Test external links: opening external http(s) URLs delegates to window.open with _blank.
   */
  it('intercepts external URLs and delegates to window.open without page navigation', () => {
    const windowOpenSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const onSelectTopicMock = vi.fn();

    const markdownContent = `
[KStars Documentation](https://docs.kde.org/kstars)
`;

    render(
      <DocViewer
        topics={sampleTopics}
        selectedTopicId="Getting_Started.md"
        content={markdownContent}
        onSelectTopic={onSelectTopicMock}
      />
    );

    const externalLink = screen.getByText('KStars Documentation');
    fireEvent.click(externalLink);

    expect(windowOpenSpy).toHaveBeenCalledWith('https://docs.kde.org/kstars', '_blank', 'noopener,noreferrer');
    expect(onSelectTopicMock).not.toHaveBeenCalled();
  });
});
