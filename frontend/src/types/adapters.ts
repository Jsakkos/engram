import { Job, DiscTitle, TitleState as BackendTitleState } from './index';
import { DiscData, Track, TrackState, DiscState, MediaType, MatchCandidate } from '../app/components/DiscCard';

/**
 * Adapter layer to transform backend API types into UI component types
 */

export function mapJobStateToDiscState(jobState: Job['state']): DiscState {
  const stateMap: Record<Job['state'], DiscState> = {
    'idle': 'idle',
    'identifying': 'scanning',
    'review_needed': 'scanning',
    'ripping': 'ripping',
    'matching': 'processing',
    'organizing': 'processing',
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
    'matching': 'matching',
    'matched': 'matched',
    'review': 'matched',
    'completed': 'matched',
    'failed': 'failed'
  };

  return stateMap[titleState] || 'pending';
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

  return {
    id: job.id.toString(),
    title: job.detected_title || job.volume_label,
    subtitle: `${displayType} • ${job.volume_label}`,
    discLabel: job.volume_label,
    coverUrl: `/api/jobs/${job.id}/poster`,
    mediaType: mediaType,
    state: mapJobStateToDiscState(job.state),
    progress: job.progress_percent || 0,
    currentSpeed: job.current_speed,
    etaSeconds: job.eta_seconds,
    subtitleStatus: job.subtitle_status || undefined,
    tracks: titles.map(title => transformDiscTitleToTrack(title, job))
  };
}

function extractFinalMatchInfo(title: DiscTitle): { confidence: number; votes: number; targetVotes: number } | undefined {
  if (!title.match_details) return undefined;

  try {
    const details = typeof title.match_details === 'string'
      ? JSON.parse(title.match_details)
      : title.match_details;

    // For matched tracks, the winning match info is at the top level
    if (details.score !== undefined && details.vote_count !== undefined) {
      return {
        confidence: details.score || 0,
        votes: details.vote_count ?? 0,
        targetVotes: details.target_votes ?? details.total_chunks ?? 5
      };
    }

    return undefined;
  } catch (error) {
    console.error('Failed to parse match_details for final match info:', error);
    return undefined;
  }
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
    duration: formatDuration(title.duration_seconds),
    state: trackState,
    progress: trackState === 'matching'
      ? (title.match_progress ?? 0)
      : trackState === 'ripping'
        ? (title.actual_size_bytes && title.expected_size_bytes
            ? (title.actual_size_bytes / title.expected_size_bytes) * 100
            : 0)
        : (title.match_confidence || 0) * 100,
    matchCandidates: extractMatchCandidates(title),
    finalMatch: title.matched_episode || undefined,
    finalMatchConfidence: finalMatchInfo?.confidence,
    finalMatchVotes: finalMatchInfo?.votes,
    finalMatchTargetVotes: finalMatchInfo?.targetVotes,

    // File tracking
    outputFilename: title.output_filename || undefined,
    organizedFrom: title.organized_from || undefined,
    organizedTo: title.organized_to || undefined,
    isExtra: title.is_extra || undefined,

    // Quality metadata
    videoResolution: title.video_resolution || undefined,
    edition: title.edition || undefined,

    // Size tracking
    fileSizeBytes: title.file_size_bytes || undefined,
    expectedSizeBytes: title.expected_size_bytes || undefined,
    actualSizeBytes: title.actual_size_bytes || undefined,
    chapterCount: title.chapter_count || undefined,
  };
}

function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
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
  score?: number;
  vote_count?: number;
  target_votes?: number;
  total_chunks?: number;
  episode?: string;
}

function extractMatchCandidates(title: DiscTitle): MatchCandidate[] | undefined {
  if (!title.match_details) return undefined;

  try {
    const details: MatchDetails = typeof title.match_details === 'string'
      ? JSON.parse(title.match_details)
      : title.match_details;

    // Map runner_ups to candidates if available
    if (details.runner_ups && Array.isArray(details.runner_ups) && details.runner_ups.length > 0) {
      return details.runner_ups.map((ru: RunnerUp) => ({
        episode: ru.episode || 'Unknown',
        confidence: ru.score || ru.confidence || 0,     // Backend uses 'score'
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
        confidence: details.score || 0,
        votes: details.vote_count ?? 0,
        targetVotes: details.target_votes ?? details.total_chunks ?? 5
      }];
    }

    return undefined;
  } catch (error) {
    console.error('Failed to parse match_details:', error);
    return undefined;
  }
}
