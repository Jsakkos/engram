import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DashboardEmptyState } from './DashboardEmptyState';

describe('DashboardEmptyState', () => {
    it('shows a welcoming first-run variant with an Import action when there is no job history', () => {
        const onImport = vi.fn();
        render(<DashboardEmptyState filter="active" firstRun onImport={onImport} />);
        expect(screen.getByTestId('sv-empty-heading')).not.toHaveTextContent(/no active operations/i);
        const body = screen.getByTestId('sv-empty-body');
        expect(body).toHaveTextContent(/insert a disc/i);
        expect(body).toHaveTextContent(/import/i);
        expect(body).not.toHaveTextContent(/all operations complete/i);
        fireEvent.click(screen.getByRole('button', { name: /import/i }));
        expect(onImport).toHaveBeenCalledTimes(1);
    });

    it('links the first-run variant to the getting-started docs', () => {
        render(<DashboardEmptyState filter="all" firstRun onImport={vi.fn()} />);
        const link = screen.getByRole('link', { name: /getting started/i });
        expect(link).toHaveAttribute(
            'href',
            'https://jsakkos.github.io/engram/getting-started/installation/',
        );
    });

    it('keeps the existing copy once any job exists', () => {
        render(<DashboardEmptyState filter="active" firstRun={false} onImport={vi.fn()} />);
        expect(screen.getByTestId('sv-empty-heading')).toHaveTextContent('› No active operations');
        expect(screen.getByTestId('sv-empty-body')).toHaveTextContent(
            'All operations complete. Insert a disc to start a new job.',
        );
        expect(screen.queryByRole('button', { name: /import/i })).not.toBeInTheDocument();
    });

    it.each([
        ['completed', '› No completed archives'],
        ['all', '› No discs detected'],
    ] as const)('keeps the %s filter heading', (filter, heading) => {
        render(<DashboardEmptyState filter={filter} firstRun={false} onImport={vi.fn()} />);
        expect(screen.getByTestId('sv-empty-heading')).toHaveTextContent(heading);
    });
});
