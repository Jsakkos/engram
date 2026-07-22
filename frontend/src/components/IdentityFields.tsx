import { useState, useRef, useCallback, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { IcoMovie, IcoTv, IcoSearch, IcoRetry } from '../app/components/icons';
import { SvLabel, sv } from '../app/components/synapse';

export interface TmdbResult {
    tmdb_id: number;
    name: string;
    type: 'tv' | 'movie';
    year: string;
    poster_path: string | null;
    popularity: number;
}

export interface IdentityValue {
    title: string;
    contentType: 'tv' | 'movie';
    season: string; // string so the number input can be transiently empty
    tmdbId: number | undefined;
    selectedYear?: string; // first-air year of the selected TMDB result
}

interface IdentityFieldsProps {
    value: IdentityValue;
    onChange: (next: IdentityValue) => void;
    autoFocus?: boolean;
    /** Fires whenever the TMDB search query changes; lets a host modal gate its
     * own Enter-to-submit handling while the user is mid-search. */
    onSearchQueryChange?: (query: string) => void;
}

/**
 * Controlled TMDB identity form: search-as-you-type, title input, TV/Movie
 * toggle, and season input. Shared between ReIdentifyModal (correcting an
 * existing job's identification) and the arm-a-drive modal (asserting
 * identity before a disc is even inserted). Owns no job-specific state —
 * the parent holds the value and receives changes via onChange.
 */
export default function IdentityFields({
    value,
    onChange,
    autoFocus,
    onSearchQueryChange,
}: IdentityFieldsProps) {
    const { title, contentType, season, tmdbId, selectedYear } = value;

    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<TmdbResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const titleInputRef = useRef<HTMLInputElement>(null);
    const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

    useEffect(() => {
        if (autoFocus) titleInputRef.current?.focus();
    }, [autoFocus]);

    useEffect(() => () => {
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
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

    const handleSearchChange = (query: string) => {
        setSearchQuery(query);
        onSearchQueryChange?.(query);
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
        searchTimerRef.current = setTimeout(() => doSearch(query), 500);
    };

    const selectResult = (result: TmdbResult) => {
        onChange({
            ...value,
            title: result.name,
            contentType: result.type,
            tmdbId: result.tmdb_id,
            selectedYear: result.year || undefined,
        });
        setSearchResults([]);
        setSearchQuery('');
        onSearchQueryChange?.('');
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
        <>
            {/* TMDB Search */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <SvLabel size={10}>Search TMDB</SvLabel>
                <div style={{ position: 'relative' }}>
                    <IcoSearch
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
                            <IcoRetry size={14} color={sv.cyan} />
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
                                                <IcoTv size={14} color={sv.inkFaint} />
                                            ) : (
                                                <IcoMovie size={14} color={sv.inkFaint} />
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
                                                <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim }}>
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
                        onChange({
                            ...value,
                            title: e.target.value,
                            tmdbId: undefined,
                            selectedYear: undefined,
                        });
                    }}
                    placeholder="e.g. Thunderbirds"
                    style={inputStyle(!!title)}
                    onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                    onBlur={(e) =>
                        (e.currentTarget.style.borderColor = title ? sv.lineHi : sv.lineMid)
                    }
                />
                {/* Confirms which TMDB show is selected (year + id) so the
                    user isn't picking a same-name show blind. Only shown
                    once a search result / candidate set the tmdbId. */}
                {tmdbId != null && (
                    <span
                        style={{
                            fontFamily: sv.mono,
                            fontSize: 11,
                            color: sv.cyan,
                        }}
                    >
                        Selected → {title}
                        {selectedYear ? ` (${selectedYear})` : ''} · TMDB #{tmdbId}
                    </span>
                )}
            </div>

            {/* Media Type Toggle */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <SvLabel size={10}>Media Type</SvLabel>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    {(
                        [
                            { value: 'movie', label: 'Movie', Icon: IcoMovie },
                            { value: 'tv', label: 'TV Show', Icon: IcoTv },
                        ] as const
                    ).map(({ value: v, label, Icon }) => {
                        const active = contentType === v;
                        return (
                            <motion.button
                                key={v}
                                type="button"
                                onClick={() => onChange({ ...value, contentType: v })}
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
                                    color: active ? sv.cyanHi : sv.inkDim,
                                    border: `1px solid ${active ? sv.cyan : sv.lineMid}`,
                                    background: active ? `${sv.cyan}14` : 'transparent',
                                    boxShadow: active
                                        ? `0 0 12px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
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
                                onChange={(e) => onChange({ ...value, season: e.target.value })}
                                style={{ ...inputStyle(true), width: 128, background: sv.bg0 }}
                                onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                onBlur={(e) => (e.currentTarget.style.borderColor = sv.lineHi)}
                            />
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
}
