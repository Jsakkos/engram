import { Job, DiscTitle, TitleState as BackendTitleState } from './index';
import { DiscData, Track, TrackState, DiscState, MediaType, MatchCandidate } from '../app/components/DiscCard';
import { formatDurationLongFloored } from '../utils/formatting';
import { getRerippableStateFromTitle } from '../components/ReviewQueue/rerip';

/**
 * Adapter layer to transform backend API types into UI component types
 */

export function mapJobStateToDiscState(jobState: Job['state']): DiscState {
  const stateMap: Record<Job['state'], DiscState> = {
    'idle': 'idle',
    'identifying': 'scanning',
    'review_needed': 'review_needed',
    'ripping': 'ripping',
    'matching': 'matching',
    'organizing': 'organizing',
    'completed': 'completed',
    'failed': 'error'
  };
  return stateMap[jobState] || 'idle';
}

export function mapTitleStateToTrackState(
  titleState: BackendTitleState,
): TrackState {
  // Map backend states directly to UI states
  const stateMap: Record<BackendTitleState, TrackState> = {
    'pending': 'pending',
    'ripping': 'ripping',
    'queued': 'queued',
    'matching': 'matching',
    'matched': 'matched',
    'review': 'review',
    'completed': 'completed',
    'failed': 'failed'
  };

  return stateMap[titleState] || 'pending';
}

/** Count of same-name TMDB twins recorded on the job (0 when absent/malformed). */
function countCandidates(candidatesJson?: string | null): number {
  if (!candidatesJson) return 0;
  try {
    const parsed = JSON.parse(candidatesJson);
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

export function transformJobToDiscData(job: Job, titles: DiscTitle[]): DiscData {
  // Determine media type - handle case-insensitively
  const contentTypeLower = job.content_type?.toLowerCase();

  let mediaType: MediaType = 'unknown';
  if (contentTypeLower === 'movie') {
    mediaType = 'movie';
  } else if (contentTypeLower === 'tv' || contentTypeLower === 'series') {
    mediaType = 'tv';
  }

  const displayType = mediaType === 'movie' ? 'MOVIE' : mediaType === 'tv' ? 'TV' : 'DETECTING';

  // "Identity review" = the disc is in review to confirm WHICH show it is, not to
  // assign episodes. The episode review queue is a dead end here. Primary signal:
  // a null tmdb_id (the analyst withholds the id for an ambiguous/unconfirmed
  // disc). Fallback: a same-name collision (>=2 twins) the analyst kept a
  // best-guess id for (the no-year-twin case) — but only while nothing has been
  // ripped/matched yet, so a genuine post-rip wrong-show disc keeps its button.
  const hasProcessedTitles = titles.some(
    // 'failed' is intentionally excluded: a fully-failed job transitions to FAILED
    // state (not review_needed), so the outer state guard makes this unreachable.
    t => t.state === 'matched' || t.state === 'review' || t.state === 'completed',
  );
  const identityReview =
    job.state === 'review_needed' &&
    (job.tmdb_id == null ||
      (countCandidates(job.candidates_json) >= 2 && !hasProcessedTitles));

  return {
    id: job.id.toString(),
    title: job.detected_title || job.volume_label,
    subtitle: `${displayType} • ${job.volume_label}`,
    discLabel: job.volume_label,
    sourceType: job.drive_id === 'import' ? 'import'
      : job.drive_id === 'staging' ? 'staging'
      : 'disc',
    coverUrl: `/api/jobs/${job.id}/poster`,
    mediaType: mediaType,
    state: mapJobStateToDiscState(job.state),
    progress: job.progress_percent || 0,
    currentSpeed: job.current_speed,
    etaSeconds: job.eta_seconds,
    subtitleStatus: job.subtitle_status || undefined,
    subtitleError: job.subtitle_error_message || undefined,
    conflictStatus: job.conflict_status || undefined,
    reviewReason: job.review_reason || undefined,
    identityReview,
    tmdbId: job.tmdb_id ?? null,
    tmdbName: job.tmdb_name ?? null,
    tmdbYear: job.tmdb_year ?? null,
    startedAt: job.created_at
      ? (job.created_at.endsWith('Z') || job.created_at.includes('+') ? job.created_at : job.created_at + 'Z')
      : undefined,
    tracks: titles.map(title => transformDiscTitleToTrack(title, job)),
    hasDamagedTrack: titles.some(t => getRerippableStateFromTitle(t.match_details).isRerippable),
  };
}

/**
 * Parse a title's match_details JSON. Returns {} when missing or malformed.
 */
function parseMatchDetails(title: DiscTitle): MatchDetails {
  if (!title.match_details) return {};
  try {
    return typeof title.match_details === 'string'
      ? JSON.parse(title.match_details)
      : title.match_details;
  } catch {
    return {};
  }
}

function extractFinalMatchInfo(title: DiscTitle): { confidence: number; votes: number; targetVotes: number } | undefined {
  if (!title.match_details) return undefined;

  const details = parseMatchDetails(title);

  // For matched tracks, the winning match info is at the top level. Prefer the
  // calibrated `confidence` (0-1, reviewer-facing); fall back to raw `score` for
  // match_details that predate calibration.
  if (details.score !== undefined && details.vote_count !== undefined) {
    return {
      confidence: details.confidence ?? details.score ?? 0,
      votes: details.vote_count ?? 0,
      targetVotes: details.target_votes ?? details.total_chunks ?? 5
    };
  }

  return undefined;
}

/**
 * Which Engram matcher produced this result, inferred from match_details shape.
 * Only Engram-engine ASR sources carry a method worth surfacing — DiscDB / AI /
 * manual matches return undefined (their provider chip already says enough).
 *  - chunk_vote: ranked voting (has vote_count)
 *  - full_file:  whole-file fallback ({method:"full_transcription"}, or a bare
 *                score with no votes)
 */
function deriveMatchMethod(title: DiscTitle): "chunk_vote" | "full_file" | undefined {
  const source = title.match_source;
  if (source && source !== "engram" && source !== "engram_chromaprint") return undefined;
  const details = parseMatchDetails(title);
  if (details.vote_count !== undefined) return "chunk_vote";
  // "full_transcription" is the canonical signal; the bare-score fallback handles
  // rows written before that field was added. A null-source row with a score but
  // no vote_count is assumed to be a full-file result — the only way to reach that
  // state via the ASR path (backend match_source backfill is a separate follow-up).
  if (details.method === "full_transcription" || details.score !== undefined) return "full_file";
  return undefined;
}

function transformDiscTitleToTrack(title: DiscTitle, _job: Job): Track {
  // Determine track title: prefer matched episode, then output filename, then generic
  let trackTitle = title.matched_episode;
  if (!trackTitle && title.output_filename) {
    // Extract filename without path and extension
    const filename = title.output_filename.split(/[\\/]/).pop() || '';
    trackTitle = filename.replace(/\.mkv$/i, '');
  }
  if (!trackTitle) {
    trackTitle = `Title ${title.title_index}`;
  }

  // Extract final match info for matched tracks
  const finalMatchInfo = extractFinalMatchInfo(title);

  // Determine track state
  const trackState = mapTitleStateToTrackState(title.state);

  return {
    id: title.id.toString(),
    title: trackTitle,
    duration: formatDurationLongFloored(title.duration_seconds),
    state: trackState,
    progress: trackState === 'queued'
      ? 0  // enqueued, no work started yet — read as idle, not in-progress
      : trackState === 'matching'
      ? (title.match_progress ?? 0)
      : trackState === 'ripping'
        ? (title.actual_size_bytes && title.expected_size_bytes
            ? (title.actual_size_bytes / title.expected_size_bytes) * 100
            : 0)
        : (title.match_confidence || 0) * 100,
    matchCandidates: extractMatchCandidates(title),
    finalMatch: title.matched_episode || undefined,
    // Displayed confidence comes from the reliable match_confidence COLUMN, set on
    // every path that produces a result (ASR result.confidence, DiscDB 0.99, manual
    // 1.0). The column is 0 only when there's no confident result at all (subtitle
    // download failed, or a pre-match reset); fall back to the match_details
    // best-guess score then. This is what un-breaks the bare full-file card.
    finalMatchConfidence:
      title.match_confidence > 0 ? title.match_confidence : finalMatchInfo?.confidence,
    finalMatchVotes: finalMatchInfo?.votes,
    finalMatchTargetVotes: finalMatchInfo?.targetVotes,
    matchMethod: deriveMatchMethod(title),

    // File tracking
    outputFilename: title.output_filename || undefined,
    organizedFrom: title.organized_from || undefined,
    organizedTo: title.organized_to || undefined,
    isExtra: title.is_extra || undefined,

    // Quality metadata
    videoResolution: title.video_resolution || undefined,
    edition: title.edition || undefined,
    matchSource: title.match_source || undefined,

    // Size tracking
    fileSizeBytes: title.file_size_bytes || undefined,
    expectedSizeBytes: title.expected_size_bytes || undefined,
    actualSizeBytes: title.actual_size_bytes || undefined,
    chapterCount: title.chapter_count || undefined,

    // Error info (from WebSocket error_message or match_details.reason for FAILED titles)
    errorMessage: title.error_message || extractErrorReason(title) || undefined,
  };
}

interface RunnerUp {
  episode?: string;
  score?: number;
  confidence?: number;
  vote_count?: number;
  target_votes?: number;
}

interface MatchDetails {
  runner_ups?: RunnerUp[];
  score?: number;        // raw ranked_voting_score
  confidence?: number;   // calibrated 0-1, reviewer-facing
  vote_count?: number;
  target_votes?: number;
  total_chunks?: number;
  episode?: string;
  reason?: string;
  method?: string;       // e.g. "full_transcription" for the whole-file fallback
}

function extractErrorReason(title: DiscTitle): string | null {
  if (title.state !== 'failed' || !title.match_details) return null;
  return parseMatchDetails(title).reason || null;
}

function extractMatchCandidates(title: DiscTitle): MatchCandidate[] | undefined {
  if (!title.match_details) return undefined;

  const details = parseMatchDetails(title);

  // Map runner_ups to candidates if available
  if (details.runner_ups && Array.isArray(details.runner_ups) && details.runner_ups.length > 0) {
    return details.runner_ups.map((ru: RunnerUp) => ({
      episode: ru.episode || 'Unknown',
      // Prefer calibrated `confidence` (?? keeps a legitimate 0 for zero-vote
      // losers); fall back to raw `score` for pre-calibration match_details.
      confidence: ru.confidence ?? ru.score ?? 0,
      votes: ru.vote_count ?? Math.floor((ru.score || 0) * 5),  // Use actual vote_count (0 is valid!)
      targetVotes: ru.target_votes ?? details.target_votes ?? details.total_chunks ?? 5
    }));
  }

  // Fallback: synthesize a single candidate from top-level match info.
  // For decisive matches with only one candidate, runner_ups may be empty
  // but the top-level score/vote_count still describes the best match.
  if (details.score !== undefined && details.vote_count !== undefined) {
    return [{
      episode: details.episode || title.matched_episode || 'Matching...',
      confidence: details.confidence ?? details.score ?? 0,
      votes: details.vote_count ?? 0,
      targetVotes: details.target_votes ?? details.total_chunks ?? 5
    }];
  }

  return undefined;
}
