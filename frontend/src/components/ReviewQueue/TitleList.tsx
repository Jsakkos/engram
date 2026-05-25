import type { CSSProperties } from 'react';
import { SvBadge, sv } from '../../app/components/synapse';
import type { DiscTitle } from '../../types';
import { confidenceColor, formatDuration, formatSize, titleDisplayName } from './utils';

const truncate: CSSProperties = {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
};

/** What the user has currently chosen for a title (selection beats raw match). */
function assignmentLabel(
    selection: string | undefined,
    episodeName: (code: string) => string,
): { text: string; color: string } {
    if (!selection) return { text: 'needs review', color: sv.yellow };
    if (selection === 'extra') return { text: 'extra', color: sv.cyan };
    if (selection === 'skip') return { text: 'discarded', color: sv.inkFaint };
    const name = episodeName(selection);
    return { text: name ? `${selection} · ${name}` : selection, color: sv.cyan };
}

/**
 * Compact, scannable list of the disc's titles. Selecting a row opens it in the
 * inspector. Each row shows the current assignment, confidence, and any
 * needs-review / conflict flags so the whole disc reads at a glance.
 */
export function TitleList({
    titles,
    selectedTitleId,
    selections,
    collisions,
    episodeName,
    onSelect,
}: {
    titles: DiscTitle[];
    selectedTitleId: number | null;
    selections: Record<number, string>;
    collisions: Set<string>;
    episodeName: (code: string) => string;
    onSelect: (titleId: number) => void;
}) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {titles.map((title) => {
                const selection = selections[title.id];
                const assignment = assignmentLabel(selection, episodeName);
                const inConflict = !!selection && collisions.has(selection);
                const isActive = title.id === selectedTitleId;
                const needsReview = !selection;
                const accent = inConflict ? sv.red : needsReview ? sv.yellow : sv.line;

                return (
                    <button
                        key={title.id}
                        type="button"
                        onClick={() => onSelect(title.id)}
                        aria-pressed={isActive}
                        style={{
                            position: 'relative',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 12,
                            width: '100%',
                            textAlign: 'left',
                            padding: '12px 14px',
                            background: isActive ? 'rgba(94,234,212,0.06)' : sv.bg1,
                            border: `1px solid ${accent}${accent === sv.line ? '' : '66'}`,
                            color: sv.ink,
                            cursor: 'pointer',
                            boxShadow: isActive ? `0 0 0 1px ${sv.cyan}, 0 0 18px ${sv.cyan}28` : undefined,
                            transition: 'border-color 120ms, box-shadow 120ms, background 120ms',
                        }}
                    >
                        <SvBadge size="sm" tone={sv.inkDim} dot={false}>
                            #{title.title_index}
                        </SvBadge>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ ...truncate, fontFamily: sv.mono, fontSize: 12.5, color: sv.cyanHi }}>
                                {titleDisplayName(title)}
                            </div>
                            <div style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint, marginTop: 3, letterSpacing: '0.06em' }}>
                                {formatDuration(title.duration_seconds)} · {formatSize(title.file_size_bytes)}
                            </div>
                        </div>
                        <div style={{ ...truncate, maxWidth: 200, textAlign: 'right', fontFamily: sv.mono, fontSize: 11, color: assignment.color }}>
                            {assignment.text}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                            {inConflict && (
                                <SvBadge size="sm" state="error" dot>
                                    conflict
                                </SvBadge>
                            )}
                            <span
                                style={{
                                    fontFamily: sv.display,
                                    fontSize: 14,
                                    fontWeight: 600,
                                    minWidth: 44,
                                    textAlign: 'right',
                                    color: title.match_confidence > 0 ? confidenceColor(title.match_confidence) : sv.inkFaint,
                                }}
                            >
                                {title.match_confidence > 0 ? `${Math.round(title.match_confidence * 100)}%` : '—'}
                            </span>
                        </div>
                    </button>
                );
            })}
        </div>
    );
}
