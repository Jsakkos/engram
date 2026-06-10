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

    it('renders REVIEW as a disabled link exposing the reason to assistive tech', () => {
        renderTopBar();
        // The canonical disabled-link pattern: role="link" + aria-disabled (a bare
        // span's aria-disabled is ignored by most AT) and the reason in the
        // accessible name (the title tooltip is mouse-only).
        const tab = screen.getByRole('link', { name: /review — no jobs awaiting review/i });
        expect(tab).toHaveAttribute('aria-disabled', 'true');
        expect(tab).not.toHaveAttribute('href');
    });

    it('renders REVIEW as a link when a job awaits review', () => {
        renderTopBar(buildNavItems({ firstReviewJobId: 6, reviewCount: 1 }));
        expect(screen.getByRole('link', { name: /review/i })).toHaveAttribute(
            'href',
            '/review/6',
        );
    });
});
