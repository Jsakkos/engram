import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SvStatusBar } from '../SvStatusBar';

const base = { activeCount: 0, completedCount: 0, isConnected: true, version: '1.0.0' };

describe('SvStatusBar (#720)', () => {
  it('renders no filler telemetry when none is supplied', () => {
    render(<SvStatusBar {...base} />);
    const text = screen.getByTestId('sv-statusbar').textContent ?? '';
    for (const fake of ['UNIT 07', 'SESSION 01', 'NOMINAL', 'CPU IDLE', 'GPU IDLE', 'DRIVE READY']) {
      expect(text).not.toContain(fake);
    }
  });

  it('renders the drive label and telemetry it is given', () => {
    render(<SvStatusBar {...base} driveLabel="2 DRIVES" telemetry={['ASR CUDA · IDLE']} />);
    const text = screen.getByTestId('sv-statusbar').textContent ?? '';
    expect(text).toContain('2 DRIVES');
    expect(text).toContain('ASR CUDA · IDLE');
  });
});
