/**
 * API helpers for settings export and import (#731).
 *
 * The export is fetched and saved as a blob rather than opened as a link, so a
 * file carrying keys never sits in browser history as a URL. Import bodies go up
 * as application/json: the backend refuses anything else, which is what keeps a
 * cross-site form from posting a settings file on the user's behalf.
 */

export interface ImportPreviewField {
    key: string;
    status: 'change' | 'same' | 'invalid';
    current: unknown;
    incoming: unknown;
    secret: boolean;
    path_missing: boolean;
    path_unchecked: boolean;
    /** Repoints a server that receives keys or data, or a program Engram runs. */
    needs_review: boolean;
    /** Host of an incoming secret URL (the Discord webhook); its path is never sent. */
    incoming_host?: string | null;
    error: string | null;
    selected: boolean;
}

export interface ImportPreview {
    schema_version: number | null;
    engram_version: string | null;
    includes_secrets: boolean;
    warnings: string[];
    fields: ImportPreviewField[];
    unknown: string[];
}

/** A refused import: `errors` maps each rejected setting to the reason. */
export class SettingsImportError extends Error {
    errors: Record<string, string>;

    constructor(message: string, errors: Record<string, string> = {}) {
        super(message);
        this.errors = errors;
    }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function failure(response: Response): Promise<SettingsImportError> {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    if (detail && typeof detail === 'object' && detail.errors) {
        const errors = detail.errors as Record<string, string>;
        // The per-setting reasons are shown beside each row; this is the banner.
        return new SettingsImportError('Some settings could not be applied. Nothing was changed.', errors);
    }
    return new SettingsImportError(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
}

export async function downloadSettings(includeSecrets: boolean): Promise<void> {
    const response = await fetch(`/api/config/export?include_secrets=${includeSecrets}`);
    if (!response.ok) throw await failure(response);
    const disposition = response.headers.get('content-disposition') ?? '';
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'engram-settings.json';
    const url = URL.createObjectURL(await response.blob());
    try {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
    } finally {
        URL.revokeObjectURL(url);
    }
}

export async function previewSettingsImport(file: unknown): Promise<ImportPreview> {
    const response = await fetch('/api/config/import/preview', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(file),
    });
    if (!response.ok) throw await failure(response);
    return response.json();
}

export async function applySettingsImport(file: unknown, keys: string[]): Promise<string[]> {
    const response = await fetch('/api/config/import/apply', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ file, keys }),
    });
    if (!response.ok) throw await failure(response);
    return (await response.json()).applied;
}

// Friendly names for the settings people most often move. Anything missing
// falls back to the humanized key, so a setting added later still renders.
const LABELS: Record<string, string> = {
    staging_path: 'Staging folder',
    library_movies_path: 'Movies library folder',
    library_tv_path: 'TV library folder',
    backup_path: 'Disc backup folder',
    backup_before_rip: 'Back up disc before ripping',
    makemkv_path: 'MakeMKV program',
    makemkv_key: 'MakeMKV license key',
    ffmpeg_path: 'FFmpeg program',
    fpcalc_path: 'fpcalc program',
    tmdb_api_key: 'TMDB token',
    tvdb_api_key: 'TheTVDB key',
    ai_api_key: 'AI provider key',
    ai_provider: 'AI provider',
    ai_model: 'AI model',
    opensubtitles_api_key: 'OpenSubtitles key',
    opensubtitles_username: 'OpenSubtitles username',
    opensubtitles_password: 'OpenSubtitles password',
    discdb_api_key: 'TheDiscDB key',
    discdb_export_path: 'TheDiscDB export folder',
    discord_webhook_url: 'Discord webhook',
    dashboard_base_url: 'Dashboard URL',
    import_watch_path: 'Import watch folder',
    extras_policy: 'Extras handling',
    always_review: 'Always review',
    conflict_resolution_default: 'When a file already exists',
    naming_season_format: 'Season folder format',
    naming_episode_format: 'Episode file format',
    naming_movie_format: 'Movie folder format',
    naming_movie_file_format: 'Movie file format',
    naming_tv_show_format: 'Show folder format',
    max_concurrent_matches: 'Parallel matches',
};

export function settingLabel(key: string): string {
    if (LABELS[key]) return LABELS[key];
    const words = key.replace(/_/g, ' ');
    return words.charAt(0).toUpperCase() + words.slice(1);
}
