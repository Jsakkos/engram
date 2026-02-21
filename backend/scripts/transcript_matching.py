# %% [markdown]
# # Transcript Matching Algorithm Benchmark
#
# Compares text-matching algorithms for identifying which episode
# a noisy transcript chunk belongs to, using subtitle files from
# the UMA cache across multiple TV shows.

# %% Imports & Configuration
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── Config ──────────────────────────────────────────────────────────────────
CACHE_DIR = Path(r"C:\Users\jonat\.uma\cache\data")
SEED = 42
NUM_TESTS_PER_EPISODE = 8  # test cases generated per episode
CHUNK_LENGTHS_SEC = [10, 30, 60, 120]  # seconds of subtitle text to extract
NOISE_DROP_RATES = [0.0, 0.05, 0.10, 0.20]  # fraction of words randomly dropped
NOISE_SUB_RATE = 0.03  # fraction of words randomly substituted (constant)

random.seed(SEED)

# ── Show Configuration ──────────────────────────────────────────────────────
# Each show specifies: directory name, season to test, and filename pattern
# Pattern types:
#   "s_e"   → "Show Name - S01E01.srt"
#   "n_x"   → "Show Name - 1x01 - Title.extra.srt"

SHOWS = [
    {"name": "Arrested Development", "season": 1, "pattern": "s_e"},
    {"name": "Breaking Bad", "season": 1, "pattern": "n_x"},
    {"name": "Seinfeld", "season": 3, "pattern": "s_e"},
    {"name": "The Office", "season": 1, "pattern": "s_e"},
    {"name": "Stranger Things", "season": 1, "pattern": "s_e"},
    {"name": "Game of Thrones", "season": 7, "pattern": "s_e"},
]


# %% SRT Parser & Text Cleaner
# ─────────────────────────────────────────────────────────────────────────────


def parse_timestamp(ts: str) -> float:
    """Parse SRT timestamp '00:01:23,456' into seconds."""
    ts = ts.strip().replace(",", ".")
    h, m, s = ts.split(":")
    return float(h) * 3600 + float(m) * 60 + float(s)


def clean_text(text: str) -> str:
    """Lowercase, strip HTML/bracket tags, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"\[.*?\]|<.*?>", "", text)  # remove [tags] and <tags>
    text = re.sub(r"([A-Za-z])-\1+", r"\1", text)  # collapse stutters
    text = re.sub(r"[^\w\s']", " ", text)  # remove special chars except apostrophes
    return " ".join(text.split())


@dataclass
class SubBlock:
    start: float
    end: float
    text: str


@dataclass
class Episode:
    number: int
    blocks: list[SubBlock]
    full_text: str = ""
    duration: float = 0.0


def extract_episode_number(filename: str, pattern: str, season: int) -> int:
    """Extract episode number from filename based on pattern type."""
    stem = Path(filename).stem

    if pattern == "s_e":
        # Match S01E03, S1E3, etc.
        m = re.search(r"S\d+E(\d+)", stem, re.IGNORECASE)
        if m:
            return int(m.group(1))
    elif pattern == "n_x":
        # Match 1x03, 01x03, etc.
        m = re.search(rf"{season}x(\d+)", stem, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 0


def file_matches_season(filename: str, pattern: str, season: int) -> bool:
    """Check if a subtitle file belongs to the specified season."""
    stem = Path(filename).stem

    if pattern == "s_e":
        m = re.search(r"S(\d+)E\d+", stem, re.IGNORECASE)
        return m is not None and int(m.group(1)) == season
    elif pattern == "n_x":
        m = re.search(r"(\d+)x\d+", stem, re.IGNORECASE)
        return m is not None and int(m.group(1)) == season
    return False


def parse_srt(filepath: Path, pattern: str = "s_e", season: int = 1) -> Episode:
    """Parse an SRT file into an Episode with timed blocks."""
    raw = filepath.read_bytes()
    # try utf-8-sig first (BOM), then utf-8, then latin-1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        content = raw.decode("latin-1", errors="replace")

    # Normalize line endings (Windows \r\n → \n)
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    ep_num = extract_episode_number(filepath.name, pattern, season)

    blocks: list[SubBlock] = []
    for block_text in content.strip().split("\n\n"):
        lines = block_text.strip().split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            parts = lines[1].split("-->")
            start = parse_timestamp(parts[0])
            end = parse_timestamp(parts[1])
            text = " ".join(lines[2:])
            cleaned = clean_text(text)
            if cleaned:
                blocks.append(SubBlock(start, end, cleaned))
        except (IndexError, ValueError):
            continue

    full = " ".join(b.text for b in blocks)
    dur = max(b.end for b in blocks) if blocks else 0.0
    return Episode(number=ep_num, blocks=blocks, full_text=full, duration=dur)


def load_show_episodes(show_config: dict) -> dict[int, Episode]:
    """Load all SRT files for a specific show and season."""
    show_dir = CACHE_DIR / show_config["name"]
    pattern = show_config["pattern"]
    season = show_config["season"]

    if not show_dir.exists():
        print(f"  [WARN] Directory not found: {show_dir}")
        return {}

    episodes = {}
    for srt in sorted(show_dir.glob("*.srt")):
        if not file_matches_season(srt.name, pattern, season):
            continue
        ep = parse_srt(srt, pattern, season)
        if ep.blocks and ep.number > 0:
            episodes[ep.number] = ep

    return episodes


def load_all_shows() -> dict[str, dict[int, Episode]]:
    """Load episodes for all configured shows."""
    all_shows = {}
    for show_config in SHOWS:
        name = show_config["name"]
        season = show_config["season"]
        print(f"  Loading {name} Season {season}...")
        episodes = load_show_episodes(show_config)
        if episodes:
            avg_blocks = sum(len(e.blocks) for e in episodes.values()) // len(episodes)
            avg_words = sum(len(e.full_text.split()) for e in episodes.values()) // len(episodes)
            print(
                f"    → {len(episodes)} episodes, "
                f"avg {avg_blocks} blocks, "
                f"avg {avg_words} words each"
            )
            all_shows[f"{name} S{season:02d}"] = episodes
        else:
            print("    → [SKIP] No episodes found")
    return all_shows


# %% Chunk Extractor
# ─────────────────────────────────────────────────────────────────────────────


def extract_chunk(episode: Episode, start_sec: float, length_sec: float) -> str:
    """Extract subtitle text from [start_sec, start_sec+length_sec]."""
    end_sec = start_sec + length_sec
    texts = [b.text for b in episode.blocks if b.end >= start_sec and b.start <= end_sec]
    return " ".join(texts)


def extract_chunk_at_position(
    episode: Episode, length_sec: float, position: str
) -> tuple[float, str]:
    """Extract a chunk at 'beginning', 'middle', 'end', or 'random' position."""
    max_start = max(0, episode.duration - length_sec)
    if position == "beginning":
        start = min(10.0, max_start)  # skip first 10s (often intro silence)
    elif position == "end":
        start = max(0, max_start - 5.0)
    elif position == "middle":
        start = max_start / 2
    else:  # random
        start = random.uniform(0, max_start) if max_start > 0 else 0
    text = extract_chunk(episode, start, length_sec)
    return start, text


# %% Noise Injector
# ─────────────────────────────────────────────────────────────────────────────

COMMON_WORDS = [
    "the",
    "a",
    "an",
    "is",
    "was",
    "are",
    "it",
    "he",
    "she",
    "they",
    "we",
    "you",
    "that",
    "this",
    "but",
    "and",
    "or",
    "so",
    "if",
    "can",
    "just",
    "not",
    "what",
    "with",
    "for",
    "have",
    "had",
    "been",
    "get",
    "well",
    "like",
    "know",
    "think",
    "going",
    "really",
    "very",
    "here",
    "there",
    "some",
    "then",
    "when",
    "how",
    "now",
    "all",
    "right",
    "yeah",
]

# Phonetic substitution pairs that mimic ASR (Whisper) errors
ASR_SUBSTITUTIONS = [
    ("their", "there"),
    ("there", "their"),
    ("they're", "there"),
    ("your", "you're"),
    ("you're", "your"),
    ("to", "too"),
    ("too", "to"),
    ("two", "to"),
    ("its", "it's"),
    ("it's", "its"),
    ("then", "than"),
    ("than", "then"),
    ("we're", "were"),
    ("were", "we're"),
    ("he's", "his"),
    ("no", "know"),
    ("write", "right"),
    ("right", "write"),
    ("hear", "here"),
    ("here", "hear"),
    ("new", "knew"),
    ("knew", "new"),
    ("would", "wood"),
    ("see", "sea"),
]

# Filler words that Whisper sometimes hallucinates
ASR_FILLERS = ["um", "uh", "like", "you know", "i mean", "so", "well", "okay"]


def drop_words(text: str, rate: float) -> str:
    """Randomly drop words at `rate` frequency."""
    if rate <= 0:
        return text
    words = text.split()
    return " ".join(w for w in words if random.random() > rate)


def substitute_words(text: str, rate: float) -> str:
    """Replace random words with common filler words."""
    if rate <= 0:
        return text
    words = text.split()
    return " ".join(random.choice(COMMON_WORDS) if random.random() < rate else w for w in words)


def asr_noise(text: str, sub_rate: float = 0.03, filler_rate: float = 0.02) -> str:
    """Apply ASR-style noise: phonetic substitutions and hallucinated fillers."""
    words = text.split()
    result = []
    sub_map = {src: dst for src, dst in ASR_SUBSTITUTIONS}

    for w in words:
        # Phonetic substitution
        if random.random() < sub_rate and w in sub_map:
            result.append(sub_map[w])
        else:
            result.append(w)
        # Occasionally insert filler words
        if random.random() < filler_rate:
            result.append(random.choice(ASR_FILLERS))

    return " ".join(result)


def add_noise(text: str, drop_rate: float, sub_rate: float) -> str:
    """Apply word dropping, substitution, and ASR-style noise."""
    text = drop_words(text, drop_rate)
    text = substitute_words(text, sub_rate)
    text = asr_noise(text, sub_rate=0.02, filler_rate=0.01)
    return text


# %% Test Case Generator
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TestCase:
    show: str  # show identifier (e.g., "Arrested Development S01")
    episode: int
    chunk_length_sec: float
    position: str
    start_sec: float
    drop_rate: float
    sub_rate: float
    clean_text: str  # before noise
    noisy_text: str  # after noise
    label: str = ""  # human-readable ID


def generate_test_cases(show_name: str, episodes: dict[int, Episode]) -> list[TestCase]:
    """Generate a grid of test cases across episodes, lengths, noise levels."""
    cases = []
    positions = ["beginning", "middle", "end", "random"]

    for ep_num, ep in episodes.items():
        for length in CHUNK_LENGTHS_SEC:
            if length > ep.duration:
                continue
            for drop_rate in NOISE_DROP_RATES:
                pos = random.choice(positions)
                start, chunk = extract_chunk_at_position(ep, length, pos)
                if len(chunk.split()) < 5:
                    continue  # skip empty/tiny chunks
                noisy = add_noise(chunk, drop_rate, NOISE_SUB_RATE)
                label = f"{show_name}:E{ep_num:02d}_{length}s_{pos}_{int(drop_rate * 100)}%drop"
                cases.append(
                    TestCase(
                        show=show_name,
                        episode=ep_num,
                        chunk_length_sec=length,
                        position=pos,
                        start_sec=start,
                        drop_rate=drop_rate,
                        sub_rate=NOISE_SUB_RATE,
                        clean_text=chunk,
                        noisy_text=noisy,
                        label=label,
                    )
                )

    return cases


# %% ── Matching Algorithm ───────────────────────────────────────────────────


class TfidfCosineAlgorithm:
    """
    TF-IDF + Cosine Similarity matcher.

    How TF-IDF Works:
    ─────────────────
    TF-IDF (Term Frequency × Inverse Document Frequency) converts text into
    numeric vectors by weighing each word's importance:

    • TF(t, d)  = how often term t appears in document d
                  With sublinear_tf: TF = 1 + log(raw_count) — diminishes
                  the impact of a word appearing 100 times vs 10 times.

    • IDF(t)    = log(N / df(t)) where N = total documents, df(t) = how many
                  documents contain term t. Rare words get HIGH weight,
                  common words (the, is, a) get LOW weight.

    • TF-IDF(t,d) = TF(t,d) × IDF(t)

    The resulting vectors live in a high-dimensional space where each dimension
    is a word (or bigram). Cosine similarity measures the angle between two
    vectors — documents about the same topic point in similar directions.

    Key Hyperparameters:
    ────────────────────
    • ngram_range=(1, 2): Include both single words AND word pairs.
      "arrested development" as a bigram is far more distinctive than
      "arrested" or "development" alone.

    • max_features=10000: Cap vocabulary at 10k most informative terms.
      Prevents memory bloat while keeping enough signal.

    • sublinear_tf=True: Use 1+log(tf) instead of raw counts.
      Prevents a word appearing 50 times from dominating the vector.

    Why It Works for Episode Matching:
    ──────────────────────────────────
    Each episode has unique dialogue with distinctive character names,
    plot-specific terms, and unique word combinations. TF-IDF naturally
    upweights these distinctive terms and downweights common dialogue
    filler ("okay", "yeah", "going to"). The cosine similarity is
    insensitive to document length — a 30s chunk can match against
    a 22-minute episode because we compare *direction* not *magnitude*.
    """

    name = "TF-IDF Cosine"

    def __init__(self):
        self.vectorizer = None
        self.ref_matrix = None
        self.ep_order = []
        self.references = {}

    def prepare(self, references: dict[int, str]):
        self.references = references
        self.ep_order = sorted(references.keys())
        corpus = [references[ep] for ep in self.ep_order]
        self.vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            max_features=10000,
            sublinear_tf=True,
        )
        self.ref_matrix = self.vectorizer.fit_transform(corpus)

    def match(self, query: str) -> tuple[int, float]:
        """Return (best_episode, confidence 0-1)."""
        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.ref_matrix)[0]
        best_idx = sims.argmax()
        return self.ep_order[best_idx], float(sims[best_idx])

    def match_all(self, query: str) -> list[tuple[int, float]]:
        """Return all (episode, score) pairs sorted descending."""
        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.ref_matrix)[0]
        results = [(self.ep_order[i], float(sims[i])) for i in range(len(self.ep_order))]
        results.sort(key=lambda x: x[1], reverse=True)
        return results


# %% Test Bench Runner
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AlgorithmResult:
    algorithm: str
    total: int = 0
    correct: int = 0
    total_time_ms: float = 0.0
    confidences_correct: list[float] = field(default_factory=list)
    confidences_wrong: list[float] = field(default_factory=list)
    # breakdowns
    by_length: dict = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "correct": 0}))
    by_noise: dict = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "correct": 0}))
    by_show: dict = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "correct": 0}))
    # confusion tracking: (true_ep, predicted_ep) → count
    confusion: dict = field(default_factory=lambda: defaultdict(int))
    # gap between top-1 and top-2 scores
    score_gaps_correct: list[float] = field(default_factory=list)
    score_gaps_wrong: list[float] = field(default_factory=list)
    # Per-episode tracking
    per_episode: dict = field(
        default_factory=lambda: defaultdict(lambda: {"total": 0, "correct": 0})
    )

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        predicted = self.correct + len(self.confidences_wrong)
        return self.correct / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.total if self.total else 0.0

    @property
    def median_confidence_correct(self) -> float:
        if not self.confidences_correct:
            return 0.0
        s = sorted(self.confidences_correct)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    @property
    def median_score_gap(self) -> float:
        if not self.score_gaps_correct:
            return 0.0
        s = sorted(self.score_gaps_correct)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def run_bench_for_show(
    algo: TfidfCosineAlgorithm,
    test_cases: list[TestCase],
    result: AlgorithmResult,
    show_name: str,
) -> None:
    """Run test cases for a single show and accumulate into result."""
    for tc in test_cases:
        t0 = time.perf_counter()
        pred_ep, confidence = algo.match(tc.noisy_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Also get top-2 for gap analysis
        all_scores = algo.match_all(tc.noisy_text)
        gap = all_scores[0][1] - all_scores[1][1] if len(all_scores) >= 2 else 0.0

        correct = pred_ep == tc.episode

        result.total += 1
        result.total_time_ms += elapsed_ms

        ep_key = f"{show_name}:E{tc.episode:02d}"
        result.per_episode[ep_key]["total"] += 1

        if correct:
            result.correct += 1
            result.confidences_correct.append(confidence)
            result.score_gaps_correct.append(gap)
            result.per_episode[ep_key]["correct"] += 1
        else:
            result.confidences_wrong.append(confidence)
            result.score_gaps_wrong.append(gap)
            result.confusion[(tc.episode, pred_ep)] += 1

        # breakdowns
        length_key = f"{int(tc.chunk_length_sec)}s"
        result.by_length[length_key]["total"] += 1
        if correct:
            result.by_length[length_key]["correct"] += 1

        noise_key = f"{int(tc.drop_rate * 100)}%"
        result.by_noise[noise_key]["total"] += 1
        if correct:
            result.by_noise[noise_key]["correct"] += 1

        result.by_show[show_name]["total"] += 1
        if correct:
            result.by_show[show_name]["correct"] += 1


# %% Results Display
# ─────────────────────────────────────────────────────────────────────────────


def fmt_pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def print_table(headers: list[str], rows: list[list[str]], title: str = ""):
    """Print a formatted ASCII table."""
    if title:
        print(f"\n{'=' * 90}")
        print(f"  {title}")
        print(f"{'=' * 90}")

    # compute column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    # header
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(f"  {header_line}")
    print(f"  {sep_line}")

    # rows
    for row in rows:
        line = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
        print(f"  {line}")


def display_results(result: AlgorithmResult):
    """Print all result tables."""

    # ── Overall Summary ──
    print_table(
        ["Metric", "Value"],
        [
            ["Total Tests", str(result.total)],
            ["Correct", str(result.correct)],
            ["Accuracy", fmt_pct(result.accuracy)],
            ["Precision", fmt_pct(result.precision)],
            ["Recall", fmt_pct(result.recall)],
            ["Avg Query Time", f"{result.avg_time_ms:.2f} ms"],
            ["Median Confidence (correct)", fmt_pct(result.median_confidence_correct)],
            ["Median Score Gap (top1-top2)", f"{result.median_score_gap:.4f}"],
        ],
        "OVERALL RESULTS — TF-IDF Cosine Similarity",
    )

    # ── Confidence Distribution ──
    print_table(
        ["Metric", "Min", "25th %ile", "Median", "75th %ile", "Max"],
        [
            _percentile_row("Correct", result.confidences_correct),
            _percentile_row("Wrong", result.confidences_wrong),
        ],
        "CONFIDENCE DISTRIBUTION",
    )

    # ── Score Gap Distribution ──
    print_table(
        ["Metric", "Min", "25th %ile", "Median", "75th %ile", "Max"],
        [
            _percentile_row("Correct", result.score_gaps_correct),
            _percentile_row("Wrong", result.score_gaps_wrong),
        ],
        "SCORE GAP (top-1 minus top-2) — larger = more decisive",
    )

    # ── By Show ──
    headers_show = ["Show", "Accuracy", "Correct/Total"]
    rows_show = []
    for show in sorted(result.by_show.keys()):
        d = result.by_show[show]
        acc = d["correct"] / d["total"] if d["total"] else 0
        rows_show.append([show, fmt_pct(acc), f"{d['correct']}/{d['total']}"])
    print_table(headers_show, rows_show, "ACCURACY BY SHOW")

    # ── By Chunk Length ──
    lengths = sorted(result.by_length.keys(), key=lambda x: int(x.rstrip("s")))
    headers_len = ["Chunk Length", "Accuracy", "Correct/Total"]
    rows_len = []
    for l in lengths:
        d = result.by_length[l]
        acc = d["correct"] / d["total"] if d["total"] else 0
        rows_len.append([l, fmt_pct(acc), f"{d['correct']}/{d['total']}"])
    print_table(headers_len, rows_len, "ACCURACY BY CHUNK LENGTH")

    # ── By Noise Level ──
    noises = sorted(result.by_noise.keys(), key=lambda x: int(x.rstrip("%")))
    headers_noise = ["Noise (word drop %)", "Accuracy", "Correct/Total"]
    rows_noise = []
    for n_lvl in noises:
        d = result.by_noise[n_lvl]
        acc = d["correct"] / d["total"] if d["total"] else 0
        rows_noise.append([n_lvl, fmt_pct(acc), f"{d['correct']}/{d['total']}"])
    print_table(headers_noise, rows_noise, "ACCURACY BY NOISE LEVEL")

    # ── Hardest Episodes ──
    episode_failures = []
    for ep_key, d in result.per_episode.items():
        if d["total"] > 0:
            acc = d["correct"] / d["total"]
            episode_failures.append((ep_key, acc, d["correct"], d["total"]))
    episode_failures.sort(key=lambda x: x[1])

    print_table(
        ["Episode", "Accuracy", "Correct/Total"],
        [[ep, fmt_pct(acc), f"{c}/{t}"] for ep, acc, c, t in episode_failures[:15]],
        "HARDEST EPISODES (lowest accuracy, showing bottom 15)",
    )

    # ── Confusion Analysis ──
    if result.confusion:
        confusion_rows = sorted(result.confusion.items(), key=lambda x: x[1], reverse=True)
        print_table(
            ["True Episode", "Predicted", "Count"],
            [
                [f"E{true:02d}", f"E{pred:02d}", str(count)]
                for (true, pred), count in confusion_rows[:20]
            ],
            "TOP CONFUSIONS (True → Predicted, showing top 20)",
        )

    # ── Speed ──
    if result.total > 0:
        qps = 1000.0 / result.avg_time_ms if result.avg_time_ms > 0 else float("inf")
        print_table(
            ["Metric", "Value"],
            [
                ["Total Time", f"{result.total_time_ms:.0f} ms"],
                ["Avg per Query", f"{result.avg_time_ms:.3f} ms"],
                ["Queries/sec", f"{qps:.0f}"],
            ],
            "SPEED",
        )


def _percentile_row(label: str, values: list[float]) -> list[str]:
    """Generate a percentile summary row."""
    if not values:
        return [label, "N/A", "N/A", "N/A", "N/A", "N/A"]
    s = sorted(values)
    n = len(s)
    return [
        label,
        f"{s[0]:.4f}",
        f"{s[n // 4]:.4f}",
        f"{s[n // 2]:.4f}",
        f"{s[3 * n // 4]:.4f}",
        f"{s[-1]:.4f}",
    ]


# %% Real-World Discrepancy Analysis
# ─────────────────────────────────────────────────────────────────────────────


def analyze_real_world_discrepancy(episodes: dict[int, Episode]):
    """
    Analyze why real-world disc rip results differ from subtitle-only tests.

    This simulates the specific conditions of real-world matching:
    1. Only 30s chunks (not 60s/120s)
    2. Chunks from specific time offsets (30s, 150s, 270s...) — the production skip pattern
    3. Higher noise levels typical of Whisper ASR output
    """
    print(f"\n{'=' * 90}")
    print("  REAL-WORLD DISCREPANCY ANALYSIS — Arrested Development S01")
    print(f"{'=' * 90}")

    if not episodes:
        print("  [SKIP] Arrested Development episodes not loaded")
        return

    algo = TfidfCosineAlgorithm()
    references = {ep: data.full_text for ep, data in episodes.items()}
    algo.prepare(references)

    # Test with production-style 30s chunks at specific offsets
    production_offsets = [30, 150, 270, 390, 510]  # matches production skip pattern
    chunk_len = 30

    print("\n  Testing with production-style sampling:")
    print(f"    Chunk length: {chunk_len}s")
    print(f"    Offsets: {production_offsets}")
    print("    Noise: 10% word drop + 3% substitution + ASR noise\n")

    results_by_ep = {}

    for ep_num, ep in sorted(episodes.items()):
        votes = defaultdict(float)  # ep → total cosine score
        correct_votes = 0
        total_votes = 0

        for offset in production_offsets:
            if offset + chunk_len > ep.duration:
                continue

            chunk = extract_chunk(ep, offset, chunk_len)
            if len(chunk.split()) < 5:
                continue

            # Apply realistic noise
            noisy = add_noise(chunk, drop_rate=0.10, sub_rate=0.03)

            # Match
            all_scores = algo.match_all(noisy)
            pred_ep, score = all_scores[0]
            all_scores[0][1] - all_scores[1][1] if len(all_scores) >= 2 else 0

            votes[pred_ep] += score
            total_votes += 1
            if pred_ep == ep_num:
                correct_votes += 1

        # Determine final vote
        if votes:
            winner = max(votes, key=votes.get)
            final_correct = winner == ep_num
        else:
            winner = -1
            final_correct = False

        results_by_ep[ep_num] = {
            "winner": winner,
            "correct": final_correct,
            "chunk_correct": correct_votes,
            "chunk_total": total_votes,
        }

        status = "[OK]" if final_correct else "[X]"
        chunk_str = f"{correct_votes}/{total_votes} chunks"
        print(f"  E{ep_num:02d}: {status} -> predicted E{winner:02d} ({chunk_str})")

    # Summary
    correct = sum(1 for r in results_by_ep.values() if r["correct"])
    total = len(results_by_ep)
    print(f"\n  Production-style accuracy: {correct}/{total} ({100 * correct / total:.1f}%)")

    # Compare with clean-text accuracy
    print("\n  Factors that reduce real-world accuracy:")
    print("    • ASR transcription noise (Whisper errors)")
    print("    • Fixed 30s chunks (vs variable lengths in ideal test)")
    print("    • Chunks from specific offsets (may hit music/silence)")
    print("    • Subtitle timing mismatches with disc chapter points")
    print("    • Background audio: music, sound effects, laugh tracks")


# %% Main
# ─────────────────────────────────────────────────────────────────────────────


def main():
    print("=" * 90)
    print("  TRANSCRIPT MATCHING -- MULTI-SHOW BENCHMARK")
    print("  TF-IDF Cosine Similarity Deep Dive")
    print("=" * 90)

    # Load all shows
    print("\n[1/4] Loading subtitle references...")
    all_shows = load_all_shows()

    total_episodes = sum(len(eps) for eps in all_shows.values())
    print(f"\n  Total: {len(all_shows)} shows, {total_episodes} episodes")

    # Generate test cases for all shows
    print("\n[2/4] Generating test cases...")
    all_test_cases = []
    for show_name, episodes in all_shows.items():
        cases = generate_test_cases(show_name, episodes)
        all_test_cases.extend(cases)
        print(f"  {show_name}: {len(cases)} test cases")

    random.shuffle(all_test_cases)
    print(f"\n  Total: {len(all_test_cases)} test cases")

    # Run benchmark per show (each show has its own TF-IDF model)
    print("\n[3/4] Running benchmark...")
    overall_result = AlgorithmResult(algorithm="TF-IDF Cosine")

    for show_name, episodes in all_shows.items():
        print(f"\n  Benchmarking {show_name}...")

        # Prepare TF-IDF for this show
        algo = TfidfCosineAlgorithm()
        references = {ep: data.full_text for ep, data in episodes.items()}
        algo.prepare(references)

        # Filter test cases for this show
        show_cases = [tc for tc in all_test_cases if tc.show == show_name]

        # Run
        t0 = time.perf_counter()
        run_bench_for_show(algo, show_cases, overall_result, show_name)
        elapsed = (time.perf_counter() - t0) * 1000
        show_d = overall_result.by_show[show_name]
        show_acc = show_d["correct"] / show_d["total"] if show_d["total"] else 0
        print(
            f"    → {fmt_pct(show_acc)} accuracy ({show_d['correct']}/{show_d['total']}) "
            f"in {elapsed:.0f}ms"
        )

    # Display results
    print("\n[4/4] Results...")
    display_results(overall_result)

    # Real-world discrepancy analysis for Arrested Development
    ad_key = next((k for k in all_shows if k.startswith("Arrested")), None)
    if ad_key:
        analyze_real_world_discrepancy(all_shows[ad_key])

    print("\n" + "=" * 90)
    print("  Benchmark complete!")
    print("=" * 90)


if __name__ == "__main__":
    main()
