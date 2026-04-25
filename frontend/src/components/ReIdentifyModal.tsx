import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Disc3, Film, Tv, Search, RefreshCw } from 'lucide-react';
import type { Job } from '../types';

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
            {/* Backdrop */}
            <motion.div
                className="absolute inset-0 bg-navy-900/85 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onCancel}
            />

            {/* Scanline overlay */}
            <div
                className="absolute inset-0 pointer-events-none opacity-[0.03]"
                style={{
                    backgroundImage:
                        'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(6, 182, 212, 1) 2px, rgba(6, 182, 212, 1) 4px)',
                }}
            />

            {/* Card */}
            <motion.div
                className="relative w-full max-w-md bg-navy-900 border border-cyan-500/50 overflow-hidden"
                style={{
                    boxShadow:
                        '0 0 40px rgba(6, 182, 212, 0.3), 0 0 80px rgba(6, 182, 212, 0.1), inset 0 0 30px rgba(6, 182, 212, 0.05)',
                }}
                initial={{ opacity: 0, scale: 0.92, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 20 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            >
                {/* Corner accents */}
                <motion.div
                    className="absolute top-0 left-0 w-6 h-6 border-t-2 border-l-2 border-cyan-400"
                    animate={{ opacity: [0.5, 1, 0.5] }}
                    transition={{ duration: 2, repeat: Infinity }}
                />
                <motion.div
                    className="absolute top-0 right-0 w-6 h-6 border-t-2 border-r-2 border-pink-500"
                    animate={{ opacity: [1, 0.5, 1] }}
                    transition={{ duration: 2, repeat: Infinity }}
                />
                <motion.div
                    className="absolute bottom-0 left-0 w-6 h-6 border-b-2 border-l-2 border-pink-500"
                    animate={{ opacity: [0.5, 1, 0.5] }}
                    transition={{ duration: 2, repeat: Infinity, delay: 1 }}
                />
                <motion.div
                    className="absolute bottom-0 right-0 w-6 h-6 border-b-2 border-r-2 border-cyan-400"
                    animate={{ opacity: [1, 0.5, 1] }}
                    transition={{ duration: 2, repeat: Infinity, delay: 1 }}
                />

                {/* Ambient glow */}
                <motion.div
                    className="absolute inset-0 pointer-events-none"
                    animate={{
                        background: [
                            'radial-gradient(circle at 0% 0%, rgba(6,182,212,0.06) 0%, transparent 60%)',
                            'radial-gradient(circle at 100% 100%, rgba(236,72,153,0.06) 0%, transparent 60%)',
                            'radial-gradient(circle at 0% 0%, rgba(6,182,212,0.06) 0%, transparent 60%)',
                        ],
                    }}
                    transition={{ duration: 6, repeat: Infinity, ease: 'linear' }}
                />

                <div className="relative p-6 space-y-5">
                    {/* Header */}
                    <div className="flex items-center gap-3">
                        <motion.div
                            animate={{ rotate: [0, 360] }}
                            transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
                        >
                            <RefreshCw
                                className="w-6 h-6 text-cyan-400"
                                style={{ filter: 'drop-shadow(0 0 6px rgba(6,182,212,0.8))' }}
                            />
                        </motion.div>
                        <div>
                            <h2
                                id="re-identify-title"
                                className="font-mono font-bold text-lg tracking-[0.2em] uppercase text-cyan-300"
                                style={{ textShadow: '0 0 10px rgba(6,182,212,0.6)' }}
                            >
                                Re-Identify Disc
                            </h2>
                            <motion.div
                                className="h-px bg-gradient-to-r from-cyan-500/80 to-transparent mt-1"
                                initial={{ scaleX: 0, originX: 0 }}
                                animate={{ scaleX: 1 }}
                                transition={{ delay: 0.2, duration: 0.4 }}
                            />
                        </div>
                    </div>

                    {/* Current identification info */}
                    <div className="flex items-start gap-3 border border-yellow-500/30 bg-yellow-500/5 p-3">
                        <Disc3
                            className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0"
                            style={{ filter: 'drop-shadow(0 0 4px rgba(234,179,8,0.6))' }}
                        />
                        <div className="space-y-1 min-w-0">
                            <p className="font-mono text-xs text-yellow-300/80 uppercase tracking-wider">
                                Wrong identification? Correct it below.
                            </p>
                            {job.review_reason && (
                                <p className="text-xs font-mono text-yellow-500/60">
                                    {job.review_reason}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Divider */}
                    <div className="h-px bg-cyan-500/20" />

                    {/* TMDB Search */}
                    <div className="space-y-2">
                        <label className="block font-mono text-xs tracking-[0.15em] uppercase text-cyan-400/70">
                            Search TMDB
                        </label>
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-cyan-600" />
                            <input
                                type="text"
                                value={searchQuery}
                                onChange={(e) => handleSearchChange(e.target.value)}
                                placeholder="Search for correct title..."
                                className="w-full bg-navy-800 border-2 border-cyan-500/30 text-cyan-300 font-mono text-sm pl-10 pr-3 py-2.5 placeholder:text-cyan-800 focus:outline-none focus:border-cyan-500 transition-colors"
                            />
                            {isSearching && (
                                <motion.div
                                    className="absolute right-3 top-1/2 -translate-y-1/2"
                                    animate={{ rotate: 360 }}
                                    transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                                >
                                    <RefreshCw className="w-4 h-4 text-cyan-500" />
                                </motion.div>
                            )}
                        </div>

                        {/* Search Results */}
                        <AnimatePresence>
                            {searchResults.length > 0 && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0 }}
                                    animate={{ opacity: 1, height: 'auto' }}
                                    exit={{ opacity: 0, height: 0 }}
                                    className="max-h-48 overflow-y-auto border border-cyan-500/20 bg-navy-800/50"
                                >
                                    {searchResults.map((result) => (
                                        <button
                                            key={`${result.type}-${result.tmdb_id}`}
                                            onClick={() => selectResult(result)}
                                            className="w-full flex items-center gap-3 px-3 py-2 hover:bg-cyan-500/10 transition-colors text-left border-b border-cyan-500/10 last:border-b-0"
                                        >
                                            {result.poster_path ? (
                                                <img
                                                    src={`https://image.tmdb.org/t/p/w92${result.poster_path}`}
                                                    alt=""
                                                    className="w-8 h-12 object-cover flex-shrink-0 border border-cyan-500/20"
                                                />
                                            ) : (
                                                <div className="w-8 h-12 bg-navy-700 border border-cyan-500/20 flex items-center justify-center flex-shrink-0">
                                                    {result.type === 'tv' ? (
                                                        <Tv className="w-4 h-4 text-cyan-600" />
                                                    ) : (
                                                        <Film className="w-4 h-4 text-cyan-600" />
                                                    )}
                                                </div>
                                            )}
                                            <div className="min-w-0 flex-1">
                                                <p className="font-mono text-sm text-cyan-300 truncate">
                                                    {result.name}
                                                </p>
                                                <div className="flex items-center gap-2">
                                                    <span className={`font-mono text-[10px] uppercase px-1.5 py-0.5 border ${
                                                        result.type === 'tv'
                                                            ? 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10'
                                                            : 'text-pink-400 border-pink-500/30 bg-pink-500/10'
                                                    }`}>
                                                        {result.type}
                                                    </span>
                                                    {result.year && (
                                                        <span className="font-mono text-[10px] text-cyan-600">
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

                    {/* Divider */}
                    <div className="h-px bg-cyan-500/20" />

                    {/* Title Input */}
                    <div className="space-y-2">
                        <label className="block font-mono text-xs tracking-[0.15em] uppercase text-cyan-400/70">
                            Title
                        </label>
                        <input
                            ref={titleInputRef}
                            type="text"
                            value={title}
                            onChange={(e) => { setTitle(e.target.value); setTmdbId(undefined); }}
                            placeholder="e.g. Thunderbirds"
                            className="w-full bg-navy-800 border-2 border-cyan-500/30 text-cyan-300 font-mono text-sm px-3 py-2.5 placeholder:text-cyan-800 focus:outline-none focus:border-cyan-500 transition-colors"
                            style={{
                                boxShadow: title
                                    ? '0 0 12px rgba(6,182,212,0.2), inset 0 0 8px rgba(6,182,212,0.05)'
                                    : 'none',
                            }}
                        />
                    </div>

                    {/* Media Type Toggle */}
                    <div className="space-y-2">
                        <label className="block font-mono text-xs tracking-[0.15em] uppercase text-cyan-400/70">
                            Media Type
                        </label>
                        <div className="grid grid-cols-2 gap-2">
                            {(
                                [
                                    { value: 'movie', label: 'Movie', Icon: Film },
                                    { value: 'tv', label: 'TV Show', Icon: Tv },
                                ] as const
                            ).map(({ value, label, Icon }) => (
                                <motion.button
                                    key={value}
                                    type="button"
                                    onClick={() => setContentType(value)}
                                    className="relative flex items-center justify-center gap-2 py-2.5 px-4 border-2 font-mono text-xs font-bold uppercase tracking-wider transition-colors"
                                    animate={
                                        contentType === value
                                            ? {
                                                borderColor: 'rgba(236,72,153,0.8)',
                                                color: 'rgb(249,168,212)',
                                            }
                                            : {
                                                borderColor: 'rgba(6,182,212,0.2)',
                                                color: 'rgba(6,182,212,0.5)',
                                            }
                                    }
                                    style={
                                        contentType === value
                                            ? {
                                                boxShadow:
                                                    '0 0 12px rgba(236,72,153,0.3), inset 0 0 8px rgba(236,72,153,0.05)',
                                                background: 'rgba(236,72,153,0.08)',
                                            }
                                            : {}
                                    }
                                    whileHover={{ scale: 1.02 }}
                                    whileTap={{ scale: 0.98 }}
                                >
                                    <Icon className="w-3.5 h-3.5" />
                                    {label}
                                    {contentType === value && (
                                        <motion.div
                                            className="absolute inset-0 border border-pink-400/30"
                                            layoutId="re-id-media-type"
                                            transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                                        />
                                    )}
                                </motion.button>
                            ))}
                        </div>
                    </div>

                    {/* Season field — TV only */}
                    <AnimatePresence>
                        {contentType === 'tv' && (
                            <motion.div
                                initial={{ opacity: 0, height: 0, marginTop: 0 }}
                                animate={{ opacity: 1, height: 'auto', marginTop: undefined }}
                                exit={{ opacity: 0, height: 0, marginTop: 0 }}
                                transition={{ type: 'spring', stiffness: 400, damping: 35 }}
                                className="overflow-hidden"
                            >
                                <div className="space-y-2 pt-1">
                                    <label className="block font-mono text-xs tracking-[0.15em] uppercase text-cyan-400/70">
                                        Season
                                    </label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={99}
                                        value={season}
                                        onChange={(e) => setSeason(e.target.value)}
                                        className="w-32 bg-black border-2 border-cyan-500/30 text-cyan-300 font-mono text-sm px-3 py-2.5 focus:outline-none focus:border-cyan-500 transition-colors"
                                        style={{ boxShadow: '0 0 8px rgba(6,182,212,0.1)' }}
                                    />
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Divider */}
                    <div className="h-px bg-cyan-500/20" />

                    {/* Action Buttons */}
                    <div className="flex items-center justify-between gap-3">
                        <motion.button
                            type="button"
                            onClick={onCancel}
                            className="flex-1 py-2.5 px-4 border-2 border-red-500/50 text-red-400 font-mono text-xs font-bold uppercase tracking-wider hover:bg-red-500/10 hover:border-red-500 transition-colors"
                            style={{ boxShadow: '0 0 8px rgba(239,68,68,0.15)' }}
                            whileHover={{ scale: 1.02 }}
                            whileTap={{ scale: 0.97 }}
                        >
                            Cancel
                        </motion.button>

                        <motion.button
                            type="button"
                            onClick={handleSubmit}
                            disabled={!title.trim()}
                            className="flex-1 py-2.5 px-4 border-2 font-mono text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                            animate={
                                title.trim()
                                    ? {
                                        borderColor: 'rgba(6,182,212,0.8)',
                                        color: 'rgb(6,182,212)',
                                        backgroundColor: 'rgba(6,182,212,0.12)',
                                    }
                                    : {
                                        borderColor: 'rgba(6,182,212,0.2)',
                                        color: 'rgba(6,182,212,0.3)',
                                        backgroundColor: 'transparent',
                                    }
                            }
                            style={
                                title.trim()
                                    ? {
                                        boxShadow:
                                            '0 0 16px rgba(6,182,212,0.3), inset 0 0 8px rgba(6,182,212,0.05)',
                                    }
                                    : {}
                            }
                            whileHover={title.trim() ? { scale: 1.02 } : {}}
                            whileTap={title.trim() ? { scale: 0.97 } : {}}
                        >
                            Re-Identify
                        </motion.button>
                    </div>
                </div>

                {/* Bottom status bar */}
                <div className="border-t border-cyan-500/20 px-6 py-2 flex items-center gap-2">
                    <motion.div
                        className="w-1.5 h-1.5 rounded-full bg-cyan-400"
                        animate={{ opacity: [0.3, 1, 0.3] }}
                        transition={{ duration: 1.5, repeat: Infinity }}
                        style={{ filter: 'drop-shadow(0 0 3px rgba(6,182,212,0.8))' }}
                    />
                    <span className="font-mono text-[10px] tracking-widest uppercase text-cyan-600">
                        {job.volume_label || 'Unknown'} · Correcting Identification
                    </span>
                </div>
            </motion.div>
        </motion.div>
    );
}
