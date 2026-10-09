import { useState } from 'react';
import { sv } from '../../app/components/synapse';
import { TvdbAttribution } from './TvdbAttribution';

interface Props {
    suggestion: { season: number; tmdb: number; tvdb: number };
    onSwitch: () => Promise<void> | void;
    onDismiss: () => Promise<void> | void;
}

/**
 * Offered when TheTVDB numbers this season differently from TMDB (Justice
 * League S1: 26 vs 24). Nothing changes until the user switches; switching
 * re-downloads references and re-matches without a re-rip.
 */
export function TvdbSuggestionNotice({ suggestion, onSwitch, onDismiss }: Props) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const run = async (fn: () => Promise<void> | void) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Request failed');
        } finally {
            setBusy(false);
        }
    };

    const button = (primary: boolean) => ({
        fontFamily: sv.mono,
        fontSize: 11,
        padding: '5px 10px',
        cursor: busy ? 'wait' : 'pointer',
        background: primary ? sv.cyan : 'transparent',
        color: primary ? sv.bg0 : sv.inkDim,
        border: `1px solid ${primary ? sv.cyan : sv.lineMid}`,
    });

    return (
        <div
            data-testid="tvdb-suggestion"
            style={{ padding: '12px 14px', marginBottom: 14, border: `1px solid ${sv.cyan}66`, background: sv.bg0 }}
        >
            <div style={{ fontFamily: sv.sans, fontSize: 12, color: sv.ink, marginBottom: 10 }}>
                TheTVDB numbers Season {suggestion.season} differently ({suggestion.tvdb} episodes vs
                TMDB's {suggestion.tmdb}). If this disc follows TheTVDB, switching re-matches it with
                TheTVDB numbering.
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button type="button" disabled={busy} style={button(true)} onClick={() => run(onSwitch)}>
                    Switch to TheTVDB numbering
                </button>
                <button type="button" disabled={busy} style={button(false)} onClick={() => run(onDismiss)}>
                    Dismiss
                </button>
                <TvdbAttribution />
            </div>
            {error && (
                <div role="alert" style={{ marginTop: 8, fontFamily: sv.sans, fontSize: 11, color: sv.amber }}>
                    {error}
                </div>
            )}
        </div>
    );
}
