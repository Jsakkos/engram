import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ParkedDiscBanner } from './ParkedDiscBanner';

describe('ParkedDiscBanner', () => {
    it('renders nothing when no disc is parked', () => {
        render(<ParkedDiscBanner discs={[]} onFinishSetup={vi.fn()} />);
        expect(screen.queryByTestId('parked-disc-banner')).not.toBeInTheDocument();
    });

    it('shows the disc label and the finish-setup call to action', () => {
        render(
            <ParkedDiscBanner
                discs={[{ drive_id: 'E:', volume_label: 'INCEPTION_2010' }]}
                onFinishSetup={vi.fn()}
            />,
        );
        expect(screen.getByTestId('parked-disc-banner')).toHaveTextContent('INCEPTION_2010');
        expect(screen.getByTestId('parked-disc-banner')).toHaveTextContent(
            /finish setup to start ripping/i,
        );
    });

    it('wires the Finish setup button to the handler', () => {
        const onFinishSetup = vi.fn();
        render(
            <ParkedDiscBanner
                discs={[{ drive_id: 'E:', volume_label: 'INCEPTION_2010' }]}
                onFinishSetup={onFinishSetup}
            />,
        );
        fireEvent.click(screen.getByRole('button', { name: /finish setup/i }));
        expect(onFinishSetup).toHaveBeenCalledTimes(1);
    });

    it('handles an unreadable (blank) volume label without empty parentheses', () => {
        render(
            <ParkedDiscBanner
                discs={[{ drive_id: 'E:', volume_label: '' }]}
                onFinishSetup={vi.fn()}
            />,
        );
        const banner = screen.getByTestId('parked-disc-banner');
        expect(banner).toHaveTextContent(/disc detected/i);
        expect(banner.textContent).not.toContain('()');
    });

    it('lists every parked disc when more than one drive is waiting', () => {
        render(
            <ParkedDiscBanner
                discs={[
                    { drive_id: 'E:', volume_label: 'INCEPTION_2010' },
                    { drive_id: 'F:', volume_label: 'TENET_2020' },
                ]}
                onFinishSetup={vi.fn()}
            />,
        );
        const banner = screen.getByTestId('parked-disc-banner');
        expect(banner).toHaveTextContent('INCEPTION_2010');
        expect(banner).toHaveTextContent('TENET_2020');
    });
});
