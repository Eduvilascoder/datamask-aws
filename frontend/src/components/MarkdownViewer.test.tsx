import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import MarkdownViewer from './MarkdownViewer';

describe('MarkdownViewer', () => {
  it('renderiza encabezados y párrafos', () => {
    render(<MarkdownViewer content={'# Título\n\nUn párrafo.'} />);
    expect(screen.getByText('Título')).toBeInTheDocument();
    expect(screen.getByText('Un párrafo.')).toBeInTheDocument();
  });

  it('renderiza tablas GFM', () => {
    const md = '| A | B |\n|---|---|\n| 1 | 2 |';
    render(<MarkdownViewer content={md} />);
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renderiza enlaces que abren en nueva pestaña', () => {
    render(<MarkdownViewer content={'[docs](https://example.com)'} />);
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('href', 'https://example.com');
    expect(link).toHaveAttribute('target', '_blank');
  });
});
