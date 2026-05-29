/**
 * BootstrapLibraryFlow — Seed the fingerprint network from an existing TV library.
 *
 * 4-step wizard:
 *   1. Directory  — text input for library path + Scan button
 *   2. Scanning   — loading state while the scan POST is in flight
 *   3. Review     — per-show resolution: accept/skip resolved shows, enter TMDB IDs for unresolved
 *   4. Fingerprint — POST to /accept in batches, show progress + final result
 *
 * Data model: Bootstrap TRUSTS the filename's season/episode numbers. The ONLY
 * review surface is per-SHOW TMDB resolution. No per-episode confidence UI.
 */

import { useState, useCallback, useMemo, useRef, useEffect, type CSSProperties } from 'react';
import { sv } from '../app/components/synapse/tokens';
import { SvPanel } from '../app/components/synapse/SvPanel';
import { SvCorners } from '../app/components/synapse/SvCorners';
import { SvActionButton } from '../app/components/synapse/SvActionButton';
import { SvNotice } from '../app/components/synapse/SvNotice';

// ─── API types ──────────────────────────────────────────────────────────────

interface ScanEpisode {
    file: string;
    season: number;
    episode: number;
}

interface ScanShow {
    folder_name: string;
    tmdb_id: number | null;
    tmdb_name: string | null;
    tmdb_year: number | null;
    resolved: boolean;
    episode_count: number;
    episodes: ScanEpisode[];
}

interface ScanSummary {
    total_files: number;
    parsed: number;
    shows: number;
    unparseable: number;
}

interface UnparseableFile {
    file: string;
}

interface ScanResult {
    shows: ScanShow[];
    unparseable: UnparseableFile[];
    summary: ScanSummary;
}

interface AcceptItem {
    file: string;
    tmdb_id: number;
    season: number;
    episode: number;
}

interface AcceptResult {
    queued: number;
    failed: number;
}

// ─── Internal state per show ─────────────────────────────────────────────────

interface ShowState {
    /** Included in the accept batch when true */
    accepted: boolean;
    /** User-entered TMDB ID for unresolved shows (overrides scan result) */
    manualTmdbId: string;
}

// ─── Wizard steps ────────────────────────────────────────────────────────────

type WizardStep = 'directory' | 'scanning' | 'review' | 'fingerprint';

const STEP_ORDER: WizardStep[] = ['directory', 'scanning', 'review', 'fingerprint'];
const STEP_LABELS: Record<WizardStep, string> = {
    directory: 'Directory',
    scanning: 'Scanning',
    review: 'Review',
    fingerprint: 'Fingerprint',
};

// ─── Shared style helpers ─────────────────────────────────────────────────────

const mono: CSSProperties = {
    fontFamily: sv.mono,
};

const labelStyle: CSSProperties = {
    ...mono,
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.18em',
    textTransform: 'uppercase',
    color: sv.inkDim,
    display: 'block',
    marginBottom: 6,
};

const inputStyle: CSSProperties = {
    ...mono,
    width: '100%',
    padding: '8px 12px',
    background: 'rgba(94, 234, 212, 0.04)',
    border: `1px solid ${sv.lineMid}`,
    color: sv.ink,
    fontSize: 13,
    outline: 'none',
};

const hintStyle: CSSProperties = {
    ...mono,
    fontSize: 10,
    color: sv.inkFaint,
    letterSpacing: '0.10em',
    marginTop: 6,
    lineHeight: 1.5,
};

// ─── Sub-components ───────────────────────────────────────────────────────────

/** Horizontal step indicator */
function StepBar({ current }: { current: WizardStep }) {
    const currentIdx = STEP_ORDER.indexOf(current);
    return (
        <div
            style={{
                display: 'flex',
                alignItems: 'center',
                padding: '14px 24px',
                borderBottom: `1px solid ${sv.line}`,
                background: 'rgba(5, 7, 12, 0.3)',
                gap: 0,
            }}
        >
            {STEP_ORDER.map((step, idx) => {
                const isDone = idx < currentIdx;
                const isActive = idx === currentIdx;
                return (
                    <div key={step} style={{ display: 'flex', alignItems: 'center', flex: idx < STEP_ORDER.length - 1 ? 1 : undefined }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                            <div
                                style={{
                                    width: 26,
                                    height: 26,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    border: `1px solid ${isDone ? sv.cyanDim : isActive ? sv.cyan : sv.line}`,
                                    background: isDone
                                        ? 'rgba(94, 234, 212, 0.07)'
                                        : isActive
                                            ? sv.cyan
                                            : 'transparent',
                                    color: isDone
                                        ? sv.cyanDim
                                        : isActive
                                            ? sv.bg0
                                            : sv.inkFaint,
                                    fontFamily: sv.mono,
                                    fontSize: 10,
                                    fontWeight: 700,
                                    letterSpacing: '0.10em',
                                    boxShadow: isActive ? `0 0 10px ${sv.cyan}66` : undefined,
                                    flexShrink: 0,
                                }}
                            >
                                {isDone ? '✓' : idx + 1}
                            </div>
                            <span
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 9,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: isDone ? sv.cyanDim : isActive ? sv.ink : sv.inkFaint,
                                    whiteSpace: 'nowrap',
                                }}
                            >
                                {STEP_LABELS[step]}
                            </span>
                        </div>
                        {idx < STEP_ORDER.length - 1 && (
                            <div
                                style={{
                                    flex: 1,
                                    height: 1,
                                    background: isDone ? sv.cyanDim : sv.line,
                                    margin: '0 12px',
                                }}
                            />
                        )}
                    </div>
                );
            })}
        </div>
    );
}

/** Single resolved-show row */
function ResolvedShowRow({
    show,
    state,
    onToggleAccepted,
    onChangeManualId,
}: {
    show: ScanShow;
    state: ShowState;
    onToggleAccepted: () => void;
    onChangeManualId: (v: string) => void;
}) {
    const [showChange, setShowChange] = useState(false);

    return (
        <div
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 14px',
                borderBottom: `1px solid ${sv.line}`,
                background: state.accepted ? 'rgba(134, 239, 172, 0.03)' : undefined,
                opacity: state.accepted ? 1 : 0.5,
            }}
        >
            {/* Checkbox */}
            <input
                type="checkbox"
                checked={state.accepted}
                onChange={onToggleAccepted}
                style={{ width: 14, height: 14, cursor: 'pointer', accentColor: sv.cyan, flexShrink: 0 }}
                aria-label={`Accept ${show.folder_name}`}
            />

            {/* Show info */}
            <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{ ...mono, fontSize: 12, color: sv.ink, fontWeight: 500 }}>
                    {show.folder_name}
                </span>
                <span style={{ ...mono, fontSize: 11, color: sv.inkDim, marginLeft: 8 }}>
                    → {show.tmdb_name} ({show.tmdb_year})
                </span>
            </div>

            {/* Episode count */}
            <span style={{ ...mono, fontSize: 10, color: sv.inkFaint, letterSpacing: '0.10em', flexShrink: 0 }}>
                {show.episode_count} eps
            </span>

            {/* Change affordance */}
            <button
                type="button"
                onClick={() => setShowChange(!showChange)}
                style={{
                    ...mono,
                    fontSize: 9,
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                    color: sv.inkFaint,
                    background: 'transparent',
                    border: `1px solid ${sv.line}`,
                    padding: '3px 8px',
                    cursor: 'pointer',
                    flexShrink: 0,
                    transition: 'color 120ms, border-color 120ms',
                }}
                onMouseEnter={(e) => {
                    e.currentTarget.style.color = sv.inkDim;
                    e.currentTarget.style.borderColor = sv.lineMid;
                }}
                onMouseLeave={(e) => {
                    e.currentTarget.style.color = sv.inkFaint;
                    e.currentTarget.style.borderColor = sv.line;
                }}
            >
                Change
            </button>

            {/* Inline TMDB ID override when Change is open */}
            {showChange && (
                <input
                    type="text"
                    value={state.manualTmdbId}
                    onChange={(e) => onChangeManualId(e.target.value)}
                    placeholder="TMDB ID override"
                    style={{
                        ...inputStyle,
                        width: 140,
                        fontSize: 11,
                        padding: '4px 8px',
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; }}
                />
            )}
        </div>
    );
}

/** Single unresolved-show row — requires a TMDB ID or explicit skip */
function UnresolvedShowRow({
    show,
    state,
    onChangeManualId,
    onToggleAccepted,
}: {
    show: ScanShow;
    state: ShowState;
    onChangeManualId: (v: string) => void;
    onToggleAccepted: () => void;
}) {
    return (
        <div
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 14px',
                borderBottom: `1px solid ${sv.line}`,
                background: state.accepted ? 'rgba(255, 61, 127, 0.04)' : undefined,
                outline: `1px solid rgba(255, 61, 127, 0.15)`,
            }}
        >
            {/* Magenta dot */}
            <div
                style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: sv.magenta,
                    boxShadow: `0 0 5px ${sv.magenta}`,
                    flexShrink: 0,
                }}
            />

            {/* Show info */}
            <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{ ...mono, fontSize: 12, color: sv.magentaHi, fontWeight: 600 }}>
                    {show.folder_name}
                </span>
                <span style={{ ...mono, fontSize: 10, color: sv.inkFaint, marginLeft: 8, letterSpacing: '0.10em' }}>
                    unresolved · {show.episode_count} eps
                </span>
            </div>

            {/* TMDB ID input */}
            <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ ...mono, fontSize: 10, color: sv.inkDim, letterSpacing: '0.12em' }}>TMDB ID</span>
                <input
                    type="text"
                    value={state.manualTmdbId}
                    onChange={(e) => onChangeManualId(e.target.value)}
                    placeholder="e.g. 1396"
                    style={{
                        ...inputStyle,
                        width: 110,
                        fontSize: 12,
                        padding: '5px 8px',
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; }}
                    aria-label={`TMDB ID for ${show.folder_name}`}
                />
            </div>

            {/* Skip toggle */}
            <button
                type="button"
                onClick={onToggleAccepted}
                style={{
                    ...mono,
                    fontSize: 9,
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                    color: state.accepted ? sv.red : sv.inkFaint,
                    background: 'transparent',
                    border: `1px solid ${state.accepted ? `${sv.red}55` : sv.line}`,
                    padding: '3px 8px',
                    cursor: 'pointer',
                    flexShrink: 0,
                    transition: 'color 120ms, border-color 120ms',
                }}
            >
                {state.accepted ? 'Unskip' : 'Skip'}
            </button>
        </div>
    );
}

// ─── Main component ───────────────────────────────────────────────────────────

interface BootstrapLibraryFlowProps {
    onClose: () => void;
}

export function BootstrapLibraryFlow({ onClose }: BootstrapLibraryFlowProps) {
    // Wizard state
    const [step, setStep] = useState<WizardStep>('directory');
    const [path, setPath] = useState('');
    const [scanResult, setScanResult] = useState<ScanResult | null>(null);
    const [scanError, setScanError] = useState<string | null>(null);
    const [showStates, setShowStates] = useState<Record<string, ShowState>>({});
    const [showUnparseable, setShowUnparseable] = useState(false);

    // Fingerprint step
    const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);
    const [finalResult, setFinalResult] = useState<AcceptResult | null>(null);
    const [fingerprintError, setFingerprintError] = useState<string | null>(null);

    // Abort in-flight scan/accept fetches when the user navigates away (Back /
    // Cancel) or the modal unmounts, so a late response can't write state onto
    // an abandoned step or fire on an unmounted component.
    const abortRef = useRef<AbortController | null>(null);
    useEffect(() => () => abortRef.current?.abort(), []);

    // ─── Handlers ────────────────────────────────────────────────────────────

    const handleScan = useCallback(async () => {
        if (!path.trim()) return;
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        setScanError(null);
        setStep('scanning');

        try {
            const res = await fetch('/api/fingerprint/bootstrap/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path.trim() }),
                signal: controller.signal,
            });
            if (!res.ok) {
                const body = await res.text().catch(() => '');
                throw new Error(`Scan failed (${res.status}): ${body || res.statusText}`);
            }
            const data: ScanResult = await res.json();
            setScanResult(data);

            // Initialize per-show state
            const initial: Record<string, ShowState> = {};
            for (const show of data.shows) {
                // Resolved shows: checked by default.
                // Unresolved shows: NOT accepted until user provides a TMDB ID.
                initial[show.folder_name] = {
                    accepted: show.resolved,
                    manualTmdbId: show.tmdb_id != null ? String(show.tmdb_id) : '',
                };
            }
            setShowStates(initial);
            setStep('review');
        } catch (err) {
            // A user-triggered abort (Back/unmount) is not an error to surface.
            if (err instanceof DOMException && err.name === 'AbortError') return;
            setScanError(err instanceof Error ? err.message : String(err));
            setStep('directory');
        }
    }, [path]);

    const updateShowState = useCallback((folderName: string, patch: Partial<ShowState>) => {
        setShowStates((prev) => ({
            ...prev,
            [folderName]: { ...prev[folderName], ...patch },
        }));
    }, []);

    /**
     * Full list of AcceptItems derived from the current showStates.
     * Memoized so it only recomputes when the scan result or per-show
     * acceptance state changes — not on every render (e.g. a keystroke in
     * any unrelated input would otherwise re-walk every show/episode).
     */
    const acceptItems = useMemo<AcceptItem[]>(() => {
        if (!scanResult) return [];
        const items: AcceptItem[] = [];
        for (const show of scanResult.shows) {
            const state = showStates[show.folder_name];
            if (!state?.accepted) continue;

            // Effective TMDB ID: manual override if entered, otherwise from scan
            const rawId = state.manualTmdbId.trim() || (show.tmdb_id != null ? String(show.tmdb_id) : '');
            const tmdbId = parseInt(rawId, 10);
            if (isNaN(tmdbId) || tmdbId <= 0) continue; // no valid TMDB ID → skip

            for (const ep of show.episodes) {
                items.push({ file: ep.file, tmdb_id: tmdbId, season: ep.season, episode: ep.episode });
            }
        }
        return items;
    }, [scanResult, showStates]);

    const handleFingerprint = useCallback(async () => {
        const items = acceptItems;
        if (items.length === 0) {
            setStep('fingerprint');
            setFinalResult({ queued: 0, failed: 0 });
            return;
        }

        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;

        setStep('fingerprint');
        setFingerprintError(null);

        const BATCH_SIZE = 50;
        const batches: AcceptItem[][] = [];
        for (let i = 0; i < items.length; i += BATCH_SIZE) {
            batches.push(items.slice(i, i + BATCH_SIZE));
        }

        setBatchProgress({ done: 0, total: batches.length });

        let totalQueued = 0;
        let totalFailed = 0;

        for (let i = 0; i < batches.length; i++) {
            try {
                const res = await fetch('/api/fingerprint/bootstrap/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ items: batches[i] }),
                    signal: controller.signal,
                });
                if (!res.ok) {
                    const body = await res.text().catch(() => '');
                    throw new Error(`Batch ${i + 1} failed (${res.status}): ${body || res.statusText}`);
                }
                const result: AcceptResult = await res.json();
                totalQueued += result.queued;
                totalFailed += result.failed;
                setBatchProgress({ done: i + 1, total: batches.length });
            } catch (err) {
                // Aborted (modal closed / new run started) — stop without writing state.
                if (err instanceof DOMException && err.name === 'AbortError') return;
                setFingerprintError(err instanceof Error ? err.message : String(err));
                setBatchProgress({ done: i + 1, total: batches.length });
                // Continue remaining batches even on partial failure
            }
        }

        if (controller.signal.aborted) return;
        setFinalResult({ queued: totalQueued, failed: totalFailed });
    }, [acceptItems]);

    // ─── Computed values for review step ─────────────────────────────────────

    const resolvedShows = scanResult?.shows.filter((s) => s.resolved) ?? [];
    const unresolvedShows = scanResult?.shows.filter((s) => !s.resolved) ?? [];
    const acceptedCount = Object.values(showStates).filter((s) => s.accepted).length;

    // Count episodes that will be submitted (only shows with valid TMDB IDs)
    const acceptEpisodeCount = acceptItems.length;

    // ─── Step renderers ───────────────────────────────────────────────────────

    const renderDirectory = () => (
        <div style={{ padding: 28 }}>
            <h3
                style={{
                    fontFamily: sv.sans,
                    fontSize: 15,
                    fontWeight: 700,
                    letterSpacing: '0.16em',
                    textTransform: 'uppercase',
                    color: sv.cyanHi,
                    marginBottom: 8,
                    textShadow: `0 0 14px ${sv.cyan}66`,
                }}
            >
                Library Directory
            </h3>
            <p style={{ ...hintStyle, fontSize: 12, color: sv.inkDim, marginBottom: 20, letterSpacing: '0.06em' }}>
                Point Engram at your existing TV library. We read your filenames,
                identify shows and episodes, and seed the fingerprint network.
            </p>

            <div style={{ marginBottom: 20 }}>
                <label htmlFor="bootstrap-path" style={labelStyle}>
                    TV Library Path
                </label>
                <div style={{ display: 'flex', gap: 10 }}>
                    <input
                        id="bootstrap-path"
                        type="text"
                        value={path}
                        onChange={(e) => setPath(e.target.value)}
                        placeholder="e.g. D:\TV Shows or /mnt/media/tv"
                        style={{ ...inputStyle, flex: 1 }}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleScan(); }}
                        onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; e.currentTarget.style.boxShadow = `0 0 8px ${sv.cyan}33`; }}
                        onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; e.currentTarget.style.boxShadow = 'none'; }}
                        autoFocus
                    />
                    <SvActionButton tone="cyan" size="md" onClick={handleScan} disabled={!path.trim()}>
                        Scan
                    </SvActionButton>
                </div>
                <p style={hintStyle}>TV shows only. We parse &ldquo;Show - SxxEyy&rdquo; filename patterns.</p>
            </div>

            {scanError && (
                <SvNotice tone="error" style={{ marginTop: 16 }}>
                    {scanError}
                </SvNotice>
            )}

            <div
                style={{
                    marginTop: 24,
                    padding: '12px 16px',
                    background: 'rgba(94, 234, 212, 0.03)',
                    border: `1px solid ${sv.line}`,
                }}
            >
                <p style={{ ...hintStyle, marginTop: 0 }}>
                    <span style={{ color: sv.cyan }}>Privacy:</span> No filenames or paths are uploaded &mdash;
                    only audio fingerprints extracted from the actual MKV files
                    (queued locally, submitted when you rip future discs).
                </p>
            </div>
        </div>
    );

    const renderScanning = () => (
        <div
            style={{
                padding: 48,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 20,
                minHeight: 260,
                justifyContent: 'center',
            }}
        >
            <div
                style={{
                    width: 40,
                    height: 40,
                    border: `2px solid ${sv.line}`,
                    borderTopColor: sv.cyan,
                    borderRadius: '50%',
                    animation: 'svSpin 0.8s linear infinite',
                }}
            />
            <p style={{ ...mono, fontSize: 12, color: sv.inkDim, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Scanning directory&hellip;
            </p>
            <p style={{ ...mono, fontSize: 11, color: sv.inkFaint, letterSpacing: '0.10em' }}>
                {path}
            </p>
        </div>
    );

    const renderReview = () => {
        if (!scanResult) return null;
        const { summary } = scanResult;

        return (
            <div style={{ padding: 24 }}>
                {/* Summary stats bar */}
                <div
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 16,
                        flexWrap: 'wrap',
                        padding: '10px 14px',
                        background: 'rgba(94, 234, 212, 0.03)',
                        border: `1px solid ${sv.line}`,
                        marginBottom: 20,
                    }}
                >
                    {[
                        { label: 'Files found', value: summary.total_files, color: sv.ink },
                        { label: 'Parsed', value: summary.parsed, color: sv.cyan },
                        { label: 'Shows', value: summary.shows, color: sv.ink },
                        { label: 'Unresolved', value: unresolvedShows.length, color: unresolvedShows.length > 0 ? sv.magenta : sv.inkFaint },
                        { label: 'Unparseable', value: summary.unparseable, color: summary.unparseable > 0 ? sv.yellow : sv.inkFaint },
                    ].map(({ label, value, color }, i, arr) => (
                        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ ...mono, fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: sv.inkDim }}>
                                    {label}
                                </span>
                                <span style={{ ...mono, fontSize: 11, fontWeight: 700, color }}>{value}</span>
                            </div>
                            {i < arr.length - 1 && (
                                <div style={{ width: 1, height: 14, background: sv.line }} />
                            )}
                        </div>
                    ))}
                </div>

                {/* Unresolved shows — need TMDB IDs */}
                {unresolvedShows.length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 8,
                                padding: '6px 14px',
                                background: 'rgba(255, 61, 127, 0.06)',
                                border: `1px solid rgba(255, 61, 127, 0.3)`,
                                borderBottom: 0,
                            }}
                        >
                            <div style={{ width: 5, height: 5, borderRadius: '50%', background: sv.magenta, boxShadow: `0 0 5px ${sv.magenta}` }} />
                            <span style={{ ...mono, fontSize: 9, letterSpacing: '0.18em', textTransform: 'uppercase', color: sv.magenta }}>
                                {unresolvedShows.length} show{unresolvedShows.length !== 1 ? 's' : ''} need TMDB IDs
                            </span>
                        </div>
                        <div style={{ border: `1px solid rgba(255, 61, 127, 0.3)` }}>
                            {unresolvedShows.map((show) => (
                                <UnresolvedShowRow
                                    key={show.folder_name}
                                    show={show}
                                    state={showStates[show.folder_name] ?? { accepted: false, manualTmdbId: '' }}
                                    onChangeManualId={(v) => {
                                        const id = v.trim();
                                        const num = parseInt(id, 10);
                                        const validId = !isNaN(num) && num > 0;
                                        updateShowState(show.folder_name, {
                                            manualTmdbId: v,
                                            // Auto-accept when a valid ID is entered
                                            accepted: validId,
                                        });
                                    }}
                                    onToggleAccepted={() => updateShowState(show.folder_name, { accepted: !showStates[show.folder_name]?.accepted })}
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Resolved shows */}
                {resolvedShows.length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                padding: '6px 14px',
                                background: 'rgba(94, 234, 212, 0.04)',
                                border: `1px solid ${sv.line}`,
                                borderBottom: 0,
                            }}
                        >
                            <span style={{ ...mono, fontSize: 9, letterSpacing: '0.18em', textTransform: 'uppercase', color: sv.cyanDim }}>
                                {resolvedShows.length} resolved show{resolvedShows.length !== 1 ? 's' : ''} &mdash; accepted by default
                            </span>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button
                                    type="button"
                                    onClick={() => resolvedShows.forEach((s) => updateShowState(s.folder_name, { accepted: true }))}
                                    style={{
                                        ...mono,
                                        fontSize: 9,
                                        letterSpacing: '0.14em',
                                        textTransform: 'uppercase',
                                        color: sv.green,
                                        background: 'transparent',
                                        border: `1px solid rgba(134, 239, 172, 0.4)`,
                                        padding: '3px 8px',
                                        cursor: 'pointer',
                                    }}
                                >
                                    Accept All
                                </button>
                                <button
                                    type="button"
                                    onClick={() => resolvedShows.forEach((s) => updateShowState(s.folder_name, { accepted: false }))}
                                    style={{
                                        ...mono,
                                        fontSize: 9,
                                        letterSpacing: '0.14em',
                                        textTransform: 'uppercase',
                                        color: sv.inkFaint,
                                        background: 'transparent',
                                        border: `1px solid ${sv.line}`,
                                        padding: '3px 8px',
                                        cursor: 'pointer',
                                    }}
                                >
                                    Deselect All
                                </button>
                            </div>
                        </div>
                        <div style={{ border: `1px solid ${sv.line}` }}>
                            {resolvedShows.map((show) => (
                                <ResolvedShowRow
                                    key={show.folder_name}
                                    show={show}
                                    state={showStates[show.folder_name] ?? { accepted: true, manualTmdbId: String(show.tmdb_id ?? '') }}
                                    onToggleAccepted={() => updateShowState(show.folder_name, { accepted: !showStates[show.folder_name]?.accepted })}
                                    onChangeManualId={(v) => updateShowState(show.folder_name, { manualTmdbId: v })}
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Unparseable files section */}
                {scanResult.unparseable.length > 0 && (
                    <div>
                        <button
                            type="button"
                            onClick={() => setShowUnparseable(!showUnparseable)}
                            style={{
                                ...mono,
                                width: '100%',
                                textAlign: 'left',
                                fontSize: 10,
                                letterSpacing: '0.12em',
                                color: sv.inkFaint,
                                background: 'transparent',
                                border: `1px solid ${sv.line}`,
                                padding: '8px 14px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                            }}
                        >
                            <span>
                                {scanResult.unparseable.length} file{scanResult.unparseable.length !== 1 ? 's' : ''} couldn&apos;t be matched to Show &mdash; SxxEyy and will be skipped
                            </span>
                            <span style={{ letterSpacing: '0.06em' }}>{showUnparseable ? '▲' : '▼'}</span>
                        </button>
                        {showUnparseable && (
                            <div
                                style={{
                                    border: `1px solid ${sv.line}`,
                                    borderTop: 0,
                                    maxHeight: 160,
                                    overflowY: 'auto',
                                    scrollbarWidth: 'thin',
                                }}
                            >
                                {scanResult.unparseable.map((u, i) => (
                                    <div
                                        key={i}
                                        style={{
                                            padding: '6px 14px',
                                            borderBottom: i < scanResult.unparseable.length - 1 ? `1px solid ${sv.line}` : undefined,
                                        }}
                                    >
                                        <span style={{ ...mono, fontSize: 11, color: sv.inkFaint }}>{u.file}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {/* Nothing selected warning */}
                {acceptedCount === 0 && (
                    <SvNotice tone="warn" style={{ marginTop: 16 }}>
                        No shows selected. At least one show must be accepted to proceed.
                    </SvNotice>
                )}
            </div>
        );
    };

    const renderFingerprint = () => {
        const totalBatches = batchProgress?.total ?? 0;
        const doneBatches = batchProgress?.done ?? 0;
        const progressPct = totalBatches > 0 ? Math.round((doneBatches / totalBatches) * 100) : 0;
        const isComplete = finalResult != null;

        return (
            <div style={{ padding: 28 }}>
                <h3
                    style={{
                        fontFamily: sv.sans,
                        fontSize: 15,
                        fontWeight: 700,
                        letterSpacing: '0.16em',
                        textTransform: 'uppercase',
                        color: sv.cyanHi,
                        marginBottom: 16,
                        textShadow: `0 0 14px ${sv.cyan}66`,
                    }}
                >
                    Queuing Fingerprints
                </h3>

                {/* Progress bar */}
                {!isComplete && batchProgress && (
                    <div style={{ marginBottom: 24 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                            <span style={{ ...mono, fontSize: 10, color: sv.inkDim, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
                                Batch {doneBatches} / {totalBatches}
                            </span>
                            <span style={{ ...mono, fontSize: 10, color: sv.cyan, fontWeight: 700 }}>
                                {progressPct}%
                            </span>
                        </div>
                        <div style={{ height: 4, background: sv.inkGhost, position: 'relative' }}>
                            <div
                                style={{
                                    position: 'absolute',
                                    left: 0,
                                    top: 0,
                                    height: '100%',
                                    width: `${progressPct}%`,
                                    background: sv.cyan,
                                    boxShadow: `0 0 8px ${sv.cyan}66`,
                                    transition: 'width 300ms ease-out',
                                }}
                            />
                        </div>
                        <p style={{ ...hintStyle, marginTop: 8 }}>
                            Submitting in batches of 50 episodes&hellip;
                        </p>
                    </div>
                )}

                {/* Waiting state (before first batch progress) */}
                {!isComplete && !batchProgress && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                        <div
                            style={{
                                width: 24,
                                height: 24,
                                border: `2px solid ${sv.line}`,
                                borderTopColor: sv.cyan,
                                borderRadius: '50%',
                                animation: 'svSpin 0.8s linear infinite',
                                flexShrink: 0,
                            }}
                        />
                        <span style={{ ...mono, fontSize: 12, color: sv.inkDim, letterSpacing: '0.12em' }}>
                            Preparing batches&hellip;
                        </span>
                    </div>
                )}

                {/* Error notice */}
                {fingerprintError && (
                    <SvNotice tone="error" style={{ marginBottom: 16 }}>
                        {fingerprintError}
                    </SvNotice>
                )}

                {/* Final result */}
                {isComplete && finalResult && (
                    <div>
                        <SvPanel
                            glow={finalResult.queued > 0}
                            style={{ marginBottom: 16 }}
                            accent={finalResult.queued > 0 ? `${sv.cyan}55` : sv.line}
                        >
                            <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
                                <div>
                                    <div style={{ ...mono, fontSize: 28, fontWeight: 700, color: finalResult.queued > 0 ? sv.cyan : sv.inkDim }}>
                                        {finalResult.queued}
                                    </div>
                                    <div style={{ ...mono, fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: sv.inkDim, marginTop: 4 }}>
                                        Episodes queued
                                    </div>
                                </div>
                                {finalResult.failed > 0 && (
                                    <div>
                                        <div style={{ ...mono, fontSize: 28, fontWeight: 700, color: sv.red }}>
                                            {finalResult.failed}
                                        </div>
                                        <div style={{ ...mono, fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: sv.inkDim, marginTop: 4 }}>
                                            Failed
                                        </div>
                                    </div>
                                )}
                            </div>
                        </SvPanel>

                        <p style={{ ...hintStyle, fontSize: 12, color: sv.inkDim, letterSpacing: '0.06em' }}>
                            {finalResult.queued > 0
                                ? 'Fingerprints are queued locally. They will be submitted to the community catalog when Engram processes future discs.'
                                : 'No episodes were queued. Select at least one show with a valid TMDB ID and try again.'}
                        </p>
                    </div>
                )}
            </div>
        );
    };

    // ─── Footer actions ───────────────────────────────────────────────────────

    const renderFooter = () => {
        switch (step) {
            case 'directory':
                return (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                        <SvActionButton tone="neutral" size="md" onClick={onClose}>
                            Cancel
                        </SvActionButton>
                        <SvActionButton tone="cyan" size="md" onClick={handleScan} disabled={!path.trim()}>
                            Scan Directory
                        </SvActionButton>
                    </div>
                );
            case 'scanning':
                return (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                        <SvActionButton tone="neutral" size="md" onClick={() => setStep('directory')}>
                            Cancel
                        </SvActionButton>
                    </div>
                );
            case 'review':
                return (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <span style={{ ...mono, fontSize: 10, color: sv.inkFaint, letterSpacing: '0.10em', flex: 1 }}>
                            {acceptedCount} show{acceptedCount !== 1 ? 's' : ''} accepted &middot; {acceptEpisodeCount} episode{acceptEpisodeCount !== 1 ? 's' : ''} to queue
                        </span>
                        <SvActionButton tone="neutral" size="md" onClick={() => setStep('directory')}>
                            ← Back
                        </SvActionButton>
                        <SvActionButton
                            tone="cyan"
                            size="md"
                            onClick={handleFingerprint}
                            disabled={acceptEpisodeCount === 0}
                        >
                            Confirm &rarr; Queue {acceptEpisodeCount > 0 ? `(${acceptEpisodeCount})` : ''}
                        </SvActionButton>
                    </div>
                );
            case 'fingerprint':
                return (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                        {finalResult ? (
                            <SvActionButton tone="cyan" size="md" onClick={onClose}>
                                Done
                            </SvActionButton>
                        ) : (
                            <SvActionButton tone="neutral" size="md" disabled>
                                Processing&hellip;
                            </SvActionButton>
                        )}
                    </div>
                );
        }
    };

    // ─── Render ───────────────────────────────────────────────────────────────

    return (
        /* Backdrop overlay */
        <div
            style={{
                position: 'fixed',
                inset: 0,
                background: 'rgba(0, 0, 0, 0.85)',
                backdropFilter: 'blur(4px)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                zIndex: 1100,
                padding: '1rem',
            }}
            onClick={onClose}
            role="dialog"
            aria-modal="true"
            aria-label="Bootstrap library fingerprints"
        >
            {/* Modal */}
            <div
                onClick={(e) => e.stopPropagation()}
                style={{
                    width: '100%',
                    maxWidth: 760,
                    maxHeight: '88vh',
                    display: 'flex',
                    flexDirection: 'column',
                    background: 'linear-gradient(180deg, rgba(18,24,39,0.96), rgba(10,14,24,0.99))',
                    border: `1px solid ${sv.lineHi}`,
                    boxShadow: `0 0 40px ${sv.cyan}1e, 0 0 80px ${sv.cyan}0f`,
                    position: 'relative',
                    overflow: 'hidden',
                }}
            >
                {/* Corner ticks */}
                <SvCorners />

                {/* Header */}
                <div
                    style={{
                        padding: '18px 24px 16px',
                        background: 'rgba(94, 234, 212, 0.03)',
                        borderBottom: `1px solid ${sv.line}`,
                        display: 'flex',
                        alignItems: 'flex-start',
                        justifyContent: 'space-between',
                        flexShrink: 0,
                    }}
                >
                    <div>
                        <div
                            style={{
                                fontFamily: sv.sans,
                                fontSize: 15,
                                fontWeight: 700,
                                letterSpacing: '0.22em',
                                textTransform: 'uppercase',
                                color: sv.cyanHi,
                                textShadow: `0 0 14px ${sv.cyan}77`,
                            }}
                        >
                            Bootstrap Library
                        </div>
                        <div
                            style={{
                                ...mono,
                                fontSize: 10,
                                letterSpacing: '0.14em',
                                textTransform: 'uppercase',
                                color: sv.inkDim,
                                marginTop: 3,
                            }}
                        >
                            Seed fingerprint network from existing media files &middot; TV shows only
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close bootstrap wizard"
                        style={{
                            background: 'transparent',
                            border: `1px solid rgba(255, 61, 127, 0.4)`,
                            color: sv.magenta,
                            width: 30,
                            height: 30,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            cursor: 'pointer',
                            fontSize: 16,
                            fontFamily: 'Arial, sans-serif',
                            flexShrink: 0,
                        }}
                        onMouseEnter={(e) => {
                            e.currentTarget.style.background = 'rgba(255, 61, 127, 0.10)';
                            e.currentTarget.style.boxShadow = `0 0 12px rgba(255, 61, 127, 0.45)`;
                        }}
                        onMouseLeave={(e) => {
                            e.currentTarget.style.background = 'transparent';
                            e.currentTarget.style.boxShadow = 'none';
                        }}
                    >
                        ✕
                    </button>
                </div>

                {/* Step bar */}
                <StepBar current={step} />

                {/* Body */}
                <div
                    style={{
                        flex: 1,
                        overflowY: 'auto',
                        scrollbarWidth: 'thin',
                        scrollbarColor: `rgba(94, 234, 212, 0.35) transparent`,
                    }}
                >
                    {step === 'directory' && renderDirectory()}
                    {step === 'scanning' && renderScanning()}
                    {step === 'review' && renderReview()}
                    {step === 'fingerprint' && renderFingerprint()}
                </div>

                {/* Footer */}
                <div
                    style={{
                        padding: '14px 24px',
                        borderTop: `1px solid ${sv.line}`,
                        background: 'rgba(5, 7, 12, 0.5)',
                        flexShrink: 0,
                    }}
                >
                    {renderFooter()}
                </div>
            </div>
        </div>
    );
}

export default BootstrapLibraryFlow;
