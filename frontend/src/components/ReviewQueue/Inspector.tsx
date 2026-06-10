import type { CSSProperties } from 'react';
import { Trash2, SkipForward } from 'lucide-react';
import { IcoRetry } from '../../app/components/icons';
import { SvActionButton, SvBadge, SvLabel, SvNotice, SvPanel, sv } from '../../app/components/synapse';
import { FEATURES, EPISODE_CONFIG } from '../../config/constants';
import type { DiscTitle } from '../../types';
import type { Candidate, CoverageEntry } from './coverage';
import type { LLMSuggestion, RosterEpisode } from './types';
import type { LLMFeedback } from './llmFeedback';
import {
    confidenceColor,
    formatDuration,
    formatSize,
    generateEpisodeOptions,
    parseMatchDetails,
    titleDisplayName,
} from './utils';

type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';

const monoFaint: CSSProperties = { fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint };

function pct(value: number): string {
    return `${Math.round(value * 100)}%`;
}

/**
 * Focused decision panel for one title: the disc-aware suggestion, the ranked
 * candidates with their evidence (votes, coverage, score gap) and conflict/gap
 * tags, plus a manual override. Assigning here updates the parent selection;
 * the header Save/Process persists it.
 */
export function Inspector({
    title,
    candidates,
    suggestion,
    selection,
    action,
    episodes,
    season,
    coverage,
    holders,
    titleIndexById,
    isRematching,
    aiEpisodeMatchingEnabled,
    llmFeedback,
    isLlmMatching,
    onAssign,
    onAction,
    onRematch,
    onDeepRematch,
    onTryLLMMatch,
    onAcceptLLMSuggestion,
}: {
    title: DiscTitle;
    candidates: Candidate[];
    suggestion: { code: string; name: string } | null;
    selection: string | undefined;
    action: TitleAction | undefined;
    episodes: RosterEpisode[];
    /** Effective season for manual/LLM codes: detected, else picker choice, else 1 (#370). */
    season: number;
    coverage: Record<string, CoverageEntry>;
    /** Episode code → title ids claiming it (roster-independent collision source). */
    holders: Map<string, number[]>;
    titleIndexById: Record<number, number>;
    isRematching: boolean;
    aiEpisodeMatchingEnabled: boolean;
    llmFeedback: LLMFeedback | null;
    isLlmMatching: boolean;
    onAssign: (code: string) => void;
    onAction: (action: TitleAction) => void;
    onRematch: (titleId: number, source: string, deep?: boolean) => void;
    onDeepRematch: (episodeCode: string) => void;
    onTryLLMMatch: (titleId: number) => void;
    onAcceptLLMSuggestion: (titleId: number, episodeNumber: number) => void;
}) {
    const details = parseMatchDetails(title);
    const fileExists = details.error === 'file_exists';
    const llmSuggestion: LLMSuggestion | null = details.llm_suggestion ?? null;
    // In-flight = the live WebSocket title state (durable, lasts the whole match)
    // OR the parent's optimistic isRematching (covers the gap before the first WS
    // message). The parent flag clears the instant the fire-and-forget POST returns,
    // so the title state is what keeps the spinner up while matching actually runs.
    const isMatching = title.state === 'matching' || isRematching;

    // The set of episode codes held by OTHER titles → conflict source. Derived
    // from live selections (not the roster) so collisions surface even when the
    // season roster is unavailable.
    const takenByOther = (code: string): number[] =>
        (holders.get(code) ?? []).filter((id) => id !== title.id);

    // This title's current pick collides with another title's pick.
    const selectionIsCode = !!selection && /^S\d+E\d+$/i.test(selection);
    const conflictWith = selectionIsCode ? takenByOther(selection as string) : [];
    const inConflict = conflictWith.length > 0;

    const stateBadge = fileExists ? (
        <SvBadge state="warn" dot>File exists</SvBadge>
    ) : !selection ? (
        <SvBadge state="review" dot>Needs review</SvBadge>
    ) : selection === 'extra' ? (
        <SvBadge tone={sv.cyan}>Extra</SvBadge>
    ) : selection === 'skip' ? (
        <SvBadge tone={sv.inkFaint}>Discarded</SvBadge>
    ) : (
        <SvBadge state="matched" dot>Assigned</SvBadge>
    );

    return (
        <SvPanel pad={0} accent={`${sv.yellow}55`} style={{ position: 'sticky', top: 88 }}>
            {/* header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '14px 16px', borderBottom: `1px solid ${sv.line}` }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: sv.display, fontSize: 15, color: sv.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {titleDisplayName(title)}
                    </div>
                    <div style={{ ...monoFaint, marginTop: 3 }}>
                        {formatDuration(title.duration_seconds)} · {formatSize(title.file_size_bytes)}
                        {title.video_resolution ? ` · ${title.video_resolution}` : ''} · {title.chapter_count} chapters
                    </div>
                </div>
                {stateBadge}
            </div>

            <div style={{ padding: 16 }}>
                {fileExists && details.message && (
                    <div style={{ marginBottom: 14 }}>
                        <SvNotice tone="warn">{details.message}</SvNotice>
                    </div>
                )}

                {/* Conflict → deep re-match all titles claiming this episode */}
                {inConflict && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', marginBottom: 14, border: `1px solid ${sv.red}`, background: `${sv.red}12` }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontFamily: sv.display, fontSize: 13, color: sv.red }}>
                                ⚠ {selection} also claimed by {conflictWith.map((id) => `#${titleIndexById[id] ?? id}`).join(', ')}
                            </div>
                            <div style={{ ...monoFaint, marginTop: 2, fontSize: 10.5 }}>
                                Deep re-match re-runs every claiming title with denser sampling + stricter votes to break the tie.
                            </div>
                        </div>
                        <SvActionButton
                            tone="magenta"
                            size="sm"
                            onClick={() => onDeepRematch(selection as string)}
                            disabled={isMatching}
                        >
                            <IcoRetry size={11} className={isMatching ? 'animate-spin' : ''} />
                            {isMatching ? 'Re-matching…' : 'Deep re-match'}
                        </SvActionButton>
                    </div>
                )}

                {/* disc-aware suggestion */}
                {suggestion && selection !== suggestion.code && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', marginBottom: 14, border: `1px solid ${sv.yellow}`, background: `${sv.yellow}12` }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontFamily: sv.display, fontSize: 13, color: sv.yellow }}>
                                ↳ Suggested: {suggestion.code}{suggestion.name ? ` — ${suggestion.name}` : ''}
                            </div>
                            <div style={{ ...monoFaint, marginTop: 2, fontSize: 10.5 }}>
                                Only unfilled gap left on this disc.
                            </div>
                        </div>
                        <SvActionButton tone="yellow" size="sm" onClick={() => onAssign(suggestion.code)}>
                            Accept
                        </SvActionButton>
                    </div>
                )}

                {/* LLM suggestion */}
                {llmSuggestion && (
                    <div style={{ marginBottom: 14 }}>
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 14px', border: `1px solid ${sv.cyan}`, background: `${sv.cyan}0d` }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                                    <span style={{ fontFamily: sv.mono, fontSize: 10, fontWeight: 700, letterSpacing: '0.16em', color: sv.cyan, background: `${sv.cyan}22`, padding: '1px 6px', border: `1px solid ${sv.cyan}55` }}>
                                        AI
                                    </span>
                                    <span style={{ fontFamily: sv.display, fontSize: 13, color: sv.cyan }}>
                                        Suggested: <strong>S{String(season).padStart(2, '0')}E{String(llmSuggestion.episode).padStart(2, '0')}</strong>
                                    </span>
                                    <span style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>
                                        {Math.round(llmSuggestion.confidence * 100)}%
                                    </span>
                                </div>
                                <div style={{ fontFamily: sv.mono, fontSize: 10.5, color: sv.inkDim, lineHeight: 1.5 }}>
                                    {llmSuggestion.reasoning}
                                </div>
                            </div>
                            <SvActionButton
                                tone="cyan"
                                size="sm"
                                onClick={() => onAcceptLLMSuggestion(title.id, llmSuggestion.episode)}
                            >
                                Accept AI suggestion
                            </SvActionButton>
                        </div>
                    </div>
                )}

                {/* AI match feedback — silent outcomes (no confident match / error).
                    Shares the suggestion slot; only shown when there is no suggestion. */}
                {!llmSuggestion && llmFeedback && (
                    <div style={{ marginBottom: 14 }}>
                        <SvNotice tone={llmFeedback.tone}>› {llmFeedback.text}</SvNotice>
                    </div>
                )}

                {/* ranked candidates */}
                <SvLabel>Ranked candidates</SvLabel>
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {candidates.length === 0 && (
                        <div style={{ ...monoFaint, padding: '12px 0', textAlign: 'center' }}>
                            No match data — use manual assignment below.
                        </div>
                    )}
                    {candidates.map((cand) => {
                        const others = takenByOther(cand.episodeCode);
                        const isGap = coverage[cand.episodeCode]?.status === 'missing';
                        const isSelected = selection === cand.episodeCode;
                        const barColor = confidenceColor(cand.score);
                        const accent = others.length
                            ? `${sv.red}88`
                            : isSelected
                              ? sv.cyan
                              : isGap
                                ? `${sv.yellow}88`
                                : sv.lineMid;
                        return (
                            <div
                                key={cand.episodeCode}
                                style={{
                                    border: `1px solid ${accent}`,
                                    background: others.length ? `${sv.red}0d` : isGap ? `${sv.yellow}0d` : sv.bg0,
                                    padding: '11px 13px',
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
                                    <div style={{ minWidth: 0 }}>
                                        <div style={{ fontFamily: sv.mono, fontSize: 13, fontWeight: 600, color: sv.ink }}>
                                            {cand.episodeCode}
                                            {cand.episodeName && (
                                                <span style={{ color: sv.inkDim, fontWeight: 400, fontSize: 11.5 }}> — {cand.episodeName}</span>
                                            )}
                                        </div>
                                    </div>
                                    <span style={{ fontFamily: sv.display, fontSize: 20, fontWeight: 600, lineHeight: 1, color: barColor }}>
                                        {pct(cand.score)}
                                    </span>
                                </div>

                                {/* tags */}
                                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                                    {cand.isBest && <SvBadge size="sm" tone={sv.inkDim} dot={false}>top score</SvBadge>}
                                    {others.length > 0 && (
                                        <SvBadge size="sm" state="error" dot>
                                            conflict · #{titleIndexById[others[0]] ?? others[0]}
                                        </SvBadge>
                                    )}
                                    {isGap && others.length === 0 && (
                                        <SvBadge size="sm" tone={sv.yellow} dot>fills gap</SvBadge>
                                    )}
                                    {isSelected && others.length === 0 && (
                                        <SvBadge size="sm" tone={sv.cyan} dot>selected</SvBadge>
                                    )}
                                </div>

                                {/* score bar */}
                                <div style={{ height: 5, background: sv.bg3, marginTop: 9, position: 'relative', overflow: 'hidden' }}>
                                    <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${Math.round(cand.score * 100)}%`, background: barColor }} />
                                </div>

                                {/* evidence + assign */}
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginTop: 9 }}>
                                    <div style={{ ...monoFaint, fontSize: 10, display: 'flex', gap: 14, letterSpacing: '0.04em' }}>
                                        {cand.voteCount != null && (
                                            <span>
                                                <span style={{ color: sv.inkDim }}>{cand.voteCount}</span>
                                                {cand.targetVotes ? `/${cand.targetVotes}` : ''} votes
                                            </span>
                                        )}
                                        {cand.isBest && details.file_cov != null && (
                                            <span><span style={{ color: sv.inkDim }}>{pct(details.file_cov)}</span> coverage</span>
                                        )}
                                        {cand.isBest && details.score_gap != null && (
                                            <span>gap <span style={{ color: sv.inkDim }}>+{pct(details.score_gap)}</span></span>
                                        )}
                                    </div>
                                    <SvActionButton
                                        tone={isGap && others.length === 0 ? 'yellow' : 'neutral'}
                                        size="sm"
                                        onClick={() => onAssign(cand.episodeCode)}
                                    >
                                        {others.length > 0 ? 'Assign anyway' : isSelected ? 'Selected' : 'Assign'}
                                    </SvActionButton>
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* manual override */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 14, paddingTop: 14, borderTop: `1px dashed ${sv.lineMid}` }}>
                    {/* row 1: label + episode picker */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ ...monoFaint, fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase' }}>Manual</span>
                        <select
                            value={selection && /^S\d+E\d+$/i.test(selection) ? selection : ''}
                            onChange={(e) => e.target.value && onAssign(e.target.value)}
                            aria-label={`Manual episode for title ${title.title_index}`}
                            style={{
                                flex: 1,
                                background: sv.bg0,
                                border: `1px solid ${sv.lineMid}`,
                                color: sv.ink,
                                fontFamily: sv.mono,
                                fontSize: 12,
                                padding: '7px 9px',
                                outline: 'none',
                                cursor: 'pointer',
                            }}
                        >
                            <option value="">Pick episode…</option>
                            {episodes.length > 0
                                ? episodes.map((ep) => (
                                      <option key={ep.episode_code} value={ep.episode_code}>
                                          {`E${String(ep.episode_number).padStart(2, '0')}`}
                                          {ep.name ? ` — ${ep.name}` : ''}
                                      </option>
                                  ))
                                : generateEpisodeOptions(
                                      season,
                                      EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON,
                                  ).map((code) => (
                                      <option key={code} value={code}>
                                          {code}
                                      </option>
                                  ))}
                        </select>
                    </div>
                    {/* row 2: action buttons */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>
                        <SvActionButton
                            tone={action === 'extra' ? 'cyan' : 'neutral'}
                            size="sm"
                            onClick={() => onAction('extra')}
                            title="Keep as extra content"
                        >
                            Extra
                        </SvActionButton>
                        <SvActionButton
                            tone={action === 'discard' ? 'red' : 'neutral'}
                            size="sm"
                            onClick={() => onAction('discard')}
                            title="Discard this title"
                            ariaLabel="Discard"
                        >
                            <Trash2 size={11} />
                        </SvActionButton>
                        <SvActionButton
                            tone="neutral"
                            size="sm"
                            onClick={() => onAction('skip')}
                            title="Skip for now"
                            ariaLabel="Skip"
                        >
                            <SkipForward size={11} />
                        </SvActionButton>
                        {aiEpisodeMatchingEnabled && (
                            <SvActionButton
                                tone="cyan"
                                size="sm"
                                onClick={() => onTryLLMMatch(title.id)}
                                disabled={isLlmMatching}
                                title="Run AI episode matching"
                            >
                                {isLlmMatching ? (
                                    <>
                                        <IcoRetry size={11} className="animate-spin" /> Matching…
                                    </>
                                ) : (
                                    'Try AI match'
                                )}
                            </SvActionButton>
                        )}
                        {/* Per-track deep re-match — re-run the matcher on just this title
                            at strict depth/votes (distinct from the conflict banner, which
                            re-matches every title claiming the contested episode). */}
                        <SvActionButton
                            tone="magenta"
                            size="sm"
                            onClick={() => onRematch(title.id, 'engram', true)}
                            disabled={isMatching}
                            title="Deep re-match this track (denser sampling + stricter votes)"
                            ariaLabel="Deep re-match this track"
                        >
                            <IcoRetry size={11} className={isMatching ? 'animate-spin' : ''} />
                            {isMatching ? 'Re-matching…' : 'Re-match'}
                        </SvActionButton>
                        {FEATURES.DISCDB && title.discdb_match_details && title.match_details && (
                            <SvActionButton
                                tone="magenta"
                                size="sm"
                                onClick={() => onRematch(title.id, title.match_source === 'discdb' ? 'engram' : 'discdb')}
                                title="Toggle match source"
                                ariaLabel="Toggle match source"
                            >
                                <IcoRetry size={11} />
                            </SvActionButton>
                        )}
                    </div>
                </div>
            </div>
        </SvPanel>
    );
}
