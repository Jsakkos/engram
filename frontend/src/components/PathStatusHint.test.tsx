import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PathStatusHint, type PathCheck } from './PathStatusHint';

const OK: PathCheck = {
    ok: true,
    normalized: 'D:\\Media\\TV',
    exists: true,
    writable: true,
    is_network: false,
    unreachable: false,
    free_bytes: 5_660_000_000_000,
    reason: null,
};

function mockCheck(body: Partial<PathCheck>) {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...OK, ...body }) });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('PathStatusHint', () => {
    it('checks nothing while the field is empty', () => {
        const fetchMock = mockCheck({});
        const { container } = render(<PathStatusHint path="  " label="TV library" />);
        expect(container).toBeEmptyDOMElement();
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('confirms a writable path and shows the free space', async () => {
        mockCheck({});
        render(<PathStatusHint path="D:\\Media\\TV" label="TV library" />);
        await waitFor(() => expect(screen.getByText(/Writable/)).toBeInTheDocument());
        expect(screen.getByText(/5\.7 TB free/)).toBeInTheDocument();
    });

    it('calls out a network share', async () => {
        mockCheck({ is_network: true, normalized: '\\\\server\\share\\TV' });
        render(<PathStatusHint path="\\\\server\\share\\TV" label="TV library" />);
        await waitFor(() => expect(screen.getByText(/network share/)).toBeInTheDocument());
    });

    it('shows what a pasted path was stored as', async () => {
        mockCheck({ normalized: '\\\\server\\share\\TV' });
        render(<PathStatusHint path="//server/share/TV" label="TV library" />);
        await waitFor(() => expect(screen.getByText(/saved as/)).toBeInTheDocument());
    });

    it('treats an offline share as a warning, not an error', async () => {
        mockCheck({
            ok: false,
            exists: false,
            writable: false,
            is_network: true,
            unreachable: true,
            reason: "This share isn't reachable right now.",
        });
        const { container } = render(<PathStatusHint path="\\\\server\\share" label="TV library" />);
        await waitFor(() => expect(screen.getByText(/isn't reachable/)).toBeInTheDocument());
        expect(container.querySelector('.path-status-warn')).toBeInTheDocument();
        expect(container.querySelector('.path-status-error')).not.toBeInTheDocument();
    });

    it('reports an unwritable path as an error', async () => {
        mockCheck({
            ok: false,
            writable: false,
            reason: "Engram can't write here: Permission denied",
        });
        const { container } = render(<PathStatusHint path="D:\\Media\\TV" label="TV library" />);
        await waitFor(() => expect(screen.getByText(/can't write here/)).toBeInTheDocument());
        expect(container.querySelector('.path-status-error')).toBeInTheDocument();
    });

    it('stays silent when the check itself fails', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
        const { container } = render(<PathStatusHint path="D:\\Media\\TV" label="TV library" />);
        await waitFor(() => expect(container).toBeEmptyDOMElement());
    });
});
