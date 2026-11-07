import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DashboardSideRail } from './DashboardSideRail';
import type { DiscTitle, Job, TitleState } from '../../types';

const job = (id: number, volume_label: string): Job =>
    ({
        id,
        volume_label,
        state: 'matching',
        progress_percent: 0,
        current_speed: '',
    }) as unknown as Job;

const title = (id: number, job_id: number, title_index: number, state: TitleState): DiscTitle =>
    ({ id, job_id, title_index, state, matched_episode: null }) as unknown as DiscTitle;

describe('DashboardSideRail activity log', () => {
    it('labels each track event with the disc it belongs to when two jobs run at once', () => {
        const jobs = [job(1, 'OFFICE_S2D3'), job(2, 'ARRESTED_S1D1')];
        const before = {
            1: [title(10, 1, 7, 'ripping')],
            2: [title(20, 2, 7, 'ripping')],
        };
        const after = {
            1: [title(10, 1, 7, 'matching')],
            2: [title(20, 2, 7, 'matching')],
        };
        const { rerender } = render(<DashboardSideRail jobs={jobs} titlesMap={before} />);
        rerender(<DashboardSideRail jobs={jobs} titlesMap={after} />);

        const rows = screen.getAllByTestId('sv-side-rail-log-entry').map((r) => r.textContent ?? '');
        expect(rows).toHaveLength(2);
        expect(rows.some((r) => r.includes('OFFICE_S2D3') && r.includes('T07'))).toBe(true);
        expect(rows.some((r) => r.includes('ARRESTED_S1D1') && r.includes('T07'))).toBe(true);
    });

    it('truncates a long disc label consistently', () => {
        const jobs = [job(1, 'A_VERY_LONG_VOLUME_LABEL_FOR_A_DISC')];
        const { rerender } = render(
            <DashboardSideRail jobs={jobs} titlesMap={{ 1: [title(10, 1, 1, 'ripping')] }} />,
        );
        rerender(<DashboardSideRail jobs={jobs} titlesMap={{ 1: [title(10, 1, 1, 'matching')] }} />);
        const row = screen.getByTestId('sv-side-rail-log-entry');
        expect(row.textContent).toContain('A_VERY_…A_DISC');
        expect(row.textContent).not.toContain('A_VERY_LONG_VOLUME_LABEL');
    });

    it('keeps the disc suffix visible so S1D1 and S1D2 stay distinguishable', () => {
        const jobs = [job(1, 'LONG_SHOW_NAME_HERE_S1D1'), job(2, 'LONG_SHOW_NAME_HERE_S1D2')];
        const before = { 1: [title(10, 1, 1, 'ripping')], 2: [title(20, 2, 1, 'ripping')] };
        const after = { 1: [title(10, 1, 1, 'matching')], 2: [title(20, 2, 1, 'matching')] };
        const { rerender } = render(<DashboardSideRail jobs={jobs} titlesMap={before} />);
        rerender(<DashboardSideRail jobs={jobs} titlesMap={after} />);
        const subjects = screen
            .getAllByTestId('sv-side-rail-log-entry')
            .map((r) => r.textContent ?? '');
        expect(subjects.some((r) => r.includes('S1D1'))).toBe(true);
        expect(subjects.some((r) => r.includes('S1D2'))).toBe(true);
    });

    it('exposes the full disc label as a tooltip on the subject', () => {
        const jobs = [job(1, 'A_VERY_LONG_VOLUME_LABEL_FOR_A_DISC')];
        const { rerender } = render(
            <DashboardSideRail jobs={jobs} titlesMap={{ 1: [title(10, 1, 1, 'ripping')] }} />,
        );
        rerender(<DashboardSideRail jobs={jobs} titlesMap={{ 1: [title(10, 1, 1, 'matching')] }} />);
        expect(screen.getByTitle('A_VERY_LONG_VOLUME_LABEL_FOR_A_DISC · T01')).toBeInTheDocument();
    });

    it('falls back to the job id when the disc has no label', () => {
        const jobs = [job(5, '')];
        const { rerender } = render(
            <DashboardSideRail jobs={jobs} titlesMap={{ 5: [title(10, 5, 2, 'ripping')] }} />,
        );
        rerender(<DashboardSideRail jobs={jobs} titlesMap={{ 5: [title(10, 5, 2, 'matching')] }} />);
        expect(screen.getByTestId('sv-side-rail-log-entry')).toHaveTextContent('JOB 5');
    });
});
