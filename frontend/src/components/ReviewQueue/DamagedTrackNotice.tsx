import { useState } from 'react';
import { reripTitle } from '../../api/client';
import { sv } from '../../app/components/synapse';
import { IcoError } from '../../app/components/icons';
import type { RerippableState } from './rerip';

interface Props {
  jobId: number;
  titleId: number;
  state: RerippableState;
}

/**
 * Review affordance for a rip-failed (damaged) track. Tells the user to clean &
 * reinsert (auto re-rip), offers a manual Re-rip button, and is shown alongside
 * the existing skip action so the track is never a dead end (Feature C).
 */
export function DamagedTrackNotice({ jobId, titleId, state }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onRerip = async () => {
    setBusy(true);
    setError(null);
    try {
      await reripTitle(jobId, titleId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Re-rip failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="damaged-track-notice"
      style={{
        padding: '12px 14px',
        marginBottom: 14,
        border: `1px solid ${sv.magenta}66`,
        background: `${sv.magenta}0d`,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontFamily: sv.mono,
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: sv.magenta,
          marginBottom: 6,
        }}
      >
        <IcoError size={13} color={sv.magenta} />
        Damaged track
      </div>
      <p
        style={{
          fontFamily: sv.mono,
          fontSize: 12,
          color: sv.inkDim,
          margin: 0,
          lineHeight: 1.5,
        }}
      >
        {state.message ||
          'This track failed to rip cleanly. Clean the disc and reinsert it to re-rip automatically, or skip it.'}
      </p>
      <div
        style={{
          marginTop: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
        }}
      >
        <button
          type="button"
          data-testid="rerip-button"
          onClick={onRerip}
          disabled={busy}
          style={{
            height: 28,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '0 10px',
            background: sv.bg0,
            border: `1px solid ${sv.cyan}55`,
            color: busy ? sv.inkDim : sv.cyan,
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.16em',
            textTransform: 'uppercase',
            cursor: busy ? 'not-allowed' : 'pointer',
            opacity: busy ? 0.6 : 1,
            boxShadow: `0 0 6px ${sv.cyan}22`,
            transition: 'border-color 120ms, box-shadow 120ms, opacity 120ms',
          }}
          onMouseEnter={(e) => {
            if (busy) return;
            e.currentTarget.style.borderColor = sv.cyan;
            e.currentTarget.style.boxShadow = `0 0 10px ${sv.cyan}55`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = `${sv.cyan}55`;
            e.currentTarget.style.boxShadow = `0 0 6px ${sv.cyan}22`;
          }}
        >
          {busy ? 'Re-ripping…' : 'Re-rip this title'}
        </button>
        {state.attempts > 0 && (
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 10,
              color: sv.inkDim,
              letterSpacing: '0.08em',
            }}
          >
            attempt {state.attempts}
          </span>
        )}
      </div>
      {error && (
        <p
          style={{
            fontFamily: sv.mono,
            fontSize: 11,
            color: sv.red,
            margin: '8px 0 0',
          }}
        >
          {error}
        </p>
      )}
    </div>
  );
}
