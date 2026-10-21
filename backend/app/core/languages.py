"""Language codes for disc streams and media files (issue #241).

MakeMKV reports stream languages as ISO 639-2 codes and mixes the bibliographic
and terminology forms on one disc (``fre`` and ``fra``, ``ger`` and ``deu``);
ffprobe reports whatever the container was tagged with, which can also be
639-1. Every comparison goes through ``normalize_language`` so the two never
disagree about whether a track is English.

The table is deliberately small and hand-written: a library (langcodes,
pycountry) would add a frozen-build hidden-import risk for a lookup this size.
"""

# 639-1 code -> (English name, 639-2 bibliographic/terminology aliases).
_LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "en": ("English", ("eng",)),
    "ja": ("Japanese", ("jpn",)),
    "zh": ("Chinese", ("chi", "zho")),
    "ko": ("Korean", ("kor",)),
    "th": ("Thai", ("tha",)),
    "vi": ("Vietnamese", ("vie",)),
    "de": ("German", ("ger", "deu")),
    "fr": ("French", ("fre", "fra")),
    "es": ("Spanish", ("spa",)),
    "pt": ("Portuguese", ("por",)),
    "it": ("Italian", ("ita",)),
    "nl": ("Dutch", ("dut", "nld")),
    "sv": ("Swedish", ("swe",)),
    "no": ("Norwegian", ("nor",)),
    "nb": ("Norwegian Bokmal", ("nob",)),
    "da": ("Danish", ("dan",)),
    "fi": ("Finnish", ("fin",)),
    "is": ("Icelandic", ("ice", "isl")),
    "pl": ("Polish", ("pol",)),
    "cs": ("Czech", ("cze", "ces")),
    "sk": ("Slovak", ("slo", "slk")),
    "hu": ("Hungarian", ("hun",)),
    "ro": ("Romanian", ("rum", "ron")),
    "bg": ("Bulgarian", ("bul",)),
    "ru": ("Russian", ("rus",)),
    "uk": ("Ukrainian", ("ukr",)),
    "el": ("Greek", ("gre", "ell")),
    "tr": ("Turkish", ("tur",)),
    "ar": ("Arabic", ("ara",)),
    "he": ("Hebrew", ("heb",)),
    "hi": ("Hindi", ("hin",)),
    "ta": ("Tamil", ("tam",)),
    "te": ("Telugu", ("tel",)),
    "bn": ("Bengali", ("ben",)),
    "id": ("Indonesian", ("ind",)),
    "ms": ("Malay", ("may", "msa")),
    "tl": ("Tagalog", ("tgl",)),
    "fa": ("Persian", ("per", "fas")),
    "hr": ("Croatian", ("hrv",)),
    "sr": ("Serbian", ("srp",)),
}

_ALIASES: dict[str, str] = {
    alias: code for code, (_, aliases) in _LANGUAGES.items() for alias in aliases
}

# Codes that say "no particular language": undetermined, multiple, none, uncoded.
_UNKNOWN = {"und", "mul", "zxx", "mis"}


def normalize_language(code: str | None) -> str | None:
    """The 639-1 code for ``code``, the lower-cased code if unmapped, or None if unknown."""
    if not code:
        return None
    code = code.strip().lower()
    if not code or code in _UNKNOWN:
        return None
    # qaa..qtz is ISO 639-2's private-use range: no shared meaning.
    if len(code) == 3 and "qaa" <= code <= "qtz":
        return None
    if code in _LANGUAGES:
        return code
    return _ALIASES.get(code, code)


def language_name(code: str | None) -> str:
    """English name for messages ("Japanese"); the upper-cased code when unmapped."""
    normalized = normalize_language(code)
    if normalized is None:
        return "an unknown language"
    entry = _LANGUAGES.get(normalized)
    return entry[0] if entry else normalized.upper()


def is_english(code: str | None) -> bool:
    return normalize_language(code) == "en"
