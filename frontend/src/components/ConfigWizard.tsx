import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { QRCodeSVG } from 'qrcode.react';
import { FEATURES } from '../config/constants';
import { EngramSelect } from './ui/EngramSelect';
import { SvActionButton } from '../app/components/synapse/SvActionButton';
import { BootstrapLibraryFlow } from './BootstrapLibraryFlow';
import GpuAccelerationSetting from './GpuAccelerationSetting';
import { requestTmdbValidation } from '../utils/tmdbValidation';
import { formatToolVersion } from '../utils/formatting';
import './ConfigWizard.css';

interface ConfigWizardProps {
    onClose: () => void;
    onComplete: () => void;
    isOnboarding?: boolean;
    /**
     * Settings mode only: open directly on a given section (e.g. "preferences")
     * or a finer-grained control (e.g. "gpu", which lands on Preferences and
     * scrolls the GPU control into view). Ignored during onboarding, which
     * always starts at step 1.
     */
    initialSection?: string;
}

const STEP_LABELS = ['Paths', 'Tools', 'TMDB', 'Data Sharing', 'Preferences'];

/**
 * Settings mode (opened from the gear) presents these as a jump-anywhere section
 * list instead of the onboarding stepper. Each maps to the same `step` index the
 * wizard flow already uses, so renderStepContent() is shared between both modes.
 */
interface SettingsSection {
    key: string;
    label: string;
    step: number;
}

const SETTINGS_SECTIONS: SettingsSection[] = [
    { key: 'paths', label: 'Library Paths', step: 1 },
    { key: 'tools', label: 'Tools & License', step: 2 },
    { key: 'metadata', label: 'Metadata & Subtitles', step: 3 },
    { key: 'sharing', label: 'Data Sharing', step: 4 },
    { key: 'preferences', label: 'Preferences', step: 5 },
];

// DOM id of the GPU control wrapper, used as a deep-link scroll target.
const GPU_ANCHOR_ID = 'setting-gpu-acceleration';

/**
 * Deep-link aliases and finer-grained anchors for `initialSection`. Plain section
 * keys resolve through SETTINGS_SECTIONS; this map adds alternate spellings and
 * sub-section anchors (e.g. the GPU control buried inside Preferences) so callers
 * like the ASR status badge can land the user exactly on the setting they clicked
 * toward instead of dumping them at step 1.
 */
const SECTION_DEEP_LINKS: Record<string, { step: number; anchorId?: string }> = {
    tmdb: { step: 3 },
    'data-sharing': { step: 4 },
    gpu: { step: 5, anchorId: GPU_ANCHOR_ID },
};

function resolveSection(section: string | undefined): { step: number; anchorId?: string } | null {
    if (!section) return null;
    if (section in SECTION_DEEP_LINKS) return SECTION_DEEP_LINKS[section];
    const match = SETTINGS_SECTIONS.find((s) => s.key === section);
    return match ? { step: match.step } : null;
}

const AI_PROVIDER_LABELS: Record<string, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    openrouter: 'OpenRouter',
    gemini: 'Google Gemini',
};

const AI_KEY_PLACEHOLDERS: Record<string, string> = {
    anthropic: 'sk-ant-...',
    openai: 'sk-...',
    openrouter: 'sk-or-...',
    gemini: 'AIzaSy...',
};

interface NamingPreset {
    id: string;
    seasonFormat: string;
    episodeFormat: string;
}

const NAMING_PRESETS: NamingPreset[] = [
    { id: 'plex', seasonFormat: 'Season {season:02d}', episodeFormat: '{show} - S{season:02d}E{episode:02d}' },
    { id: 'kodi', seasonFormat: 'Season {season:d}', episodeFormat: '{show} - S{season:02d}E{episode:02d}' },
    { id: 'minimal', seasonFormat: 'S{season:02d}', episodeFormat: '{show} - S{season:02d}E{episode:02d}' },
];

function SavedKeyBadge({ saved, text }: { saved: boolean; text: string }) {
    if (!saved) {
        return null;
    }
    return <span className="ml-2 text-xs font-normal text-green-500">{text}</span>;
}

interface ConfigData {
    stagingPath: string;
    makemkvPath: string;
    makemkvKey: string;
    libraryMoviesPath: string;
    libraryTvPath: string;
    tmdbApiKey: string;
    maxConcurrentMatches: number;
    ffmpegPath: string;
    conflictResolutionDefault: string;
    episodeOrderingPreference: 'aired' | 'dvd';
    watchdogEnabled: boolean;
    timeoutIdentifyingSeconds: number;
    timeoutRippingSeconds: number;
    timeoutMatchingSeconds: number;
    timeoutOrganizingSeconds: number;
    stagingCleanupPolicy: string;
    stagingCleanupDays: number;
    extrasPolicy: string;
    namingSeasonFormat: string;
    namingEpisodeFormat: string;
    namingMovieFormat: string;
    namingTvShowFormat: string;
    discdbEnabled: boolean;
    enableFingerprintContributions: boolean;
    fingerprintServerUrl: string;
    contributionPseudonym: string;
    fingerprintDisclosureAccepted: boolean;
    fingerprintDisclosureAcceptedAt: string | null;
    aiIdentificationEnabled: boolean;
    aiEpisodeMatchingEnabled: boolean;
    aiProvider: string;
    aiApiKey: string;
    discdbContributionsEnabled: boolean;
    discdbContributionTier: number;
    discdbExportPath: string;
    discdbApiKey: string;
    discdbApiUrl: string;
    opensubtitlesApiKey: string;
    opensubtitlesUsername: string;
    opensubtitlesPassword: string;
    allowLanAccess: boolean;
    importWatchPath: string;
    importDestinationMode: string;
}

interface NetworkInfo {
    lan_access_enabled: boolean;
    active_lan_bound: boolean;
    lan_ip: string | null;
    port: number;
    lan_url: string | null;
}

interface ToolDetectionResult {
    found: boolean;
    path: string | null;
    version: string | null;
    error: string | null;
}

interface DetectToolsResponse {
    makemkv: ToolDetectionResult;
    ffmpeg: ToolDetectionResult;
    platform: string;
}

function ConfigWizard({ onClose, onComplete, isOnboarding = true, initialSection }: ConfigWizardProps) {
    // Settings mode can deep-link to a section; onboarding always starts at step 1.
    const [step, setStep] = useState(() => (!isOnboarding ? resolveSection(initialSection)?.step : undefined) ?? 1);
    const [isLoading, setIsLoading] = useState(true);
    // A deep-linked control (e.g. the GPU toggle) to scroll into view once the
    // settings body has rendered. Consumed once.
    const pendingScrollAnchor = useRef<string | null>(
        !isOnboarding ? (resolveSection(initialSection)?.anchorId ?? null) : null,
    );
    const [config, setConfig] = useState<ConfigData>({
        stagingPath: '',
        makemkvPath: '',
        makemkvKey: '',
        libraryMoviesPath: '',
        libraryTvPath: '',
        tmdbApiKey: '',
        maxConcurrentMatches: 2,
        ffmpegPath: '',
        conflictResolutionDefault: 'ask',
        episodeOrderingPreference: 'aired',
        watchdogEnabled: true,
        timeoutIdentifyingSeconds: 600,
        timeoutRippingSeconds: 1200,
        timeoutMatchingSeconds: 1800,
        timeoutOrganizingSeconds: 600,
        stagingCleanupPolicy: 'on_success',
        stagingCleanupDays: 7,
        extrasPolicy: 'keep',
        namingSeasonFormat: 'Season {season:02d}',
        namingEpisodeFormat: '{show} - S{season:02d}E{episode:02d}',
        namingMovieFormat: '{title} ({year})',
        namingTvShowFormat: '{show}',
        discdbEnabled: true,
        enableFingerprintContributions: true,
        fingerprintServerUrl: '',
        contributionPseudonym: '',
        fingerprintDisclosureAccepted: false,
        fingerprintDisclosureAcceptedAt: null,
        aiIdentificationEnabled: false,
        aiEpisodeMatchingEnabled: false,
        aiProvider: 'anthropic',
        aiApiKey: '',
        discdbContributionsEnabled: false,
        discdbContributionTier: 2,
        discdbExportPath: '',
        discdbApiKey: '',
        discdbApiUrl: 'https://thediscdb.com',
        opensubtitlesApiKey: '',
        opensubtitlesUsername: '',
        opensubtitlesPassword: '',
        allowLanAccess: false,
        importWatchPath: '',
        importDestinationMode: 'library',
    });
    const [networkInfo, setNetworkInfo] = useState<NetworkInfo | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [toolDetection, setToolDetection] = useState<DetectToolsResponse | null>(null);
    const [isDetecting, setIsDetecting] = useState(false);
    const [showMakemkvOverride, setShowMakemkvOverride] = useState(false);
    const [showFfmpegOverride, setShowFfmpegOverride] = useState(false);
    const [savedKeys, setSavedKeys] = useState<{makemkv: boolean, tmdb: boolean, opensubtitles: boolean, ai: boolean}>({makemkv: false, tmdb: false, opensubtitles: false, ai: false});
    // 'error' ("couldn't check") is deliberately distinct from 'invalid' ("token
    // rejected"): conflating them sent users hunting for a token problem that may
    // not exist (#243). 'error' never counts as validated, but the gate still
    // lets the user continue without TMDB.
    const [tmdbValidation, setTmdbValidation] = useState<{status: 'idle' | 'testing' | 'valid' | 'invalid' | 'error', error?: string}>({status: 'idle'});
    // Inline validation for manually-entered tool paths (MakeMKV/FFmpeg), keyed by
    // the config field. Without this, a hand-typed override was saved blind — no
    // confirmation it actually points at a working binary.
    const [pathValidation, setPathValidation] = useState<
        Partial<Record<keyof ConfigData, {status: 'idle' | 'validating' | 'valid' | 'invalid', version?: string, error?: string}>>
    >({});
    // Monotonic per-field request counter. Each edit/validate bumps it; a response
    // is applied only if its id is still current, so a slow earlier response can't
    // overwrite a newer one (or clobber the user's in-progress edits).
    const pathValidationSeq = useRef<Partial<Record<keyof ConfigData, number>>>({});
    // #243: first-run gate — require a validated TMDB token (or explicit skip) before leaving the TMDB step.
    const [tmdbContinueAnyway, setTmdbContinueAnyway] = useState(false);
    const [tmdbGatePrompted, setTmdbGatePrompted] = useState(false);
    const [showBootstrapFlow, setShowBootstrapFlow] = useState(false);

    const totalSteps = STEP_LABELS.length;

    const fetchNetworkInfo = useCallback(async () => {
        try {
            const res = await fetch('/api/network/info');
            if (res.ok) setNetworkInfo(await res.json());
        } catch {
            // non-fatal; address panel renders nothing if unreachable
        }
    }, []);

    // Fetch network info when the LAN toggle is enabled; clear stale data when disabled.
    useEffect(() => {
        if (config.allowLanAccess) {
            fetchNetworkInfo();
        } else {
            setNetworkInfo(null);
        }
    }, [config.allowLanAccess, fetchNetworkInfo]);

    // Load existing config on mount
    useEffect(() => {
        const loadConfig = async () => {
            try {
                const response = await fetch('/api/config');
                if (!response.ok) {
                    throw new Error(`Failed to load config: ${response.status}`);
                }
                const data = await response.json();
                // Track which sensitive keys are already saved in the database
                setSavedKeys({
                    makemkv: data.makemkv_key === '***',
                    tmdb: data.tmdb_api_key === '***',
                    opensubtitles: data.opensubtitles_api_key === '***',
                    ai: data.ai_api_key === '***',
                });
                // Note: API keys are redacted as "***" for security
                setConfig({
                    stagingPath: data.staging_path || '',
                    makemkvPath: data.makemkv_path || '',
                    makemkvKey: data.makemkv_key === '***' ? '' : (data.makemkv_key || ''),
                    libraryMoviesPath: data.library_movies_path || '',
                    libraryTvPath: data.library_tv_path || '',
                    tmdbApiKey: data.tmdb_api_key === '***' ? '' : (data.tmdb_api_key || ''),
                    maxConcurrentMatches: data.max_concurrent_matches ?? 2,
                    ffmpegPath: data.ffmpeg_path || '',
                    conflictResolutionDefault: data.conflict_resolution_default || 'ask',
                    episodeOrderingPreference: data.episode_ordering_preference || 'aired',
                    watchdogEnabled: data.watchdog_enabled ?? true,
                    timeoutIdentifyingSeconds: data.timeout_identifying_seconds ?? 600,
                    timeoutRippingSeconds: data.timeout_ripping_seconds ?? 1200,
                    timeoutMatchingSeconds: data.timeout_matching_seconds ?? 1800,
                    timeoutOrganizingSeconds: data.timeout_organizing_seconds ?? 600,
                    stagingCleanupPolicy: data.staging_cleanup_policy || 'on_success',
                    stagingCleanupDays: data.staging_cleanup_days ?? 7,
                    extrasPolicy: data.extras_policy || 'keep',
                    namingSeasonFormat: data.naming_season_format || 'Season {season:02d}',
                    namingEpisodeFormat: data.naming_episode_format || '{show} - S{season:02d}E{episode:02d}',
                    namingMovieFormat: data.naming_movie_format || '{title} ({year})',
                    namingTvShowFormat: data.naming_tv_show_format || '{show}',
                    discdbEnabled: data.discdb_enabled ?? true,
                    enableFingerprintContributions: data.enable_fingerprint_contributions ?? true,
                    fingerprintServerUrl: data.fingerprint_server_url || '',
                    contributionPseudonym: data.contribution_pseudonym || '',
                    fingerprintDisclosureAccepted: data.fingerprint_disclosure_accepted ?? false,
                    fingerprintDisclosureAcceptedAt: data.fingerprint_disclosure_accepted_at || null,
                    aiIdentificationEnabled: data.ai_identification_enabled ?? false,
                    aiEpisodeMatchingEnabled: data.ai_episode_matching_enabled ?? false,
                    aiProvider: data.ai_provider || 'anthropic',
                    aiApiKey: data.ai_api_key === '***' ? '' : (data.ai_api_key || ''),
                    discdbContributionsEnabled: data.discdb_contributions_enabled ?? false,
                    discdbContributionTier: data.discdb_contribution_tier ?? 2,
                    discdbExportPath: data.discdb_export_path || '',
                    discdbApiKey: '',  // Never populated from API (sensitive)
                    discdbApiUrl: data.discdb_api_url || 'https://thediscdb.com',
                    opensubtitlesApiKey: data.opensubtitles_api_key === '***' ? '' : (data.opensubtitles_api_key || ''),
                    opensubtitlesUsername: data.opensubtitles_username || '',
                    opensubtitlesPassword: data.opensubtitles_password === '***' ? '' : (data.opensubtitles_password || ''),
                    allowLanAccess: data.allow_lan_access ?? false,
                    importWatchPath: data.import_watch_path || '',
                    importDestinationMode: data.import_destination_mode || 'library',
                });
            } catch (error) {
                console.error('Failed to load config:', error);
            } finally {
                setIsLoading(false);
            }
        };
        loadConfig();
    }, []);

    // Detect tools when entering step 2
    useEffect(() => {
        if (step === 2 && !toolDetection) {
            detectTools();
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [step]);

    // Deep-link (M2): once the settings body has rendered, scroll a requested
    // sub-section control (e.g. the GPU toggle) into view and briefly flash it.
    // Runs once — the initial step is already set to the right section above.
    useEffect(() => {
        if (isLoading) return;
        const anchorId = pendingScrollAnchor.current;
        if (!anchorId) return;
        pendingScrollAnchor.current = null;
        const el = document.getElementById(anchorId);
        if (!el) return;
        el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        el.classList.add('settings-anchor-flash');
        const timer = window.setTimeout(() => el.classList.remove('settings-anchor-flash'), 1800);
        return () => window.clearTimeout(timer);
    }, [isLoading]);

    // #243: once the first-run gate has been shown, advance as soon as the user
    // satisfies it — the token validates in the background, or they opt to continue
    // without one — so they don't have to click Next a second time. Keeping the
    // advance here (not in the button) routes every transition through one place.
    useEffect(() => {
        if (
            isOnboarding &&
            step === 3 &&
            tmdbGatePrompted &&
            (tmdbValidation.status === 'valid' || tmdbContinueAnyway)
        ) {
            setTmdbGatePrompted(false);
            setStep((s) => s + 1);
        }
    }, [isOnboarding, step, tmdbGatePrompted, tmdbValidation.status, tmdbContinueAnyway]);

    const detectTools = async () => {
        setIsDetecting(true);
        try {
            const response = await fetch('/api/detect-tools');
            if (!response.ok) {
                throw new Error(`Detection failed: ${response.status}`);
            }
            const data: DetectToolsResponse = await response.json();
            setToolDetection(data);

            // Update config paths from detection if currently empty
            if (data.makemkv.found && data.makemkv.path && !config.makemkvPath) {
                setConfig(prev => ({ ...prev, makemkvPath: data.makemkv.path! }));
            }
            if (data.ffmpeg.found && data.ffmpeg.path && !config.ffmpegPath) {
                setConfig(prev => ({ ...prev, ffmpegPath: data.ffmpeg.path! }));
            }
        } catch (error) {
            console.error('Tool detection failed:', error);
        } finally {
            setIsDetecting(false);
        }
    };

    const handleInputChange = (field: keyof ConfigData, value: string | boolean | number) => {
        setConfig(prev => ({ ...prev, [field]: value }));
    };

    const handleNext = () => {
        // #243: don't let first-run users sail past the TMDB step with an unvalidated
        // (or missing) token — it's the single most impactful silent misconfiguration.
        if (isOnboarding && step === 3) {
            const tmdbReady = savedKeys.tmdb || tmdbValidation.status === 'valid';
            if (!tmdbReady && !tmdbContinueAnyway) {
                if (config.tmdbApiKey.trim() && tmdbValidation.status === 'idle') {
                    handleTestTmdb();
                }
                setTmdbGatePrompted(true);
                return;
            }
        }
        if (step < totalSteps) {
            setStep(step + 1);
        } else {
            handleSave();
        }
    };

    const handleBack = () => {
        if (step > 1) {
            setStep(step - 1);
        }
    };

    const handleSave = async () => {
        setIsSaving(true);
        // Only include a secret key in the payload when the user actually entered one,
        // so blank fields don't overwrite stored credentials.
        const optional = (key: string, value: string) => (value ? { [key]: value } : {});
        try {
            const response = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    staging_path: config.stagingPath,
                    makemkv_path: config.makemkvPath,
                    makemkv_key: config.makemkvKey,
                    library_movies_path: config.libraryMoviesPath,
                    library_tv_path: config.libraryTvPath,
                    tmdb_api_key: config.tmdbApiKey,
                    max_concurrent_matches: config.maxConcurrentMatches,
                    ffmpeg_path: config.ffmpegPath,
                    conflict_resolution_default: config.conflictResolutionDefault,
                    episode_ordering_preference: config.episodeOrderingPreference,
                    watchdog_enabled: config.watchdogEnabled,
                    timeout_identifying_seconds: config.timeoutIdentifyingSeconds,
                    timeout_ripping_seconds: config.timeoutRippingSeconds,
                    timeout_matching_seconds: config.timeoutMatchingSeconds,
                    timeout_organizing_seconds: config.timeoutOrganizingSeconds,
                    staging_cleanup_policy: config.stagingCleanupPolicy,
                    staging_cleanup_days: config.stagingCleanupDays,
                    extras_policy: config.extrasPolicy,
                    naming_season_format: config.namingSeasonFormat,
                    naming_episode_format: config.namingEpisodeFormat,
                    naming_movie_format: config.namingMovieFormat,
                    naming_tv_show_format: config.namingTvShowFormat,
                    discdb_enabled: config.discdbEnabled,
                    enable_fingerprint_contributions: config.enableFingerprintContributions,
                    fingerprint_server_url: config.fingerprintServerUrl || null,
                    ai_identification_enabled: config.aiIdentificationEnabled,
                    ai_episode_matching_enabled: config.aiEpisodeMatchingEnabled,
                    ai_provider: config.aiProvider,
                    ...optional('ai_api_key', config.aiApiKey),
                    discdb_contributions_enabled: config.discdbContributionsEnabled,
                    discdb_contribution_tier: config.discdbContributionTier,
                    discdb_export_path: config.discdbExportPath,
                    ...optional('discdb_api_key', config.discdbApiKey),
                    discdb_api_url: config.discdbApiUrl,
                    ...optional('opensubtitles_api_key', config.opensubtitlesApiKey),
                    opensubtitles_username: config.opensubtitlesUsername,
                    ...optional('opensubtitles_password', config.opensubtitlesPassword),
                    allow_lan_access: config.allowLanAccess,
                    import_watch_path: config.importWatchPath || null,
                    import_destination_mode: config.importDestinationMode,
                    setup_complete: true,
                }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to save config: ${response.status} ${errorText}`);
            }

            await response.json();
            onComplete();
        } catch (error) {
            console.error('Failed to save config:', error);
            toast.error(`Failed to save configuration: ${error instanceof Error ? error.message : 'Unknown error'}`);
        } finally {
            setIsSaving(false);
        }
    };

    const handleTestTmdb = async () => {
        const key = config.tmdbApiKey.trim();
        if (!key) {
            setTmdbValidation({status: 'invalid', error: 'Please enter a token first'});
            return;
        }
        setTmdbValidation({status: 'testing'});
        // requestTmdbValidation tells "token rejected" apart from "couldn't reach
        // the endpoint" and console.errors the underlying cause for either failure
        // (#243). The returned discriminated union maps straight onto our state.
        setTmdbValidation(await requestTmdbValidation(key));
    };

    // Validate a manually-entered tool path against the backend, which actually
    // runs the binary. Picks the endpoint by tool; an empty path resets to idle.
    const validateToolPath = async (
        toolName: string,
        configField: keyof ConfigData,
        rawPath: string,
    ) => {
        const path = rawPath.trim();
        if (!path) {
            setPathValidation(prev => ({ ...prev, [configField]: { status: 'idle' } }));
            return;
        }
        const endpoint = toolName === 'MakeMKV' ? '/api/validate/makemkv' : '/api/validate/ffmpeg';
        const requestId = (pathValidationSeq.current[configField] ?? 0) + 1;
        pathValidationSeq.current[configField] = requestId;
        const isStale = () => pathValidationSeq.current[configField] !== requestId;
        setPathValidation(prev => ({ ...prev, [configField]: { status: 'validating' } }));
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path }),
            });
            if (isStale()) return; // a newer request for this field superseded us
            const result = await response.json();
            if (result.valid) {
                setPathValidation(prev => ({
                    ...prev,
                    [configField]: { status: 'valid', version: result.version },
                }));
            } else {
                setPathValidation(prev => ({
                    ...prev,
                    [configField]: { status: 'invalid', error: result.error || 'Validation failed' },
                }));
            }
        } catch {
            if (isStale()) return;
            setPathValidation(prev => ({
                ...prev,
                [configField]: { status: 'invalid', error: 'Failed to reach validation endpoint' },
            }));
        }
    };

    const renderToolStatus = (
        tool: ToolDetectionResult | undefined,
        toolName: string,
        installHint: string,
        downloadUrl: string | null,
        showOverride: boolean,
        setShowOverride: (v: boolean) => void,
        configField: keyof ConfigData,
    ) => {
        if (isDetecting || !tool) {
            return (
                <div className="tool-status-card tool-detecting">
                    <div className="tool-status-header">
                        <div className="spinner-mini"></div>
                        <span className="tool-name">{toolName}</span>
                    </div>
                    <span className="tool-status-text">Detecting...</span>
                </div>
            );
        }

        if (tool.found) {
            return (
                <div className="tool-status-card tool-found">
                    <div className="tool-status-header">
                        <span className="tool-status-icon found">OK</span>
                        <span className="tool-name">{toolName}</span>
                        <span className="tool-version" title={tool.version ?? undefined}>
                            {formatToolVersion(tool.version)}
                        </span>
                    </div>
                    <span className="tool-path">{tool.path}</span>
                </div>
            );
        }

        return (
            <div className="tool-status-card tool-not-found">
                <div className="tool-status-header">
                    <span className="tool-status-icon not-found">!!</span>
                    <span className="tool-name">{toolName} not found</span>
                </div>
                <span className="tool-status-text">
                    {toolName === 'MakeMKV'
                        ? 'Required for disc ripping.'
                        : 'Required for audio-based episode matching.'}
                    {downloadUrl && (
                        <>
                            {' '}Download from{' '}
                            <a href={downloadUrl} target="_blank" rel="noopener noreferrer">
                                {downloadUrl.replace('https://', '')}
                            </a>
                        </>
                    )}
                </span>
                <span className="tool-install-hint">
                    Install: <code>{installHint}</code>
                </span>
                <button
                    type="button"
                    className="tool-override-toggle"
                    onClick={() => setShowOverride(!showOverride)}
                >
                    {showOverride ? 'Hide manual override' : 'Override path manually'}
                </button>
                {showOverride && (
                    <div className="tool-override-input">
                        <input
                            type="text"
                            value={config[configField] as string}
                            onChange={(e) => {
                                handleInputChange(configField, e.target.value);
                                // Editing invalidates the previous result and any in-flight
                                // validation; re-check on blur.
                                pathValidationSeq.current[configField] =
                                    (pathValidationSeq.current[configField] ?? 0) + 1;
                                setPathValidation(prev => ({ ...prev, [configField]: { status: 'idle' } }));
                            }}
                            onBlur={(e) => validateToolPath(toolName, configField, e.target.value)}
                            placeholder={
                                toolName === 'FFmpeg'
                                    ? (toolDetection?.platform === 'win32'
                                        ? 'C:\\Users\\You\\ffmpeg\\bin\\ffmpeg.exe'
                                        : '/usr/local/bin/ffmpeg')
                                    : `Path to ${toolName.toLowerCase()} executable`
                            }
                        />
                        <span className="form-hint">
                            Point to the {toolName} executable file itself, not the folder it lives in.
                        </span>
                        {pathValidation[configField]?.status === 'validating' && (
                            <span style={{ fontSize: '0.85rem' }}>Checking…</span>
                        )}
                        {pathValidation[configField]?.status === 'valid' && (
                            <span style={{ color: '#22c55e', fontSize: '0.85rem' }}>
                                ✓ {pathValidation[configField]?.version || 'Valid'}
                            </span>
                        )}
                        {pathValidation[configField]?.status === 'invalid' && (
                            <span style={{ color: '#ef4444', fontSize: '0.85rem' }}>
                                ✗ {pathValidation[configField]?.error}
                            </span>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const renderStepContent = () => {
        switch (step) {
            case 1:
                return (
                    <div className="wizard-step">
                        <h3 className="step-title">Library Paths</h3>
                        <p className="step-description">
                            Where should Engram save your ripped media?
                        </p>

                        <div className="form-group">
                            <label htmlFor="stagingPath">Staging Directory</label>
                            <input
                                id="stagingPath"
                                type="text"
                                value={config.stagingPath}
                                onChange={(e) => handleInputChange('stagingPath', e.target.value)}
                                placeholder="e.g., C:\Temp\Engram-Staging or ~/.engram/staging"
                            />
                            <span className="form-hint">
                                Temporary storage during ripping. Files are moved to library after processing.
                                Ensure this directory has adequate disk space (10-50GB recommended).
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="moviesPath">Movies Library</label>
                            <input
                                id="moviesPath"
                                type="text"
                                value={config.libraryMoviesPath}
                                onChange={(e) => handleInputChange('libraryMoviesPath', e.target.value)}
                                placeholder="e.g., D:\Media\Movies"
                            />
                        </div>

                        <div className="form-group">
                            <label htmlFor="tvPath">TV Shows Library</label>
                            <input
                                id="tvPath"
                                type="text"
                                value={config.libraryTvPath}
                                onChange={(e) => handleInputChange('libraryTvPath', e.target.value)}
                                placeholder="e.g., D:\Media\TV Shows"
                            />
                        </div>

                        <div className="form-group" style={{ marginTop: '1.5rem' }}>
                            <label htmlFor="importWatchPath">Import Watch Folder</label>
                            <div className="form-hint" style={{ display: 'block', marginBottom: '0.5rem' }}>
                                Automatically import MKV files ripped by AutomaticRippingMachine or copied in
                                manually. Point it at the folder that holds your rips. Supported layouts:
                                <ul style={{ margin: '0.4rem 0 0.4rem', paddingLeft: '1.1rem' }}>
                                    <li><code>Show Name / Season 01 / episode.mkv</code> — recommended, best episode matching</li>
                                    <li><code>Show Name / episode.mkv</code> — flat; episodes are matched across all seasons of the show</li>
                                    <li><code>DISC_LABEL / title_t00.mkv</code> — per-disc, ARM-style</li>
                                </ul>
                                Season folders may be written <code>Season 1</code> or <code>Season 01</code>.
                                Without a season folder, matching searches every season, which is slower.
                            </div>
                            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                <input
                                    id="importWatchPath"
                                    type="text"
                                    value={config.importWatchPath}
                                    onChange={(e) => handleInputChange('importWatchPath', e.target.value)}
                                    placeholder="Not configured (e.g., D:\ARM-Output or /mnt/arm)"
                                    style={{ flex: 1 }}
                                />
                                {config.importWatchPath && (
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => handleInputChange('importWatchPath', '')}
                                        style={{ whiteSpace: 'nowrap', padding: '0.375rem 0.75rem', fontSize: '0.85rem' }}
                                    >
                                        Clear
                                    </button>
                                )}
                            </div>
                            {config.importWatchPath && (
                                <div style={{ marginTop: '0.75rem' }}>
                                    <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>Destination</label>
                                    <div style={{ display: 'flex', gap: 0, flexWrap: 'wrap' }}>
                                        {(['library', 'in_place'] as const).map(mode => (
                                            <button
                                                key={mode}
                                                type="button"
                                                onClick={() => handleInputChange('importDestinationMode', mode)}
                                                style={{
                                                    padding: '0.375rem 0.875rem',
                                                    fontSize: '0.85rem',
                                                    cursor: 'pointer',
                                                    background: config.importDestinationMode === mode ? 'var(--color-sv-cyan)' : 'transparent',
                                                    color: config.importDestinationMode === mode ? 'var(--color-sv-bg0)' : 'inherit',
                                                    border: '1px solid var(--color-sv-line-mid)',
                                                    fontWeight: config.importDestinationMode === mode ? 700 : 400,
                                                    marginRight: mode === 'library' ? -1 : 0,
                                                }}
                                            >
                                                {mode === 'library' ? 'Organize into library' : 'Organize in place'}
                                            </button>
                                        ))}
                                    </div>
                                    <span className="form-hint">
                                        {config.importDestinationMode === 'library'
                                            ? 'Files are moved into your configured TV and movie library paths.'
                                            : 'Files are organized within the watch folder itself (TV/ and Movies/ subdirectories).'}
                                    </span>
                                </div>
                            )}
                        </div>
                    </div>
                );

            case 2: {
                const isWindows = toolDetection?.platform === 'win32';
                const makemkvInstallHint = isWindows
                    ? 'Download installer from makemkv.com'
                    : 'sudo apt install makemkv-bin makemkv-oss';
                const ffmpegInstallHint = isWindows
                    ? 'winget install Gyan.FFmpeg'
                    : 'sudo apt install ffmpeg';

                return (
                    <div className="wizard-step">
                        <h3 className="step-title">Tools & License</h3>
                        <p className="step-description">
                            Engram auto-detects required tools on your system.
                        </p>

                        <div className="tool-detection-section">
                            {renderToolStatus(
                                toolDetection?.makemkv,
                                'MakeMKV',
                                makemkvInstallHint,
                                'https://makemkv.com',
                                showMakemkvOverride,
                                setShowMakemkvOverride,
                                'makemkvPath',
                            )}

                            {renderToolStatus(
                                toolDetection?.ffmpeg,
                                'FFmpeg',
                                ffmpegInstallHint,
                                'https://ffmpeg.org/download.html',
                                showFfmpegOverride,
                                setShowFfmpegOverride,
                                'ffmpegPath',
                            )}

                            {toolDetection && (
                                <button
                                    type="button"
                                    className="tool-rescan-btn"
                                    onClick={() => { setToolDetection(null); detectTools(); }}
                                    disabled={isDetecting}
                                >
                                    Re-scan
                                </button>
                            )}
                        </div>

                        <div className="form-group" style={{ marginTop: '1.5rem' }}>
                            <label htmlFor="licenseKey">
                                MakeMKV License Key
                                <SavedKeyBadge saved={savedKeys.makemkv} text="Key saved" />
                            </label>
                            <input
                                id="licenseKey"
                                type="text"
                                value={config.makemkvKey}
                                onChange={(e) => handleInputChange('makemkvKey', e.target.value)}
                                placeholder={savedKeys.makemkv ? "Enter new key to replace existing" : "T-xxxxx-xxxxx-xxxxx-xxxxx"}
                            />
                            <span className="form-hint">
                                Found in MakeMKV under Help &rarr; Register. Leave blank to use the beta key (requires periodic updates).
                            </span>
                        </div>
                    </div>
                );
            }

            case 3:
                return (
                    <div className="wizard-step">
                        <h3 className="step-title">TMDB Read Access Token</h3>
                        <p className="step-description">
                            Required for TV show metadata and episode information.
                            Go to{' '}
                            <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer">
                                TMDB API Settings
                            </a>
                            {' '}and copy the <strong>Read Access Token</strong> (v4 auth),
                            not the shorter "API Key" (v3 auth).
                        </p>

                        <div className="form-group">
                            <label htmlFor="tmdbApiKey">
                                TMDB Read Access Token
                                <SavedKeyBadge saved={savedKeys.tmdb} text="Token saved" />
                            </label>
                            <input
                                id="tmdbApiKey"
                                type="text"
                                value={config.tmdbApiKey}
                                onChange={(e) => {
                                    handleInputChange('tmdbApiKey', e.target.value);
                                    setTmdbValidation({status: 'idle'});
                                }}
                                onBlur={() => {
                                    // #243: auto-validate on blur so users get inline ✓/✗ without clicking.
                                    if (config.tmdbApiKey.trim() && tmdbValidation.status === 'idle') {
                                        handleTestTmdb();
                                    }
                                }}
                                placeholder={savedKeys.tmdb ? "Enter new token to replace existing" : "Paste your Read Access Token here (long string starting with eyJ…)"}
                            />
                            <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem'}}>
                                <button
                                    type="button"
                                    onClick={handleTestTmdb}
                                    disabled={tmdbValidation.status === 'testing' || (!config.tmdbApiKey && !savedKeys.tmdb)}
                                    className="btn-secondary"
                                    style={{padding: '0.25rem 0.75rem', fontSize: '0.85rem'}}
                                >
                                    {tmdbValidation.status === 'testing' ? 'Testing...' : 'Test Token'}
                                </button>
                                {tmdbValidation.status === 'valid' && (
                                    <span style={{color: '#22c55e', fontSize: '0.85rem'}}>✓ Valid token</span>
                                )}
                                {tmdbValidation.status === 'invalid' && (
                                    <span style={{color: '#ef4444', fontSize: '0.85rem'}}>✗ {tmdbValidation.error}</span>
                                )}
                                {/* "Couldn't check" — amber, not red: the token wasn't rejected,
                                    the check itself failed. Distinct from 'invalid' (#243). */}
                                {tmdbValidation.status === 'error' && (
                                    <span style={{color: '#f59e0b', fontSize: '0.85rem'}}>⚠ {tmdbValidation.error}</span>
                                )}
                            </div>
                            <span className="form-hint">
                                The Read Access Token is a long JWT string starting with "eyJ...".
                                Find it under API &rarr; Read Access Token in your TMDB account settings.
                            </span>
                        </div>

                        {isOnboarding && tmdbGatePrompted &&
                            !(savedKeys.tmdb || tmdbValidation.status === 'valid') && !tmdbContinueAnyway && (
                            <div className="tmdb-gate-warning">
                                <strong>TMDB token not validated</strong>
                                <span>
                                    Without a working token Engram can&apos;t reliably identify shows or movies —
                                    classification falls back to heuristics only. Enter a token above (it validates
                                    automatically), or continue without it.
                                </span>
                                <SvActionButton
                                    tone="amber"
                                    size="md"
                                    style={{ alignSelf: 'flex-start' }}
                                    onClick={() => setTmdbContinueAnyway(true)}
                                >
                                    Continue without TMDB
                                </SvActionButton>
                            </div>
                        )}

                        <h4 style={{marginTop: '1.5rem', marginBottom: '0.25rem', fontSize: '1rem', fontWeight: 600}}>
                            OpenSubtitles.com <span style={{fontWeight: 400, fontSize: '0.85rem', opacity: 0.7}}>(Optional)</span>
                        </h4>
                        <p className="step-description" style={{marginTop: 0}}>
                            Enables automatic subtitle downloads for episode matching.{' '}
                            Register a free account at{' '}
                            <a href="https://www.opensubtitles.com" target="_blank" rel="noopener noreferrer">opensubtitles.com</a>,
                            then create a consumer at{' '}
                            <a href="https://www.opensubtitles.com/consumers" target="_blank" rel="noopener noreferrer">opensubtitles.com/consumers</a>{' '}
                            to get an API key.
                        </p>

                        <div className="form-group">
                            <label htmlFor="osApiKey">
                                API Key
                                <SavedKeyBadge saved={savedKeys.opensubtitles} text="Key saved" />
                            </label>
                            <input
                                id="osApiKey"
                                type="password"
                                value={config.opensubtitlesApiKey}
                                onChange={(e) => handleInputChange('opensubtitlesApiKey', e.target.value)}
                                placeholder={savedKeys.opensubtitles ? 'Enter new key to replace existing' : 'API key from opensubtitles.com/consumers'}
                            />
                        </div>

                        <div className="form-group">
                            <label htmlFor="osUsername">Username</label>
                            <input
                                id="osUsername"
                                type="text"
                                value={config.opensubtitlesUsername}
                                onChange={(e) => handleInputChange('opensubtitlesUsername', e.target.value)}
                                placeholder="Your opensubtitles.com username"
                            />
                        </div>

                        <div className="form-group">
                            <label htmlFor="osPassword">Password</label>
                            <input
                                id="osPassword"
                                type="password"
                                value={config.opensubtitlesPassword}
                                onChange={(e) => handleInputChange('opensubtitlesPassword', e.target.value)}
                                placeholder={savedKeys.opensubtitles ? 'Enter new password to replace existing' : 'Your opensubtitles.com password'}
                            />
                            <span className="form-hint">
                                Free accounts get 5 subtitle downloads/day. Used only for TV episode matching; skipped if not configured.
                            </span>
                        </div>
                    </div>
                );

            case 4: {
                const providerLabel = AI_PROVIDER_LABELS[config.aiProvider] || config.aiProvider;
                return (
                    <div className="wizard-step">
                        <h3 className="step-title">Data Sharing</h3>
                        <p className="step-description">
                            Everything here is optional and governs data that leaves your machine. Nothing is on by
                            default except local fingerprint extraction — and no filenames, paths, or personal
                            information are ever sent.
                        </p>

                        {/* ── Fingerprint network ─────────────────────────────── */}
                        <details className="wizard-group" open>
                            <summary>
                                <span className="wizard-group-chevron">▸</span>Fingerprint network
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.enableFingerprintContributions}
                                    onChange={(e) => handleInputChange('enableFingerprintContributions', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Contribute audio fingerprints</strong>
                                    <span className="checkbox-hint">
                                        Engram extracts a perceptual audio fingerprint from each ripped title and shares
                                        it with the community catalog so everyone&apos;s rips identify faster. Only the
                                        fingerprint is sent — never audio, filenames, or paths. Untick to keep
                                        fingerprints entirely on this machine.
                                    </span>
                                </span>
                            </label>
                        </div>

                        {config.enableFingerprintContributions && (
                            <>
                                <div className="form-group">
                                    <label htmlFor="fingerprintServerUrl">Fingerprint network server URL</label>
                                    <input
                                        id="fingerprintServerUrl"
                                        type="text"
                                        placeholder="https://api.engramfp.com"
                                        value={config.fingerprintServerUrl}
                                        onChange={(e) => handleInputChange('fingerprintServerUrl', e.target.value)}
                                    />
                                    <span className="form-hint">
                                        Base URL of the fingerprint network (no trailing path). Leave blank to use the
                                        default network. To stop contributing entirely, untick the toggle above.
                                    </span>
                                </div>

                                <div className="id-panel">
                                    <div className="id-panel-row">
                                        <div>
                                            <div className="id-panel-label">Your anonymous contribution ID</div>
                                            <div className="id-panel-value">
                                                {config.contributionPseudonym || '(generated on first run)'}
                                            </div>
                                        </div>
                                        {config.fingerprintDisclosureAccepted ? (
                                            <span className="contrib-badge contrib-badge--green">
                                                <span className="dot" />
                                                Disclosure accepted
                                            </span>
                                        ) : (
                                            <span className="contrib-badge contrib-badge--amber">
                                                <span className="dot" />
                                                Disclosure not accepted
                                            </span>
                                        )}
                                    </div>
                                    {config.fingerprintDisclosureAccepted && config.fingerprintDisclosureAcceptedAt && (
                                        <span className="form-hint">
                                            Accepted on {new Date(config.fingerprintDisclosureAcceptedAt).toLocaleString()}.
                                        </span>
                                    )}
                                    <div className="id-panel-actions">
                                        <SvActionButton
                                            tone="red"
                                            size="md"
                                            onClick={async () => {
                                                if (!window.confirm(
                                                    'This deletes your raw contributions on the fingerprint server, clears the local queue, and generates a new anonymous ID. Already-promoted consensus data cannot be recalled. Continue?'
                                                )) return;
                                                try {
                                                    const r = await fetch('/api/fingerprint/forget', { method: 'POST' });
                                                    if (!r.ok) {
                                                        const detail = await r.text();
                                                        window.alert(`Forget failed: ${r.status} ${detail}`);
                                                        return;
                                                    }
                                                    const d = await r.json();
                                                    window.alert(
                                                        `Done. Server deleted ${d.server_rows_deleted} row(s), local queue cleared (${d.local_rows_deleted}). New anonymous ID: ${d.new_pseudonym}`
                                                    );
                                                    setConfig((prev) => ({
                                                        ...prev,
                                                        contributionPseudonym: d.new_pseudonym,
                                                        fingerprintDisclosureAccepted: false,
                                                        fingerprintDisclosureAcceptedAt: null,
                                                    }));
                                                } catch (err) {
                                                    window.alert(`Forget failed: ${err}`);
                                                }
                                            }}
                                        >
                                            Forget me on the server
                                        </SvActionButton>
                                    </div>
                                    <span className="form-hint">
                                        Forgetting deletes your raw contributions on the server, clears the local queue,
                                        and rotates your ID. Already-promoted consensus data can&apos;t be recalled.
                                    </span>
                                </div>
                            </>
                        )}

                        {config.enableFingerprintContributions && (
                            <div className="form-group" style={{ marginTop: '0.75rem' }}>
                                <SvActionButton
                                    tone="cyan"
                                    size="md"
                                    onClick={() => setShowBootstrapFlow(true)}
                                >
                                    Contribute from existing library&hellip;
                                </SvActionButton>
                                <span className="form-hint" style={{ display: 'block', marginTop: '0.4rem' }}>
                                    Seed the fingerprint network from your existing TV library (reads filenames only — no files are uploaded).
                                </span>
                            </div>
                        )}

                            </div>
                        </details>

                        {/* ── AI assistance ───────────────────────────────────── */}
                        <details className="wizard-group">
                            <summary>
                                <span className="wizard-group-chevron">▸</span>AI assistance
                                <span className="wizard-group-sub">optional · API key required</span>
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.aiIdentificationEnabled}
                                    onChange={(e) => handleInputChange('aiIdentificationEnabled', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>AI-Powered Title Resolution</strong>
                                    <span className="checkbox-hint">
                                        When TMDB lookup fails (obscure titles, abbreviations), use an LLM to identify the disc. Requires an API key below.
                                    </span>
                                </span>
                            </label>
                        </div>

                        {config.aiIdentificationEnabled && (
                            <>
                                <div className="wizard-grid">
                                <div className="form-group">
                                    <label htmlFor="aiProvider">AI Provider</label>
                                    <EngramSelect
                                        id="aiProvider"
                                        value={config.aiProvider}
                                        onValueChange={(v) => handleInputChange('aiProvider', v)}
                                        options={[
                                            { value: 'anthropic', label: 'Anthropic (Claude)' },
                                            { value: 'openai', label: 'OpenAI' },
                                            { value: 'openrouter', label: 'OpenRouter' },
                                            { value: 'gemini', label: 'Google Gemini' },
                                        ]}
                                    />
                                </div>
                                <div className="form-group">
                                    <label htmlFor="aiApiKey">
                                        {providerLabel} API Key
                                        <SavedKeyBadge saved={savedKeys.ai} text="Key saved" />
                                    </label>
                                    <input
                                        id="aiApiKey"
                                        type="password"
                                        placeholder={savedKeys.ai ? 'Enter new key to replace existing' : (AI_KEY_PLACEHOLDERS[config.aiProvider] || '')}
                                        value={config.aiApiKey}
                                        onChange={(e) => handleInputChange('aiApiKey', e.target.value)}
                                    />
                                    <span className="form-hint">
                                        API key for {providerLabel}. Used only when TMDB lookup fails.
                                    </span>
                                </div>
                                </div>
                                <div className="form-group checkbox-group">
                                    <label className="checkbox-label">
                                        <input
                                            type="checkbox"
                                            checked={config.aiEpisodeMatchingEnabled}
                                            onChange={(e) => handleInputChange('aiEpisodeMatchingEnabled', e.target.checked)}
                                        />
                                        <span className="checkbox-text">
                                            <strong>AI-Powered Episode Matching (TV)</strong>
                                            <span className="checkbox-hint">
                                                When audio fingerprint matching can't identify a TV episode, send the cleaned transcript and TMDB synopses to your AI provider for a suggested episode. Always confirmed via the review queue — never auto-organizes. <em>Gemini Flash-Lite recommended for best accuracy on this task.</em>
                                            </span>
                                        </span>
                                    </label>
                                </div>
                            </>
                        )}

                            </div>
                        </details>

                        {/* ── TheDiscDB (feature-flagged) ──────────────────────── */}
                        {FEATURES.DISCDB && (
                            <details className="wizard-group">
                                <summary>
                                    <span className="wizard-group-chevron">▸</span>TheDiscDB
                                </summary>
                                <div className="wizard-group-body">
                                <div className="form-group checkbox-group">
                                    <label className="checkbox-label">
                                        <input
                                            type="checkbox"
                                            checked={config.discdbEnabled}
                                            onChange={(e) => handleInputChange('discdbEnabled', e.target.checked)}
                                        />
                                        <span className="checkbox-text">
                                            <strong>Enable TheDiscDB Lookup</strong>
                                            <span className="checkbox-hint">
                                                Query TheDiscDB for known disc layouts. When matched, skips audio fingerprinting and instantly maps episodes. No API key required.
                                            </span>
                                        </span>
                                    </label>
                                </div>
                                <div className="form-group checkbox-group">
                                    <label className="checkbox-label">
                                        <input
                                            type="checkbox"
                                            checked={config.discdbContributionsEnabled}
                                            onChange={(e) => handleInputChange('discdbContributionsEnabled', e.target.checked)}
                                        />
                                        <span className="checkbox-text">
                                            <strong>Enable TheDiscDB Contributions</strong>
                                            <span className="checkbox-hint">
                                                Share disc metadata (track info, episode mappings) with TheDiscDB after each rip. Helps others identify their discs automatically. No personal data is shared.
                                            </span>
                                        </span>
                                    </label>
                                </div>

                                {config.discdbContributionsEnabled && (
                                    <>
                                        <div className="form-group">
                                            <label htmlFor="discdbContributionTier">Contribution Level</label>
                                            <EngramSelect
                                                id="discdbContributionTier"
                                                value={String(config.discdbContributionTier)}
                                                onValueChange={(v) => handleInputChange('discdbContributionTier', parseInt(v, 10))}
                                                options={[
                                                    { value: '2', label: 'Automatic — share auto-collected data' },
                                                    { value: '3', label: 'Full — prompt for UPC and images' },
                                                ]}
                                            />
                                        </div>

                                        <div className="form-group">
                                            <label htmlFor="discdbApiKey">TheDiscDB API Key</label>
                                            <input
                                                id="discdbApiKey"
                                                type="password"
                                                value={config.discdbApiKey}
                                                onChange={(e) => handleInputChange('discdbApiKey', e.target.value)}
                                                placeholder="Enter API key for automatic submission"
                                            />
                                            <small>Required for submitting directly to TheDiscDB. Leave empty for local-only export.</small>
                                        </div>

                                        <div className="form-group">
                                            <label htmlFor="discdbExportPath">Export Directory (optional)</label>
                                            <input
                                                id="discdbExportPath"
                                                type="text"
                                                value={config.discdbExportPath}
                                                onChange={(e) => handleInputChange('discdbExportPath', e.target.value)}
                                                placeholder="~/.engram/discdb-exports"
                                            />
                                            <small>Leave empty for the default location</small>
                                        </div>
                                    </>
                                )}
                                </div>
                            </details>
                        )}

                    </div>
                );
            }

            case 5:
                return (
                    <div className="wizard-step">
                        <h3 className="step-title">Preferences</h3>
                        <p className="step-description">
                            How Engram matches, names, and tidies up — all processed locally on this machine.
                        </p>

                        {/* ── Matching & ordering ─────────────────────────────── */}
                        <details className="wizard-group" open>
                            <summary>
                                <span className="wizard-group-chevron">▸</span>Matching &amp; ordering
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group">
                            <label htmlFor="maxConcurrentMatches">Max Concurrent Matches</label>
                            <input
                                id="maxConcurrentMatches"
                                type="number"
                                min={1}
                                max={8}
                                value={config.maxConcurrentMatches}
                                onChange={(e) => handleInputChange('maxConcurrentMatches', Math.max(1, Math.min(8, parseInt(e.target.value) || 1)))}
                            />
                            <span className="form-hint">
                                Requested number of episodes transcribed in parallel. Automatically
                                clamped to your hardware (CPU cores, or a GPU limit). Takes effect
                                after a backend restart.
                            </span>
                        </div>

                        <div id={GPU_ANCHOR_ID}>
                            <GpuAccelerationSetting />
                        </div>

                        <div className="form-group">
                            <label htmlFor="conflictResolution">Default Conflict Resolution</label>
                            <EngramSelect
                                id="conflictResolution"
                                value={config.conflictResolutionDefault}
                                onValueChange={(v) => handleInputChange('conflictResolutionDefault', v)}
                                options={[
                                    { value: 'ask', label: 'Always ask me' },
                                    { value: 'rename', label: 'Automatically rename (keep both)' },
                                    { value: 'overwrite', label: 'Automatically overwrite' },
                                    { value: 'skip', label: 'Automatically skip' },
                                ]}
                            />
                            <span className="form-hint">
                                What should Engram do when a file already exists in your library?
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="episodeOrdering">Episode Ordering</label>
                            <EngramSelect
                                id="episodeOrdering"
                                value={config.episodeOrderingPreference}
                                onValueChange={(v) => handleInputChange('episodeOrderingPreference', v)}
                                options={[
                                    { value: 'aired', label: 'Aired Order (Default)' },
                                    { value: 'dvd', label: 'DVD Order' },
                                ]}
                            />
                            <span className="form-hint">
                                How TV episodes are numbered in filenames. DVD order uses the disc
                                release's numbering (e.g. Firefly) when a show provides it, falling back
                                to aired order otherwise. This only affects new rips' filenames — matching,
                                history, and the fingerprint network always use canonical aired numbering.
                                Individual shows can be overridden during review.
                            </span>
                        </div>

                            </div>
                        </details>

                        {/* ── Maintenance & watchdog ──────────────────────────── */}
                        <details className="wizard-group">
                            <summary>
                                <span className="wizard-group-chevron">▸</span>Maintenance &amp; watchdog
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group">
                            <label htmlFor="stagingCleanup">Staging Cleanup Policy</label>
                            <EngramSelect
                                id="stagingCleanup"
                                value={config.stagingCleanupPolicy}
                                onValueChange={(v) => handleInputChange('stagingCleanupPolicy', v)}
                                options={[
                                    { value: 'on_success', label: 'Clean on success (delete after organization)' },
                                    { value: 'on_completion', label: 'Clean on completion (delete after success or failure)' },
                                    { value: 'after_days', label: 'Clean after N days' },
                                    { value: 'manual', label: 'Manual only (never auto-delete)' },
                                ]}
                            />
                            <span className="form-hint">
                                When should staging files be automatically deleted? A single Blu-ray rip can be 30-50GB.
                            </span>
                        </div>

                        {config.stagingCleanupPolicy === 'after_days' && (
                            <div className="form-group">
                                <label htmlFor="stagingCleanupDays">Cleanup After (days)</label>
                                <input
                                    id="stagingCleanupDays"
                                    type="number"
                                    min={1}
                                    max={365}
                                    value={config.stagingCleanupDays}
                                    onChange={(e) => handleInputChange('stagingCleanupDays', Math.max(1, parseInt(e.target.value) || 7))}
                                />
                                <span className="form-hint">
                                    Delete staging files older than this many days.
                                </span>
                            </div>
                        )}

                        <div className="form-group">
                            <label htmlFor="watchdogEnabled">Stale-Job Watchdog</label>
                            <EngramSelect
                                id="watchdogEnabled"
                                value={config.watchdogEnabled ? 'on' : 'off'}
                                onValueChange={(v) => handleInputChange('watchdogEnabled', v === 'on')}
                                options={[
                                    { value: 'on', label: 'Enabled (auto-advance stuck jobs)' },
                                    { value: 'off', label: 'Disabled' },
                                ]}
                            />
                            <span className="form-hint">
                                When a job stops making progress for longer than its phase timeout, Engram
                                resolves the stuck tracks (ripped-but-unmatched → review) and moves the job
                                forward instead of leaving it stuck. You can also force this manually per job.
                            </span>
                        </div>

                        {config.watchdogEnabled && (
                            <div className="form-group">
                                <label>Phase Timeouts (seconds of no progress)</label>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
                                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                                        Identifying
                                        <input
                                            type="number"
                                            min={30}
                                            value={config.timeoutIdentifyingSeconds}
                                            onChange={(e) => handleInputChange('timeoutIdentifyingSeconds', Math.max(30, parseInt(e.target.value) || 600))}
                                        />
                                    </label>
                                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                                        Ripping
                                        <input
                                            type="number"
                                            min={60}
                                            value={config.timeoutRippingSeconds}
                                            onChange={(e) => handleInputChange('timeoutRippingSeconds', Math.max(60, parseInt(e.target.value) || 1200))}
                                        />
                                    </label>
                                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                                        Matching
                                        <input
                                            type="number"
                                            min={60}
                                            value={config.timeoutMatchingSeconds}
                                            onChange={(e) => handleInputChange('timeoutMatchingSeconds', Math.max(60, parseInt(e.target.value) || 1800))}
                                        />
                                    </label>
                                    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                                        Organizing
                                        <input
                                            type="number"
                                            min={30}
                                            value={config.timeoutOrganizingSeconds}
                                            onChange={(e) => handleInputChange('timeoutOrganizingSeconds', Math.max(30, parseInt(e.target.value) || 600))}
                                        />
                                    </label>
                                </div>
                                <span className="form-hint">
                                    Ripping leans on MakeMKV&apos;s own no-growth stall watchdog; this is an
                                    additional ceiling on total silence per phase.
                                </span>
                            </div>
                        )}

                            </div>
                        </details>

                        {/* ── Naming & extras ─────────────────────────────────── */}
                        <details className="wizard-group">
                            <summary>
                                <span className="wizard-group-chevron">▸</span>Naming &amp; extras
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group">
                            <label htmlFor="extrasPolicy">Extras Handling</label>
                            <EngramSelect
                                id="extrasPolicy"
                                value={config.extrasPolicy}
                                onValueChange={(v) => handleInputChange('extrasPolicy', v)}
                                options={[
                                    { value: 'keep', label: 'Keep all extras (organize to Extras/ folder)' },
                                    { value: 'skip', label: 'Skip extras (discard after ripping)' },
                                    { value: 'ask', label: 'Ask me (show in Review Queue)' },
                                ]}
                            />
                            <span className="form-hint">
                                How to handle bonus content that doesn&apos;t match any episode runtime.
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="namingConvention">Naming Convention</label>
                            <EngramSelect
                                id="namingConvention"
                                value={
                                    NAMING_PRESETS.find(
                                        (p) =>
                                            p.seasonFormat === config.namingSeasonFormat &&
                                            p.episodeFormat === config.namingEpisodeFormat,
                                    )?.id ?? 'custom'
                                }
                                onValueChange={(v) => {
                                    const preset = NAMING_PRESETS.find((p) => p.id === v);
                                    if (preset) {
                                        handleInputChange('namingSeasonFormat', preset.seasonFormat);
                                        handleInputChange('namingEpisodeFormat', preset.episodeFormat);
                                    }
                                }}
                                options={[
                                    { value: 'plex', label: 'Plex (Season 01 / Show - S01E01)' },
                                    { value: 'kodi', label: 'Kodi (Season 1 / Show - S01E01)' },
                                    { value: 'minimal', label: 'Minimal (S01 / Show - S01E01)' },
                                    { value: 'custom', label: 'Custom' },
                                ]}
                            />
                            <span className="form-hint">
                                Preview: TV/{config.namingSeasonFormat.replace('{season:02d}', '01').replace('{season:d}', '1')}/{config.namingEpisodeFormat.replace('{show}', 'Breaking Bad').replace('{season:02d}', '01').replace('{season:d}', '1').replace('{episode:02d}', '05').replace('{episode:d}', '5')}.mkv
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="namingTvShowFormat">Show Folder Format</label>
                            <input
                                id="namingTvShowFormat"
                                type="text"
                                value={config.namingTvShowFormat}
                                onChange={(e) => handleInputChange('namingTvShowFormat', e.target.value)}
                                placeholder="{show}"
                            />
                            <span className="form-hint">
                                Placeholders: {'{show}'}, {'{year}'}, {'{tmdb_id}'}. Default{' '}
                                {'{show}'} keeps your current folders. To let same-name shows
                                coexist (e.g. Frasier 1993 vs 2023), use Plex{' '}
                                &quot;{'{show} ({year}) {{tmdb-{tmdb_id}}}'}&quot; or Jellyfin{' '}
                                &quot;{'{show} ({year}) [tmdbid-{tmdb_id}]'}&quot;.
                            </span>
                        </div>

                        {!NAMING_PRESETS.some((p) => p.seasonFormat === config.namingSeasonFormat) && (
                            <>
                                <div className="form-group">
                                    <label htmlFor="namingSeasonFormat">Season Folder Format</label>
                                    <input
                                        id="namingSeasonFormat"
                                        type="text"
                                        value={config.namingSeasonFormat}
                                        onChange={(e) => handleInputChange('namingSeasonFormat', e.target.value)}
                                        placeholder="Season {season:02d}"
                                    />
                                    <span className="form-hint">
                                        Placeholders: {'{season}'} — e.g., &quot;Season {'{season:02d}'}&quot; → Season 01
                                    </span>
                                </div>
                                <div className="form-group">
                                    <label htmlFor="namingEpisodeFormat">Episode Filename Format</label>
                                    <input
                                        id="namingEpisodeFormat"
                                        type="text"
                                        value={config.namingEpisodeFormat}
                                        onChange={(e) => handleInputChange('namingEpisodeFormat', e.target.value)}
                                        placeholder="{show} - S{season:02d}E{episode:02d}"
                                    />
                                    <span className="form-hint">
                                        Placeholders: {'{show}'}, {'{season}'}, {'{episode}'}
                                    </span>
                                </div>
                            </>
                        )}

                            </div>
                        </details>

                        {/* ── Network access ──────────────────────────────────── */}
                        <details className="wizard-group">
                            <summary>
                                <span className="wizard-group-chevron">▸</span>Network access
                            </summary>
                            <div className="wizard-group-body">

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.allowLanAccess}
                                    onChange={(e) => handleInputChange('allowLanAccess', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Allow access from other devices on my network (LAN)</strong>
                                    <span className="checkbox-hint">
                                        ⚠ Engram has no login — anyone on your network can view and control it.
                                        Use only on a trusted home network. Takes effect after restarting Engram.
                                    </span>
                                </span>
                            </label>
                        </div>

                        {config.allowLanAccess && (
                            <div className="lan-address-panel">
                                {!networkInfo ? (
                                    <div className="lan-loading">Detecting network address…</div>
                                ) : networkInfo.active_lan_bound ? (
                                    <>
                                        <div className="lan-status-live">
                                            <span className="lan-status-dot" />
                                            Engram is live on your network
                                        </div>
                                        {networkInfo.lan_url && (
                                            <>
                                                <div className="lan-url-row">
                                                    <code className="lan-url">{networkInfo.lan_url}</code>
                                                    <button
                                                        type="button"
                                                        className="lan-copy-btn"
                                                        onClick={() => {
                                                            navigator.clipboard?.writeText(networkInfo.lan_url as string).catch(() => {
                                                                // HTTP context (LAN): clipboard API unavailable; select text for manual copy
                                                                const el = document.querySelector('.lan-url') as HTMLElement;
                                                                if (el) window.getSelection()?.selectAllChildren(el);
                                                            });
                                                        }}
                                                    >
                                                        Copy
                                                    </button>
                                                </div>
                                                <div className="lan-qr">
                                                    <QRCodeSVG value={networkInfo.lan_url} size={120} />
                                                </div>
                                            </>
                                        )}
                                    </>
                                ) : (
                                    <>
                                        <div className="lan-restart-notice">
                                            Save settings and restart Engram to apply.
                                        </div>
                                        {networkInfo.lan_url && (
                                            <>
                                                <span className="form-hint">After restart, access Engram at:</span>
                                                <div className="lan-url-row">
                                                    <code className="lan-url">{networkInfo.lan_url}</code>
                                                    <button
                                                        type="button"
                                                        className="lan-copy-btn"
                                                        onClick={() => {
                                                            navigator.clipboard?.writeText(networkInfo.lan_url as string).catch(() => {
                                                                // HTTP context (LAN): clipboard API unavailable; select text for manual copy
                                                                const el = document.querySelector('.lan-url') as HTMLElement;
                                                                if (el) window.getSelection()?.selectAllChildren(el);
                                                            });
                                                        }}
                                                    >
                                                        Copy
                                                    </button>
                                                </div>
                                                <div className="lan-qr lan-qr-pending">
                                                    <QRCodeSVG value={networkInfo.lan_url} size={120} />
                                                </div>
                                            </>
                                        )}
                                    </>
                                )}
                            </div>
                        )}

                            </div>
                        </details>

                        <div className="config-summary">
                            <h4>Configuration Summary</h4>
                            <dl>
                                <dt>Movies:</dt>
                                <dd>{config.libraryMoviesPath || 'Not set'}</dd>
                                <dt>TV Shows:</dt>
                                <dd>{config.libraryTvPath || 'Not set'}</dd>
                                <dt>MakeMKV Key:</dt>
                                <dd>{config.makemkvKey ? 'New key entered' : (savedKeys.makemkv ? 'Configured' : 'Not set')}</dd>
                                <dt>TMDB Token:</dt>
                                <dd>{config.tmdbApiKey ? 'New token entered' : (savedKeys.tmdb ? 'Configured' : 'Not set')}</dd>
                                {(config.aiApiKey || savedKeys.ai) && (
                                    <>
                                        <dt>AI Key:</dt>
                                        <dd>{config.aiApiKey ? 'New key entered' : 'Configured'}</dd>
                                    </>
                                )}
                            </dl>
                        </div>
                    </div>
                );

            default:
                return null;
        }
    };

    // Shared scrollable content pane — same in both modes, only the surrounding
    // chrome (stepper vs. section nav) differs.
    const wizardBody = (
        <div className="wizard-body">
            {isLoading ? (
                <div className="wizard-loading">
                    <div className="spinner-mini"></div>
                    <span>Loading configuration...</span>
                </div>
            ) : (
                renderStepContent()
            )}
        </div>
    );

    return (
        <>
        <div className="wizard-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="wizard-title">
            <div className="wizard-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2 className="modal-title" id="wizard-title">{isOnboarding ? 'Setup Wizard' : 'Settings'}</h2>
                    <button
                        className="modal-close"
                        onClick={onClose}
                        aria-label={isOnboarding ? 'Close setup wizard' : 'Close settings'}
                    >
                        &times;
                    </button>
                </div>

                {isOnboarding ? (
                    <>
                        {/* Onboarding: linear stepper showing first-run progress. */}
                        <div className="wizard-progress">
                            {Array.from({ length: totalSteps }, (_, i) => i + 1).map((s) => (
                                <div
                                    key={s}
                                    className={`progress-step ${s === step ? 'active' : ''} ${s < step ? 'completed' : ''}`}
                                    aria-label={`Step ${s}: ${STEP_LABELS[s - 1]}${s === step ? ' (current)' : ''}`}
                                    aria-current={s === step ? 'step' : undefined}
                                >
                                    <span className="step-number">{s < step ? '✓' : s}</span>
                                    <span className="step-label">{STEP_LABELS[s - 1]}</span>
                                </div>
                            ))}
                        </div>
                        {wizardBody}
                    </>
                ) : (
                    /* Settings: jump-anywhere section list — no linear progression. */
                    <div className="settings-main">
                        <nav className="settings-nav" aria-label="Settings sections">
                            {SETTINGS_SECTIONS.map((section) => (
                                <button
                                    key={section.key}
                                    type="button"
                                    className={`settings-nav-item ${step === section.step ? 'active' : ''}`}
                                    aria-current={step === section.step ? 'page' : undefined}
                                    onClick={() => setStep(section.step)}
                                >
                                    {section.label}
                                </button>
                            ))}
                        </nav>
                        {wizardBody}
                    </div>
                )}

                <div className="wizard-actions">
                    {step > 1 && isOnboarding && (
                        <button className="btn-secondary" onClick={handleBack}>
                            &larr; Back
                        </button>
                    )}

                    {isOnboarding ? (
                        <button
                            className="btn-primary"
                            onClick={handleNext}
                            disabled={isSaving}
                        >
                            {step === totalSteps ? (isSaving ? 'Saving...' : 'Complete Setup') : 'Next →'}
                        </button>
                    ) : (
                        <button
                            className="btn-primary"
                            onClick={handleSave}
                            disabled={isSaving}
                        >
                            {isSaving ? 'Saving...' : 'Save Changes'}
                        </button>
                    )}
                </div>
            </div>
        </div>

        {showBootstrapFlow && (
            <BootstrapLibraryFlow onClose={() => setShowBootstrapFlow(false)} />
        )}
    </>
    );
}

export default ConfigWizard;
