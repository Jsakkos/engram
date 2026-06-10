import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SvTopBar } from '../SvTopBar';
import { buildNavItems } from '../../../navigation';

function renderTopBar(navItems = buildNavItems({})) {
    return render(
        <MemoryRouter>
            <SvTopBar
                isConnected
                version="0.0.0-test"
                navItems={navItems}
                onSettingsClick={() => {}}
            />
        </MemoryRouter>,
    );
}

afterEach(() => {
    vi.restoreAllMocks();
});

describe('SvTopBar nav', () => {
    it('renders without duplicate-key warnings when REVIEW has no destination', () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        renderTopBar();
        const dupKeyCalls = consoleError.mock.calls.filter((args) =>
            String(args[0]).includes('same key'),
        );
        expect(dupKeyCalls).toHaveLength(0);
    });

    it('renders a disabled (non-link) REVIEW tab when nothing needs review', () => {
        renderTopBar();
        expect(screen.queryByRole('link', { name: /review/i })).not.toBeInTheDocument();
        expect(screen.getByText('REVIEW').closest('[aria-disabled="true"]')).toBeTruthy();
    });

    it('renders REVIEW as a link when a job awaits review', () => {
        renderTopBar(buildNavItems({ firstReviewJobId: 6, reviewCount: 1 }));
        expect(screen.getByRole('link', { name: /review/i })).toHaveAttribute(
            'href',
            '/review/6',
        );
    });
});
