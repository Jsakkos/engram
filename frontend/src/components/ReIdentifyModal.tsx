import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Disc3, Film, Tv, Search, RefreshCw } from 'lucide-react';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';

interface TmdbResult {
    tmdb_id: number;
    name: string;
    type: 'tv' | 'movie';
    year: string;
    poster_path: string | null;
    popularity: number;
}

interface ReIdentifyModalProps {
    job: Job;
    onSubmit: (title: string, contentType: 'tv' | 'movie', season?: number, tmdbId?: number) => void;
    onCancel: () => void;
}

export default function ReIdentifyModal({ job, onSubmit, onCancel }: ReIdentifyModalProps) {
    const [title, setTitle] = useState(job.detected_title || '');
    const [contentType, setContentType] = useState<'movie' | 'tv'>(
        job.content_type === 'tv' ? 'tv' : 'movie'
    );
    const [season, setSeason] = useState<string>(String(job.detected_season || 1));
    const [tmdbId, setTmdbId] = useState<number | undefined>();
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<TmdbResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const titleInputRef = useRef<HTMLInputElement>(null);
    const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

    useEffect(() => {
        titleInputRef.current?.focus();
    }, []);

    const doSearch = useCallback(async (query: string) => {
        if (!query.trim()) {
            setSearchResults([]);
            return;
        }
        setIsSearching(true);
        try {
            const resp = await fetch(`/api/tmdb/search?query=${encodeURIComponent(query)}`);
            if (resp.ok) {
                const data = await resp.json();
                setSearchResults(data.results || []);
            }
        } catch {
            // Silently fail — search is optional
        } finally {
            setIsSearching(false);
        }
    }, []);

    const handleSearchChange = (value: string) => {
        setSearchQuery(value);
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
        searchTimerRef.current = setTimeout(() => doSearch(value), 500);
    };

    const selectResult = (result: TmdbResult) => {
        setTitle(result.name);
        setContentType(result.type);
        setTmdbId(result.tmdb_id);
        setSearchResults([]);
        setSearchQuery('');
    };

    const handleSubmit = () => {
        if (!title.trim()) return;
        onSubmit(
            title.trim(),
            contentType,
            contentType === 'tv' ? (parseInt(season, 10) || 1) : undefined,
            tmdbId,
        );
    };

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter' && !searchQuery) handleSubmit();
        if (e.key === 'Escape') onCancel();
    };

    const inputStyle = (filled: boolean): React.CSSProperties => ({
        width: '100%',
        background: sv.bg1,
        border: `1px solid ${filled ? sv.lineHi : sv.lineMid}`,
        color: sv.cyanHi,
        fontFamily: sv.mono,
        fontSize: 13,
        padding: '10px 12px',
        outline: 'none',
        boxShadow: filled ? `0 0 12px ${sv.cyan}33, inset 0 0 8px ${sv.cyan}0d` : 'none',
        transition: 'border-color 0.18s',
    });

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onKeyDown={handleKeyDown}
            role="dialog"
            aria-modal="true"
            aria-labelledby="re-identify-title"
        >
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onCancel}
            />
            <div
                className="absolute inset-0 pointer-events-none"
                style={{
                    backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 2px, ${sv.cyan} 2px, ${sv.cyan} 4px)`,
                    opacity: 0.03,
                }}
            />

            <motion.div
                className="relative w-full max-w-md"
                initial={{ opacity: 0, scale: 0.92, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 20 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            >
                <SvPanel
                    glow
                    pad={0}
                    style={{
                        background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                        boxShadow: `0 0 40px ${sv.cyan}33, 0 0 80px ${sv.cyan}11, inset 0 0 30px ${sv.cyan}0d`,
                    }}
                >
                    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
                        {/* Header */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <motion.div
                                animate={{ rotate: [0, 360] }}
                                transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
                            >
                                <RefreshCw
                                    size={22}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}cc)` }}
                                />
                            </motion.div>
                            <div style={{ flex: 1 }}>
                                <h2
                                    id="re-identify-title"
                                    style={{
                                        fontFamily: sv.display,
                                        fontWeight: 700,
                                        fontSize: 18,
                                        letterSpacing: '0.2em',
                                        textTransform: 'uppercase',
                                        color: sv.cyanHi,
                                        textShadow: `0 0 10px ${sv.cyan}99`,
                                        margin: 0,
                                    }}
                                >
                                    Re-Identify Disc
                                </h2>
                                <motion.div
                                    style={{
                                        height: 1,
                                        marginTop: 4,
                                        background: `linear-gradient(90deg, ${sv.cyan}cc, transparent)`,
                                    }}
                                    initial={{ scaleX: 0, originX: 0 }}
                                    animate={{ scaleX: 1 }}
                                    transition={{ delay: 0.2, duration: 0.4 }}
                                />
                            </div>
                        </div>

                        {/* Notice */}
                        <div
                            style={{
                                display: 'flex',
                                gap: 12,
                                alignItems: 'flex-start',
                                padding: 12,
                                border: `1px solid ${sv.yellow}4d`,
                                background: `${sv.yellow}0d`,
                            }}
                        >
                            <Disc3
                                size={16}
                                color={sv.yellow}
                                style={{ marginTop: 2, flexShrink: 0, filter: `drop-shadow(0 0 4px ${sv.yellow}99)` }}
                            />
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                                <p
                                    style={{
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        color: `${sv.yellow}cc`,
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.14em',
                                        margin: 0,
                                    }}
                                >
                                    Wrong identification? Correct it below.
                                </p>
                                {job.review_reason && (
                                    <p
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 11,
                                            color: `${sv.yellow}99`,
                                            margin: 0,
                                        }}
                                    >
                                        {job.review_reason}
                                    </p>
                                )}
                            </div>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* TMDB Search */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Search TMDB</SvLabel>
                            <div style={{ position: 'relative' }}>
                                <Search
                                    size={14}
                                    color={sv.inkFaint}
                                    style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }}
                                />
                                <input
                                    type="text"
                                    value={searchQuery}
                                    onChange={(e) => handleSearchChange(e.target.value)}
                                    placeholder="Search for correct title..."
                                    style={{ ...inputStyle(!!searchQuery), paddingLeft: 36 }}
                                    onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                    onBlur={(e) =>
                                        (e.currentTarget.style.borderColor = searchQuery ? sv.lineHi : sv.lineMid)
                                    }
                                />
                                {isSearching && (
                                    <motion.div
                                        animate={{ rotate: 360 }}
                                        transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                                        style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)' }}
                                    >
                                        <RefreshCw size={14} color={sv.cyan} />
                                    </motion.div>
                                )}
                            </div>

                            <AnimatePresence>
                                {searchResults.length > 0 && (
                                    <motion.div
                                        initial={{ opacity: 0, height: 0 }}
                                        animate={{ opacity: 1, height: 'auto' }}
                                        exit={{ opacity: 0, height: 0 }}
                                        style={{
                                            maxHeight: 192,
                                            overflowY: 'auto',
                                            border: `1px solid ${sv.line}`,
                                            background: `${sv.bg1}80`,
                                        }}
                                    >
                                        {searchResults.map((result) => (
                                            <button
                                                key={`${result.type}-${result.tmdb_id}`}
                                                onClick={() => selectResult(result)}
                                                style={{
                                                    width: '100%',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: 12,
                                                    padding: '8px 12px',
                                                    borderBottom: `1px solid ${sv.line}`,
                                                    background: 'transparent',
                                                    cursor: 'pointer',
                                                    textAlign: 'left',
                                                    transition: 'background 0.18s',
                                                }}
                                                onMouseEnter={(e) =>
                                                    (e.currentTarget.style.background = `${sv.cyan}1a`)
                                                }
                                                onMouseLeave={(e) =>
                                                    (e.currentTarget.style.background = 'transparent')
                                                }
                                            >
                                                {result.poster_path ? (
                                                    <img
                                                        src={`https://image.tmdb.org/t/p/w92${result.poster_path}`}
                                                        alt=""
                                                        style={{
                                                            width: 32,
                                                            height: 48,
                                                            objectFit: 'cover',
                                                            flexShrink: 0,
                                                            border: `1px solid ${sv.line}`,
                                                        }}
                                                    />
                                                ) : (
                                                    <div
                                                        style={{
                                                            width: 32,
                                                            height: 48,
                                                            background: sv.bg2,
                                                            border: `1px solid ${sv.line}`,
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            justifyContent: 'center',
                                                            flexShrink: 0,
                                                        }}
                                                    >
                                                        {result.type === 'tv' ? (
                                                            <Tv size={14} color={sv.inkFaint} />
                                                        ) : (
                                                            <Film size={14} color={sv.inkFaint} />
                                                        )}
                                                    </div>
                                                )}
                                                <div style={{ minWidth: 0, flex: 1 }}>
                                                    <p
                                                        style={{
                                                            fontFamily: sv.mono,
                                                            fontSize: 13,
                                                            color: sv.cyanHi,
                                                            margin: 0,
                                                            whiteSpace: 'nowrap',
                                                            overflow: 'hidden',
                                                            textOverflow: 'ellipsis',
                                                        }}
                                                    >
                                                        {result.name}
                                                    </p>
                                                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 2 }}>
                                                        <span
                                                            style={{
                                                                fontFamily: sv.mono,
                                                                fontSize: 10,
                                                                textTransform: 'uppercase',
                                                                padding: '2px 6px',
                                                                color: result.type === 'tv' ? sv.cyan : sv.magenta,
                                                                border: `1px solid ${result.type === 'tv' ? sv.cyan : sv.magenta}4d`,
                                                                background: `${result.type === 'tv' ? sv.cyan : sv.magenta}1a`,
                                                                letterSpacing: '0.14em',
                                                            }}
                                                        >
                                                            {result.type}
                                                        </span>
                                                        {result.year && (
                                                            <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
                                                                {result.year}
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>
                                            </button>
                                        ))}
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Title Input */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Title</SvLabel>
                            <input
                                ref={titleInputRef}
                                type="text"
                                value={title}
                                onChange={(e) => {
                                    setTitle(e.target.value);
                                    setTmdbId(undefined);
                                }}
                                placeholder="e.g. Thunderbirds"
                                style={inputStyle(!!title)}
                                onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                onBlur={(e) =>
                                    (e.currentTarget.style.borderColor = title ? sv.lineHi : sv.lineMid)
                                }
                            />
                        </div>

                        {/* Media Type Toggle */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Media Type</SvLabel>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                                {(
                                    [
                                        { value: 'movie', label: 'Movie', Icon: Film },
                                        { value: 'tv', label: 'TV Show', Icon: Tv },
                                    ] as const
                                ).map(({ value, label, Icon }) => {
                                    const active = contentType === value;
                                    return (
                                        <motion.button
                                            key={value}
                                            type="button"
                                            onClick={() => setContentType(value)}
                                            whileHover={{ scale: 1.02 }}
                                            whileTap={{ scale: 0.98 }}
                                            style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                gap: 8,
                                                padding: '10px 14px',
                                                fontFamily: sv.mono,
                                                fontSize: 11,
                                                fontWeight: 700,
                                                letterSpacing: '0.18em',
                                                textTransform: 'uppercase',
                                                color: active ? sv.magentaHi : sv.inkDim,
                                                border: `1px solid ${active ? sv.magenta : sv.lineMid}`,
                                                background: active ? `${sv.magenta}14` : 'transparent',
                                                boxShadow: active
                                                    ? `0 0 12px ${sv.magenta}4d, inset 0 0 8px ${sv.magenta}0d`
                                                    : 'none',
                                                cursor: 'pointer',
                                                transition: 'all 0.18s',
                                            }}
                                        >
                                            <Icon size={14} />
                                            {label}
                                        </motion.button>
                                    );
                                })}
                            </div>
                        </div>

                        <AnimatePresence>
                            {contentType === 'tv' && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0 }}
                                    animate={{ opacity: 1, height: 'auto' }}
                                    exit={{ opacity: 0, height: 0 }}
                                    transition={{ type: 'spring', stiffness: 400, damping: 35 }}
                                    style={{ overflow: 'hidden' }}
                                >
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 4 }}>
                                        <SvLabel size={10}>Season</SvLabel>
                                        <input
                                            type="number"
                                            min={1}
                                            max={99}
                                            value={season}
                                            onChange={(e) => setSeason(e.target.value)}
                                            style={{ ...inputStyle(true), width: 128, background: sv.bg0 }}
                                            onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                            onBlur={(e) => (e.currentTarget.style.borderColor = sv.lineHi)}
                                        />
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Action Buttons */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            <motion.button
                                type="button"
                                onClick={onCancel}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: sv.red,
                                    border: `1px solid ${sv.red}80`,
                                    background: 'transparent',
                                    boxShadow: `0 0 8px ${sv.red}26`,
                                    cursor: 'pointer',
                                }}
                            >
                                Cancel
                            </motion.button>

                            <motion.button
                                type="button"
                                onClick={handleSubmit}
                                disabled={!title.trim()}
                                whileHover={title.trim() ? { scale: 1.02 } : {}}
                                whileTap={title.trim() ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: title.trim() ? sv.cyan : `${sv.cyan}4d`,
                                    border: `1px solid ${title.trim() ? sv.cyan : `${sv.cyan}33`}`,
                                    background: title.trim() ? `${sv.cyan}1f` : 'transparent',
                                    boxShadow: title.trim()
                                        ? `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
                                        : 'none',
                                    cursor: title.trim() ? 'pointer' : 'not-allowed',
                                    opacity: title.trim() ? 1 : 0.3,
                                }}
                            >
                                Re-Identify
                            </motion.button>
                        </div>
                    </div>

                    {/* Bottom status bar */}
                    <div
                        style={{
                            borderTop: `1px solid ${sv.line}`,
                            padding: '8px 24px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                        }}
                    >
                        <motion.div
                            animate={{ opacity: [0.3, 1, 0.3] }}
                            transition={{ duration: 1.5, repeat: Infinity }}
                            style={{
                                width: 6,
                                height: 6,
                                borderRadius: '50%',
                                background: sv.cyan,
                                filter: `drop-shadow(0 0 3px ${sv.cyan}cc)`,
                            }}
                        />
                        <span
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 10,
                                letterSpacing: '0.22em',
                                textTransform: 'uppercase',
                                color: sv.inkFaint,
                            }}
                        >
                            {job.volume_label || 'Unknown'} · Correcting Identification
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
