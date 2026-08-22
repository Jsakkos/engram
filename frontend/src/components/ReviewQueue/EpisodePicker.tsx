import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { SvActionButton, sv } from '../../app/components/synapse';
import { EPISODE_CONFIG } from '../../config/constants';
import { buildRangeCode, displayEpisodeCode, inferSpan, MAX_SPAN, parseEpisodeCode } from './coverage';
import type { RosterEpisode } from './types';
import { generateEpisodeOptions } from './utils';

const monoFaint: CSSProperties = { fontFamily: sv.mono, fontSize: 11, color: sv.inkDim };

/** Episodes whose code or name matches the typed query, in roster order. */
function filterEpisodes(episodes: RosterEpisode[], query: string): RosterEpisode[] {
    const q = query.trim().toLowerCase();
    if (!q) return episodes;
    // A bare number ("7") should find E07 rather than every episode whose name
    // happens to contain a 7 — the common way to reach for an episode.
    const asNumber = /^\d+$/.test(q) ? parseInt(q, 10) : null;
    return episodes.filter(
        (ep) =>
            (asNumber != null && ep.episode_number === asNumber) ||
            ep.episode_code.toLowerCase().includes(q) ||
            ep.name.toLowerCase().includes(q),
    );
}

/**
 * Searchable episode picker with combined-track support.
 *
 * A season list is not always short — a segment-numbered show can list 100+
 * episodes for one season — so the picker filters as you type over both the code
 * and the episode name (the names come from TMDB via the season roster, so the
 * data is already cached). `Spans` turns the pick into a range for one track that
 * holds several episodes; the assignment is a single canonical code
 * (`S01E01-E02-E03`) and organizes to one correspondingly-named file.
 */
export function EpisodePicker({
    titleIndex,
    season,
    episodes,
    selection,
    trackSeconds,
    spansEnabled = false,
    onAssign,
}: {
    titleIndex: number;
    season: number;
    episodes: RosterEpisode[];
    /** Current assignment for this title (any code, 'extra'/'skip', or none). */
    selection: string | undefined;
    /** Track length, used to suggest how many episodes it holds. */
    trackSeconds?: number | null;
    /**
     * Whether to offer the combined-track control at all. Decided per DISC, not
     * per track: on a segment-format disc the tracks the heuristic can't read are
     * precisely the ones needing a hand-set span, so hiding the control there
     * would be backwards. On an ordinary disc it is noise, and stays away.
     */
    spansEnabled?: boolean;
    onAssign: (code: string) => void;
}) {
    const parsed = selection ? parseEpisodeCode(selection) : null;
    const selectedFirst = parsed?.episodes[0] ?? null;
    const selectedSpan = parsed?.episodes.length ?? 0;

    const suggestedSpan = useMemo(() => inferSpan(trackSeconds, episodes), [trackSeconds, episodes]);
    // The live span: what's assigned, else the runtime-derived suggestion.
    const [span, setSpan] = useState(selectedSpan || suggestedSpan);
    const [query, setQuery] = useState('');
    const [open, setOpen] = useState(false);
    const boxRef = useRef<HTMLDivElement>(null);

    // Follow the assignment when it changes elsewhere (accepting a suggestion,
    // a re-match landing) so the stepper never contradicts the current pick.
    useEffect(() => {
        if (selectedSpan) setSpan(selectedSpan);
        else setSpan(suggestedSpan);
    }, [selectedSpan, suggestedSpan, selection]);

    // Close on outside click — a dropdown left open over the next title's row
    // reads as part of that row.
    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => {
            if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open]);

    // Roster episodes when TMDB gave us some; otherwise a plain numbered list so
    // the picker still works on a disc whose show/season isn't identified.
    const options: RosterEpisode[] = useMemo(() => {
        if (episodes.length > 0) return episodes;
        return generateEpisodeOptions(season, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON).map(
            (code, i) => ({ episode_code: code, episode_number: i + 1, name: '' }),
        );
    }, [episodes, season]);

    const filtered = useMemo(() => filterEpisodes(options, query), [options, query]);

    const assign = (first: number, nextSpan = span) => {
        const maxFirst = options.length ? options[options.length - 1].episode_number : first;
        const clamped = Math.max(1, Math.min(nextSpan, maxFirst - first + 1));
        onAssign(buildRangeCode(season, first, clamped));
        setQuery('');
        setOpen(false);
    };

    const changeSpan = (next: number) => {
        const clamped = Math.max(1, Math.min(MAX_SPAN, next));
        setSpan(clamped);
        // Re-emit immediately when something is already picked, so the range in
        // the header and the roster strip track the stepper.
        if (selectedFirst != null) assign(selectedFirst, clamped);
    };

    const label = selectedFirst != null ? displayEpisodeCode(selection as string) : 'Pick episode…';

    return (
        <div ref={boxRef} style={{ position: 'relative', flex: 1, minWidth: 0 }}>
            <input
                type="text"
                role="combobox"
                aria-expanded={open}
                aria-controls={`episode-list-${titleIndex}`}
                aria-label={`Manual episode for title ${titleIndex}`}
                placeholder={label}
                value={query}
                onChange={(e) => {
                    setQuery(e.target.value);
                    setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' && filtered.length > 0) {
                        e.preventDefault();
                        assign(filtered[0].episode_number);
                    } else if (e.key === 'Escape') {
                        setOpen(false);
                    }
                }}
                style={{
                    width: '100%',
                    background: sv.bg0,
                    border: `1px solid ${open ? sv.cyan : sv.lineMid}`,
                    color: selectedFirst != null && !query ? sv.cyan : sv.ink,
                    fontFamily: sv.mono,
                    fontSize: 12,
                    padding: '7px 9px',
                    outline: 'none',
                }}
            />

            {open && (
                <div
                    id={`episode-list-${titleIndex}`}
                    role="listbox"
                    style={{
                        position: 'absolute',
                        zIndex: 20,
                        top: '100%',
                        left: 0,
                        right: 0,
                        maxHeight: 240,
                        overflowY: 'auto',
                        background: sv.bg1,
                        border: `1px solid ${sv.lineMid}`,
                        borderTop: 'none',
                    }}
                >
                    {filtered.length === 0 && (
                        <div style={{ ...monoFaint, padding: '8px 9px' }}>No episode matches “{query}”</div>
                    )}
                    {filtered.map((ep) => {
                        const isSelected = ep.episode_number === selectedFirst;
                        return (
                            <div
                                key={ep.episode_code}
                                role="option"
                                aria-selected={isSelected}
                                tabIndex={-1}
                                onMouseDown={(e) => e.preventDefault()}
                                onClick={() => assign(ep.episode_number)}
                                style={{
                                    display: 'flex',
                                    gap: 8,
                                    padding: '6px 9px',
                                    cursor: 'pointer',
                                    background: isSelected ? `${sv.cyan}18` : 'transparent',
                                    borderBottom: `1px solid ${sv.line}`,
                                }}
                            >
                                <span style={{ fontFamily: sv.mono, fontSize: 11.5, color: sv.cyan, flexShrink: 0 }}>
                                    {`E${String(ep.episode_number).padStart(2, '0')}`}
                                </span>
                                <span
                                    style={{
                                        fontSize: 11.5,
                                        color: sv.inkDim,
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                    }}
                                >
                                    {ep.name}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Combined-track stepper. Shown when the disc calls for it — some
                track on it reads as combined, or the user has asked for the control
                on every disc — and also whenever THIS track is already assigned a
                range, so an existing span can always be seen and changed. */}
            {(spansEnabled || selectedSpan > 1) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                <span style={{ ...monoFaint, fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                    Spans
                </span>
                <SvActionButton
                    tone="neutral"
                    size="sm"
                    onClick={() => changeSpan(span - 1)}
                    disabled={span <= 1}
                    title="One fewer episode in this track"
                    ariaLabel="Fewer episodes"
                >
                    −
                </SvActionButton>
                <span style={{ fontFamily: sv.mono, fontSize: 12, color: sv.ink, minWidth: 68, textAlign: 'center' }}>
                    {span === 1 ? '1 episode' : `${span} episodes`}
                </span>
                <SvActionButton
                    tone="neutral"
                    size="sm"
                    onClick={() => changeSpan(span + 1)}
                    disabled={span >= MAX_SPAN}
                    title="One more episode in this track"
                    ariaLabel="More episodes"
                >
                    +
                </SvActionButton>
            {suggestedSpan > 1 && selectedSpan !== suggestedSpan && (
                <span style={{ ...monoFaint, fontSize: 10.5 }}>
                    runtime suggests {suggestedSpan}
                </span>
            )}
                </div>
            )}
        </div>
    );
}
