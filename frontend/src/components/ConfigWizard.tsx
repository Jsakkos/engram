import { useState, useEffect } from 'react';
import './ConfigWizard.css';

interface ConfigWizardProps {
    onClose: () => void;
    onComplete: () => void;
    isOnboarding?: boolean;
}

interface ConfigData {
    stagingPath: string;
    makemkvPath: string;
    makemkvKey: string;
    libraryMoviesPath: string;
    libraryTvPath: string;
    transcodingEnabled: boolean;
    tmdbApiKey: string;
    maxConcurrentMatches: number;
    ffmpegPath: string;
    conflictResolutionDefault: string;
    stagingCleanupPolicy: string;
    stagingCleanupDays: number;
    extrasPolicy: string;
    namingSeasonFormat: string;
    namingEpisodeFormat: string;
    namingMovieFormat: string;
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

function ConfigWizard({ onClose, onComplete, isOnboarding = true }: ConfigWizardProps) {
    const [step, setStep] = useState(1);
    const [isLoading, setIsLoading] = useState(true);
    const [config, setConfig] = useState<ConfigData>({
        stagingPath: '',
        makemkvPath: '',
        makemkvKey: '',
        libraryMoviesPath: '',
        libraryTvPath: '',
        transcodingEnabled: false,
        tmdbApiKey: '',
        maxConcurrentMatches: 2,
        ffmpegPath: '',
        conflictResolutionDefault: 'ask',
        stagingCleanupPolicy: 'on_success',
        stagingCleanupDays: 7,
        extrasPolicy: 'keep',
        namingSeasonFormat: 'Season {season:02d}',
        namingEpisodeFormat: '{show} - S{season:02d}E{episode:02d}',
        namingMovieFormat: '{title} ({year})',
    });
    const [isSaving, setIsSaving] = useState(false);
    const [toolDetection, setToolDetection] = useState<DetectToolsResponse | null>(null);
    const [isDetecting, setIsDetecting] = useState(false);
    const [showMakemkvOverride, setShowMakemkvOverride] = useState(false);
    const [showFfmpegOverride, setShowFfmpegOverride] = useState(false);
    const [savedKeys, setSavedKeys] = useState<{makemkv: boolean, tmdb: boolean}>({makemkv: false, tmdb: false});
    const [tmdbValidation, setTmdbValidation] = useState<{status: 'idle' | 'testing' | 'valid' | 'invalid', error?: string}>({status: 'idle'});

    const totalSteps = 4;

    // Load existing config on mount
    useEffect(() => {
        const loadConfig = async () => {
            try {
                const response = await fetch('/api/config');
                if (!response.ok) {
                    throw new Error(`Failed to load config: ${response.status}`);
                }
                const data = await response.json();
                console.log('Loaded config from backend:', data);
                // Track which sensitive keys are already saved in the database
                setSavedKeys({
                    makemkv: data.makemkv_key === '***',
                    tmdb: data.tmdb_api_key === '***',
                });
                // Note: API keys are redacted as "***" for security
                setConfig({
                    stagingPath: data.staging_path || '',
                    makemkvPath: data.makemkv_path || '',
                    makemkvKey: data.makemkv_key === '***' ? '' : (data.makemkv_key || ''),
                    libraryMoviesPath: data.library_movies_path || '',
                    libraryTvPath: data.library_tv_path || '',
                    transcodingEnabled: data.transcoding_enabled || false,
                    tmdbApiKey: data.tmdb_api_key === '***' ? '' : (data.tmdb_api_key || ''),
                    maxConcurrentMatches: data.max_concurrent_matches ?? 2,
                    ffmpegPath: data.ffmpeg_path || '',
                    conflictResolutionDefault: data.conflict_resolution_default || 'ask',
                    stagingCleanupPolicy: data.staging_cleanup_policy || 'on_success',
                    stagingCleanupDays: data.staging_cleanup_days ?? 7,
                    extrasPolicy: data.extras_policy || 'keep',
                    namingSeasonFormat: data.naming_season_format || 'Season {season:02d}',
                    namingEpisodeFormat: data.naming_episode_format || '{show} - S{season:02d}E{episode:02d}',
                    namingMovieFormat: data.naming_movie_format || '{title} ({year})',
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
                    transcoding_enabled: config.transcodingEnabled,
                    tmdb_api_key: config.tmdbApiKey,
                    max_concurrent_matches: config.maxConcurrentMatches,
                    ffmpeg_path: config.ffmpegPath,
                    conflict_resolution_default: config.conflictResolutionDefault,
                    staging_cleanup_policy: config.stagingCleanupPolicy,
                    staging_cleanup_days: config.stagingCleanupDays,
                    extras_policy: config.extrasPolicy,
                    naming_season_format: config.namingSeasonFormat,
                    naming_episode_format: config.namingEpisodeFormat,
                    naming_movie_format: config.namingMovieFormat,
                    setup_complete: true,
                }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to save config: ${response.status} ${errorText}`);
            }

            const result = await response.json();
            console.log('Config saved successfully:', result);
            onComplete();
        } catch (error) {
            console.error('Failed to save config:', error);
            alert(`Failed to save configuration: ${error instanceof Error ? error.message : 'Unknown error'}`);
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
        try {
            const response = await fetch('/api/validate/tmdb', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key }),
            });
            const result = await response.json();
            if (result.valid) {
                setTmdbValidation({status: 'valid'});
            } else {
                setTmdbValidation({status: 'invalid', error: result.error || 'Invalid token'});
            }
        } catch {
            setTmdbValidation({status: 'invalid', error: 'Failed to reach validation endpoint'});
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
                        <span className="tool-version">{tool.version}</span>
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
                            onChange={(e) => handleInputChange(configField, e.target.value)}
                            placeholder={`Path to ${toolName.toLowerCase()} executable`}
                        />
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
                    </div>
                );

            case 2: {
                const isWindows = toolDetection?.platform === 'win32';
                const makemkvInstallHint = isWindows
                    ? 'Download installer from makemkv.com'
                    : 'sudo apt install makemkv-bin makemkv-oss';
                const ffmpegInstallHint = isWindows
                    ? 'winget install ffmpeg'
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
                                null,
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
                                {savedKeys.makemkv && (
                                    <span className="ml-2 text-xs font-normal text-green-500">Key saved</span>
                                )}
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
                                {savedKeys.tmdb && (
                                    <span className="ml-2 text-xs font-normal text-green-500">Token saved</span>
                                )}
                            </label>
                            <input
                                id="tmdbApiKey"
                                type="text"
                                value={config.tmdbApiKey}
                                onChange={(e) => {
                                    handleInputChange('tmdbApiKey', e.target.value);
                                    setTmdbValidation({status: 'idle'});
                                }}
                                placeholder={savedKeys.tmdb ? "Enter new token to replace existing" : "eyJhbGciOiJIUzI1NiJ9..."}
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
                                    <span style={{color: '#22c55e', fontSize: '0.85rem'}}>Valid token</span>
                                )}
                                {tmdbValidation.status === 'invalid' && (
                                    <span style={{color: '#ef4444', fontSize: '0.85rem'}}>{tmdbValidation.error}</span>
                                )}
                            </div>
                            <span className="form-hint">
                                The Read Access Token is a long JWT string starting with "eyJ...".
                                Find it under API &rarr; Read Access Token in your TMDB account settings.
                            </span>
                        </div>
                    </div>
                );

            case 4:
                return (
                    <div className="wizard-step">
                        <h3 className="step-title">Preferences</h3>
                        <p className="step-description">
                            Configure additional options for your workflow.
                        </p>

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.transcodingEnabled}
                                    onChange={(e) => handleInputChange('transcodingEnabled', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Enable Transcoding</strong>
                                    <span className="checkbox-hint">
                                        Compress files after ripping using HandBrake (slower, smaller files)
                                    </span>
                                </span>
                            </label>
                        </div>

                        <div className="form-group">
                            <label htmlFor="maxConcurrentMatches">Max Concurrent Matches</label>
                            <input
                                id="maxConcurrentMatches"
                                type="number"
                                min={1}
                                max={4}
                                value={config.maxConcurrentMatches}
                                onChange={(e) => handleInputChange('maxConcurrentMatches', Math.max(1, Math.min(10, parseInt(e.target.value) || 1)))}
                            />
                            <span className="form-hint">
                                Number of episodes matched simultaneously (uses GPU for speech recognition). Lower values reduce memory usage.
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="conflictResolution">Default Conflict Resolution</label>
                            <select
                                id="conflictResolution"
                                value={config.conflictResolutionDefault}
                                onChange={(e) => handleInputChange('conflictResolutionDefault', e.target.value)}
                            >
                                <option value="ask">Always ask me</option>
                                <option value="rename">Automatically rename (keep both)</option>
                                <option value="overwrite">Automatically overwrite</option>
                                <option value="skip">Automatically skip</option>
                            </select>
                            <span className="form-hint">
                                What should Engram do when a file already exists in your library?
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="stagingCleanup">Staging Cleanup Policy</label>
                            <select
                                id="stagingCleanup"
                                value={config.stagingCleanupPolicy}
                                onChange={(e) => handleInputChange('stagingCleanupPolicy', e.target.value)}
                            >
                                <option value="on_success">Clean on success (delete after organization)</option>
                                <option value="on_completion">Clean on completion (delete after success or failure)</option>
                                <option value="after_days">Clean after N days</option>
                                <option value="manual">Manual only (never auto-delete)</option>
                            </select>
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
                            <label htmlFor="extrasPolicy">Extras Handling</label>
                            <select
                                id="extrasPolicy"
                                value={config.extrasPolicy}
                                onChange={(e) => handleInputChange('extrasPolicy', e.target.value)}
                            >
                                <option value="keep">Keep all extras (organize to Extras/ folder)</option>
                                <option value="skip">Skip extras (discard after ripping)</option>
                                <option value="ask">Ask me (show in Review Queue)</option>
                            </select>
                            <span className="form-hint">
                                How to handle bonus content that doesn&apos;t match any episode runtime.
                            </span>
                        </div>

                        <div className="form-group">
                            <label>Naming Convention</label>
                            <select
                                value={
                                    config.namingSeasonFormat === 'Season {season:02d}' &&
                                    config.namingEpisodeFormat === '{show} - S{season:02d}E{episode:02d}'
                                        ? 'plex'
                                    : config.namingSeasonFormat === 'Season {season:d}' &&
                                      config.namingEpisodeFormat === '{show} - S{season:02d}E{episode:02d}'
                                        ? 'kodi'
                                    : config.namingSeasonFormat === 'S{season:02d}' &&
                                      config.namingEpisodeFormat === '{show} - S{season:02d}E{episode:02d}'
                                        ? 'minimal'
                                    : 'custom'
                                }
                                onChange={(e) => {
                                    const preset = e.target.value;
                                    if (preset === 'plex') {
                                        handleInputChange('namingSeasonFormat', 'Season {season:02d}');
                                        handleInputChange('namingEpisodeFormat', '{show} - S{season:02d}E{episode:02d}');
                                    } else if (preset === 'kodi') {
                                        handleInputChange('namingSeasonFormat', 'Season {season:d}');
                                        handleInputChange('namingEpisodeFormat', '{show} - S{season:02d}E{episode:02d}');
                                    } else if (preset === 'minimal') {
                                        handleInputChange('namingSeasonFormat', 'S{season:02d}');
                                        handleInputChange('namingEpisodeFormat', '{show} - S{season:02d}E{episode:02d}');
                                    }
                                }}
                            >
                                <option value="plex">Plex (Season 01 / Show - S01E01)</option>
                                <option value="kodi">Kodi (Season 1 / Show - S01E01)</option>
                                <option value="minimal">Minimal (S01 / Show - S01E01)</option>
                                <option value="custom">Custom</option>
                            </select>
                            <span className="form-hint">
                                Preview: TV/{config.namingSeasonFormat.replace('{season:02d}', '01').replace('{season:d}', '1')}/{config.namingEpisodeFormat.replace('{show}', 'Breaking Bad').replace('{season:02d}', '01').replace('{season:d}', '1').replace('{episode:02d}', '05').replace('{episode:d}', '5')}.mkv
                            </span>
                        </div>

                        {(
                            config.namingSeasonFormat !== 'Season {season:02d}' &&
                            config.namingSeasonFormat !== 'Season {season:d}' &&
                            config.namingSeasonFormat !== 'S{season:02d}'
                        ) && (
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
                                <dt>Transcoding:</dt>
                                <dd>{config.transcodingEnabled ? 'Enabled' : 'Disabled (Passthrough)'}</dd>
                            </dl>
                        </div>
                    </div>
                );

            default:
                return null;
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal wizard-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h2 className="modal-title">Setup Wizard</h2>
                    <button className="modal-close" onClick={onClose}>&times;</button>
                </div>

                <div className={`wizard-progress ${!isOnboarding ? 'tabs-mode' : ''}`}>
                    {[1, 2, 3, 4].map((s) => (
                        <div
                            key={s}
                            className={`progress-step ${s === step ? 'active' : ''} ${s < step || !isOnboarding ? 'completed' : ''} ${!isOnboarding ? 'clickable' : ''}`}
                            onClick={() => !isOnboarding && setStep(s)}
                        >
                            <span className="step-number">{!isOnboarding ? (s === step ? '●' : '○') : (s < step ? '✓' : s)}</span>
                            <span className="step-label">
                                {s === 1 ? 'Paths' : s === 2 ? 'Tools' : s === 3 ? 'TMDB' : 'Preferences'}
                            </span>
                        </div>
                    ))}
                </div>

                <div className="modal-body">
                    {isLoading ? (
                        <div className="wizard-loading">
                            <div className="spinner-mini"></div>
                            <span>Loading configuration...</span>
                        </div>
                    ) : (
                        renderStepContent()
                    )}
                </div>

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
    );
}

export default ConfigWizard;
