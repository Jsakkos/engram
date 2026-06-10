/**
 * Shared formatting helpers.
 *
 * Each exported function is copied verbatim from a former local definition.
 * Variants that produce different output for the same input are kept as
 * separate exports — they are NOT interchangeable.
 */

// ── Bytes ───────────────────────────────────────────────────────────────────

/**
 * Bytes → `123 B` / `1.5 KB` / `1.5 MB` / `1.5 GB`.
 * Tiers B/KB/MB/GB, one decimal on KB/MB/GB, raw integer on B.
 * (formerly TrackGrid.formatBytes)
 */
export function formatBytesBinary(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

/**
 * Bytes → `1.5 GB` / `512 MB` / `128 KB`, or `—` for falsy/non-finite input.
 * Tiers KB/MB/GB only, GB one decimal, MB/KB zero decimals.
 * (formerly DashboardSideRail.formatBytes)
 */
export function formatBytesCompact(bytes: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "—";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

/**
 * Bytes → `0 B` for zero, else log-tiered B/KB/MB/GB/TB.
 * One decimal for MB and larger, zero decimals for B and KB.
 * (formerly HistoryPage.formatBytes)
 */
export function formatBytesScaled(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

/**
 * Bytes → `1.50 GB`. Always GB, two decimals.
 * (formerly ReviewQueue/utils.formatSize)
 */
export function formatSizeGB(bytes: number): string {
  const gb = bytes / (1024 * 1024 * 1024);
  return `${gb.toFixed(2)} GB`;
}

// ── Duration ────────────────────────────────────────────────────────────────

/**
 * Seconds → coarse `45s` / `12m` / `2h 5m` (rounded).
 * (formerly HistoryPage.formatDuration)
 */
export function formatDurationCoarse(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

/**
 * Seconds → `m:ss` (no hours tier). Uses raw `seconds % 60`.
 * (formerly HistoryPage.formatTitleDuration, ContributePage.formatDuration,
 * EnhanceWizard.formatDuration)
 */
export function formatDurationShort(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Seconds → `h:mm:ss` when ≥1h, else `m:ss`. Uses raw `seconds % 60`.
 * (formerly ReviewQueue/utils.formatDuration)
 */
export function formatDurationLong(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) {
    return `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Seconds → `h:mm:ss` when ≥1h, else `m:ss`. Floors the seconds component
 * (`Math.floor(seconds % 60)`), so fractional input is truncated.
 * (formerly adapters.formatDuration)
 */
export function formatDurationLongFloored(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

// ── ETA ─────────────────────────────────────────────────────────────────────

/**
 * Seconds → `—` (falsy) / `< 1 min` / `N min` / `Nh Nm`.
 * (formerly DiscCard.formatEta)
 */
export function formatEta(seconds?: number): string {
  if (!seconds) return "—";
  if (seconds < 60) return "< 1 min";
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
}

/**
 * Seconds → `—` (falsy) / `< 1m` / `Nm`. Compact single-line ETA.
 * (formerly the inline ETA expression in App.tsx CompactList)
 */
export function formatEtaCompact(seconds?: number): string {
  if (!seconds) return "—";
  if (seconds < 60) return "< 1m";
  return `${Math.ceil(seconds / 60)}m`;
}

// ── Tool versions ───────────────────────────────────────────────────────────

/**
 * Reduce a raw tool version banner to a short human-readable form for the
 * setup wizard / bug report. Raw strings come from the backend detectors:
 * the full first line of `ffmpeg -version` (with Copyright tail), MakeMKV's
 * "MakeMKV v1.18.3 win(x64-release)" robot-mode line, or the literal
 * "(version probe timed out)" marker when the probe gave up.
 */
export function formatToolVersion(raw: string | null): string {
  if (!raw) return "Detected";
  if (/version probe timed out/i.test(raw)) return "Detected (version unknown)";
  const ffmpeg = raw.match(/ffmpeg version (\S+)/i);
  if (ffmpeg) return `ffmpeg ${ffmpeg[1].split(/-(?:full|essentials)_build/)[0]}`;
  const makemkv = raw.match(/MakeMKV v[\d.]+/i);
  if (makemkv) return makemkv[0];
  return raw.length > 60 ? `${raw.slice(0, 59)}…` : raw;
}

// ── Date / time ─────────────────────────────────────────────────────────────

/**
 * ISO string → localized `Mon D, YYYY, HH:MM`, or `—` for null input.
 * (formerly HistoryPage.formatDate)
 */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * ISO string → localized `Mon D, HH:MM:SS`, or `—` for null input.
 * (formerly HistoryPage.formatDateShort)
 */
export function formatDateTimeShort(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * ISO string → localized `Mon DD, YYYY`, or `—` for null / invalid input.
 */
export function formatDateOnly(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" });
}

/**
 * Epoch milliseconds → `HH:MM:SS` (local time-of-day).
 * (formerly DashboardSideRail.formatTime)
 */
export function formatTimeOfDay(ts: number): string {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}
