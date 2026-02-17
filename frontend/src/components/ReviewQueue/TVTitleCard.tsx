/**
 * TV title card component with match details and episode selection
 */

import { DiscTitle, Job } from '../../types';
import { MatchDetails } from './types';
import { formatDuration, formatSize, getDetailedConfidenceTooltip, parseMatchDetails } from './utils';
import { MATCHING_CONFIG } from '../../config/constants';
import { EpisodeSelector } from './EpisodeSelector';

interface TVTitleCardProps {
    title: DiscTitle;
    job: Job | null;
    selectedEpisode: string;
    isExpanded: boolean;
    onEpisodeChange: (titleId: number, episodeCode: string) => void;
    onToggleExpand: (titleId: number) => void;
}

export function TVTitleCard({
    title,
    job,
    selectedEpisode,
    isExpanded,
    onEpisodeChange,
    onToggleExpand,
}: TVTitleCardProps) {
    const details: MatchDetails = parseMatchDetails(title);
    const isConflict = details.error === 'file_exists';
    const matchesFound = details.matches_found;

    const getAlternativeMatches = (): Array<{ episode: string; confidence: number; vote_count?: number }> => {
        return details.runner_ups || [];
    };

    const getReviewReason = (): JSX.Element => {
        const reasons: string[] = [];

        if (title.match_confidence < MATCHING_CONFIG.HIGH_CONFIDENCE) {
            reasons.push('Low confidence');
        }
        if (details.vote_count && details.vote_count < MATCHING_CONFIG.MIN_VOTES) {
            reasons.push('Few votes');
        }
        if (details.conflict_reason) {
            reasons.push('Lost conflict');
        }
        if (details.score_gap !== undefined && details.score_gap < 0.1) {
            reasons.push('Close competition');
        }
        if (!title.matched_episode) {
            reasons.push('No match found');
        }

        return (
            <div className="review-reasons">
                {reasons.length === 0 ? (
                    <span className="reason-tag neutral">Manual check</span>
                ) : (
                    reasons.map((r, i) => (
                        <span key={i} className="reason-tag">
                            {r}
                        </span>
                    ))
                )}
            </div>
        );
    };

    return (
        <div className="title-row-wrapper">
            <div className={`title-grid-row ${title.match_confidence > MATCHING_CONFIG.MIN_CONFIDENCE ? 'has-match' : ''} ${isConflict ? 'conflict-row' : ''}`}>
                <div className="col-title">
                    <button
                        className="expand-matches-btn"
                        onClick={() => onToggleExpand(title.id)}
                    >
                        {isExpanded ? '▼' : '▶'}
                    </button>
                    <span className="title-index">#{title.title_index}</span>
                    <div className="title-info-col">
                        <span className="title-name">
                            {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                        </span>
                        {isConflict && (
                            <div className="title-error">
                                ⚠️ File already exists in library
                            </div>
                        )}
                    </div>
                </div>
                <div className="col-duration">
                    {formatDuration(title.duration_seconds)}
                </div>
                <div className="col-size">
                    {formatSize(title.file_size_bytes)}
                </div>
                <div className="col-confidence">
                    {title.match_confidence > 0 ? (
                        <span
                            className={`confidence-badge ${title.match_confidence >= MATCHING_CONFIG.AUTO_MATCH_THRESHOLD ? 'high' : title.match_confidence >= MATCHING_CONFIG.MIN_CONFIDENCE ? 'medium' : 'low'}`}
                            title={getDetailedConfidenceTooltip(title)}
                            style={{ cursor: 'help' }}
                        >
                            {Math.round(title.match_confidence * 100)}%
                        </span>
                    ) : (
                        <span className="confidence-badge none">—</span>
                    )}
                </div>
                <div className="col-stats">
                    {matchesFound !== undefined ? (
                        <div className="match-stats-detail">
                            <div className="stat-row">
                                <span className="stat-label">Votes:</span>
                                <span className="stat-value">{details.vote_count || '?'}</span>
                            </div>
                            <div className="stat-row">
                                <span className="stat-label">Coverage:</span>
                                <span className="stat-value">{Math.round((details.file_cov || 0) * 100)}%</span>
                            </div>
                            <div className="stat-row">
                                <span className="stat-label">Gap:</span>
                                <span className="stat-value">
                                    {details.score_gap !== undefined
                                        ? `+${Math.round(details.score_gap * 100)}%`
                                        : '—'}
                                </span>
                            </div>
                        </div>
                    ) : (
                        <span className="stat-empty">—</span>
                    )}
                </div>
                <div className="col-review-reason">
                    {getReviewReason()}
                </div>
                <div className="col-episode">
                    <EpisodeSelector
                        title={title}
                        job={job}
                        selectedEpisode={selectedEpisode}
                        onEpisodeChange={onEpisodeChange}
                    />
                </div>
            </div>

            {/* Expandable competing matches section */}
            {isExpanded && (
                <div className="competing-matches-section">
                    <h4>All Competing Matches:</h4>
                    <table className="matches-table">
                        <thead>
                            <tr>
                                <th>Rank</th>
                                <th>Episode</th>
                                <th>Score</th>
                                <th>Votes</th>
                                <th>Assessment</th>
                            </tr>
                        </thead>
                        <tbody>
                            {/* Best match */}
                            {title.matched_episode && (
                                <tr className="match-row best-match">
                                    <td>🥇 1st</td>
                                    <td><strong>{title.matched_episode}</strong></td>
                                    <td>{Math.round(title.match_confidence * 100)}%</td>
                                    <td>{details.vote_count || '?'}</td>
                                    <td>
                                        <span className="assessment-badge primary">Best Match</span>
                                    </td>
                                </tr>
                            )}

                            {/* Runner-ups */}
                            {getAlternativeMatches().map((alt, idx) => (
                                <tr key={idx} className="match-row runner-up">
                                    <td>{idx === 0 ? '🥈' : idx === 1 ? '🥉' : `${idx + 2}th`}</td>
                                    <td>{alt.episode}</td>
                                    <td>{Math.round(alt.confidence * 100)}%</td>
                                    <td>{alt.vote_count || '?'}</td>
                                    <td>
                                        <span className="assessment-badge">
                                            {alt.confidence > MATCHING_CONFIG.MIN_CONFIDENCE ? 'Possible' : 'Unlikely'}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
