import type { CSSProperties } from 'react';
import { sv } from '../../app/components/synapse';
import { IcoError } from '../../app/components/icons/status';
import type { CoverageEntry, EpisodeStatus } from './coverage';
import type { RosterEpisode } from './types';

const STATUS_COLOR: Record<EpisodeStatus, string> = {
    assigned: sv.cyan,
    duplicate: sv.red,
    missing: sv.yellow,
    off: sv.inkFaint,
};

const truncate: CSSProperties = {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
};

// Faint diagonal hatch overlay marking a slot whose episode has no reference
// subtitle. Kept low-alpha + pointer-events:none so it reads as "striped"
// without obscuring the coverage color or the episode text beneath it.
const NO_REF_HATCH: CSSProperties = {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
    backgroundImage: `repeating-linear-gradient(45deg, ${sv.red}22 0, ${sv.red}22 1px, transparent 1px, transparent 6px)`,
};

/**
 * Disc-level coverage at a glance: the season's episodes as slots, colored by
 * live status — assigned (cyan), doubled (red), gap inside the disc range
 * (dashed yellow), or not on this disc (ghost). The suggested gap glows.
 *
 * A second, orthogonal axis is overlaid: episodes with no reference subtitle
 * (no precomputed vector, no downloaded SRT) get a red warning glyph + hatch,
 * because that absence — not the assignment state — is why the matcher couldn't
 * auto-identify them.
 */
export function SeasonRosterStrip({
    episodes,
    coverage,
    suggestedCode,
    titleIndexById,
}: {
    episodes: RosterEpisode[];
    coverage: Record<string, CoverageEntry>;
    suggestedCode: string | null;
    titleIndexById: Record<number, number>;
}) {
    return (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {episodes.map((ep) => {
                const entry = coverage[ep.episode_code];
                const status: EpisodeStatus = entry?.status ?? 'off';
                const color = STATUS_COLOR[status];
                const isSuggested = ep.episode_code === suggestedCode;
                const noReference = ep.has_reference === false;

                let who: string;
                if (entry && entry.titleIds.length > 0) {
                    who = entry.titleIds.map((id) => `#${titleIndexById[id] ?? id}`).join(' ');
                } else if (status === 'missing') {
                    who = '— gap —';
                } else {
                    who = 'other disc';
                }

                const tooltip = noReference
                    ? `${ep.episode_code} — ${ep.name} — no reference subtitle (matching can't auto-identify this episode)`
                    : `${ep.episode_code} — ${ep.name}`;

                return (
                    <div
                        key={ep.episode_code}
                        title={tooltip}
                        style={{
                            position: 'relative',
                            flex: '1 1 78px',
                            minWidth: 78,
                            padding: '8px 8px 9px',
                            background: sv.bg0,
                            border: `1px solid ${color}${status === 'off' ? '55' : 'aa'}`,
                            borderStyle: status === 'missing' ? 'dashed' : 'solid',
                            opacity: status === 'off' ? 0.5 : 1,
                            boxShadow: isSuggested ? `0 0 0 1px ${sv.yellow}, 0 0 16px ${sv.yellow}40` : undefined,
                        }}
                    >
                        {noReference && <div aria-hidden style={NO_REF_HATCH} />}
                        {isSuggested && (
                            <span
                                style={{
                                    position: 'absolute',
                                    top: -7,
                                    right: -1,
                                    fontFamily: sv.mono,
                                    fontSize: 8,
                                    letterSpacing: '0.1em',
                                    fontWeight: 700,
                                    padding: '1px 5px',
                                    background: sv.yellow,
                                    color: sv.bg0,
                                }}
                            >
                                SUGGEST
                            </span>
                        )}
                        {noReference && (
                            <span
                                style={{
                                    position: 'absolute',
                                    top: -7,
                                    left: -1,
                                    lineHeight: 0,
                                    padding: 1,
                                    background: sv.bg0,
                                }}
                            >
                                <IcoError size={11} color={sv.red} title="No reference subtitle" />
                            </span>
                        )}
                        <div style={{ position: 'relative', zIndex: 1, fontFamily: sv.mono, fontSize: 11, fontWeight: 700, color }}>
                            {`E${String(ep.episode_number).padStart(2, '0')}`}
                        </div>
                        <div style={{ ...truncate, position: 'relative', zIndex: 1, fontFamily: sv.sans, fontSize: 10, color: sv.inkDim, marginTop: 3 }}>
                            {ep.name || '—'}
                        </div>
                        <div style={{ ...truncate, position: 'relative', zIndex: 1, fontFamily: sv.mono, fontSize: 9, color: sv.inkDim, marginTop: 5, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                            {who}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
