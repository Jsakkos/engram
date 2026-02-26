# Episode Matching Investigation Scripts

This directory contains scripts to investigate and improve the episode matching system in Engram. The investigation uses a **two-phase approach**: transcription (slow, cached) and matching evaluation (fast, iterative).

## Overview

The investigation addresses the current system's tendency to fall back to full-file transcription by:

1. **Generating complete coverage transcriptions** (every 30s, no gaps) for test files
2. **Testing multiple matching algorithms** with the cached transcriptions
3. **Comparing performance metrics** to identify the best approach

## Quick Start

### Phase 1: Generate Transcription Data (~30-40 minutes for subset)

Process a subset of test episodes with complete chunk coverage:

```bash
cd backend
uv run python -m app.matcher.scripts.generate_investigation_data --subset
```

This processes:
- **Arrested Development**: Episodes 1-5 (~110 minutes, ~220 chunks)
- **The Expanse**: Episodes 1-3 (~135 minutes, ~270 chunks)

**Output:**
- `investigation_output/transcriptions/{show}/Season {season}/S{season}E{episode}.json`
- `investigation_output/transcription_index.json`
- `investigation_output/references/{show}_S{season}.json`

### Phase 2: Run Matching Evaluation (~2-5 minutes)

Test all matching methods using cached transcriptions:

```bash
uv run python -m app.matcher.scripts.evaluate_matching_methods
```

**Output:**
- `investigation_output/matching_results.csv`

This runs **fast** because transcriptions are cached. You can iterate on matching algorithms without re-transcribing.

### Phase 3: Export Results for Analysis (~1 minute)

Generate human-readable reports:

```bash
uv run python -m app.matcher.scripts.export_investigation_results
```

**Output:**
- `investigation_output/analysis/master_dataset.csv` - Chunk-level detail
- `investigation_output/analysis/method_comparison.csv` - Summary table
- `investigation_output/analysis/error_analysis.md` - Detailed error breakdown
- `investigation_output/analysis/visualization_data.json` - Chart data

## Scripts

### 1. `generate_investigation_data.py`

Generates complete transcription data with every 30-second chunk transcribed.

**Usage:**

```bash
# Subset (default: 5 Arrested Dev + 3 Expanse)
uv run python -m app.matcher.scripts.generate_investigation_data --subset

# All files in test directory
uv run python -m app.matcher.scripts.generate_investigation_data --all

# Specific show
uv run python -m app.matcher.scripts.generate_investigation_data \
    --show "Arrested Development"

# Specific episodes
uv run python -m app.matcher.scripts.generate_investigation_data \
    --show "Arrested Development" --episodes 1-5

# Single episode
uv run python -m app.matcher.scripts.generate_investigation_data \
    --show "The Expanse" --episodes 3

# Force re-processing (ignore cache)
uv run python -m app.matcher.scripts.generate_investigation_data --subset --force

# Custom paths
uv run python -m app.matcher.scripts.generate_investigation_data \
    --test-dir "D:\Videos\Tests" \
    --output-dir "custom_output"
```

**What it does:**

1. Discovers `.mkv` files in test directory
2. Extracts audio chunks every 30 seconds (complete coverage)
3. Transcribes each chunk with Faster-Whisper "small" model
4. Caches results to JSON files (one per episode)
5. Loads reference subtitles from subtitle cache
6. Creates index for fast lookup

**Processing Time:**
- **Subset**: ~30-40 minutes (8 episodes)
- **Full season**: ~2-3 hours per season (~22 episodes)
- **Per episode**: ~3-5 minutes (depends on duration)

**Cache Structure:**
```
investigation_output/
├── transcriptions/
│   ├── Arrested Development/
│   │   └── Season 01/
│   │       ├── S01E01.json
│   │       ├── S01E02.json
│   │       └── ...
│   └── The Expanse/
│       └── Season 01/
│           └── ...
├── references/
│   ├── Arrested Development_S01.json
│   └── The Expanse_S01.json
└── transcription_index.json
```

### 2. `evaluate_matching_methods.py`

Evaluates multiple matching algorithms using cached transcription data.

**Usage:**

```bash
# Run all methods with default thresholds
uv run python -m app.matcher.scripts.evaluate_matching_methods

# Run specific methods
uv run python -m app.matcher.scripts.evaluate_matching_methods \
    --methods ranked_voting sparse_sampling

# Test specific thresholds
uv run python -m app.matcher.scripts.evaluate_matching_methods \
    --thresholds 0.1 0.15 0.2 0.25

# Custom paths
uv run python -m app.matcher.scripts.evaluate_matching_methods \
    --transcriptions investigation_output/transcriptions \
    --references investigation_output/references \
    --output results.csv
```

**Matching Methods Tested:**

1. **sparse_sampling** (baseline)
   - Simulates current production behavior
   - Dense (≤900s): every 30s chunk
   - Sparse (>900s): every 150s (every 5th chunk)
   - Early exit on confidence > 0.92
   - Weighted score: avg_confidence × file_coverage

2. **complete_coverage**
   - Uses ALL chunks (every 30s)
   - Same weighted scoring as sparse
   - No early exit

3. **ranked_voting**
   - Ranked-choice voting with weighted confidence
   - Each chunk votes for all matching episodes
   - Winner: highest `sum(confidence × chunk_weight) / sum(chunk_weights)`

4. **simple_vote**
   - Unweighted vote count
   - Each chunk match = 1 vote
   - Winner: most votes

5. **alt_similarity_balanced** (50% token_sort + 50% partial)
6. **alt_similarity_pure_token_sort** (100% token_sort)
7. **alt_similarity_token_set_heavy** (80% token_set + 20% partial)

**Metrics Calculated:**

- **Accuracy**: % of files matched to correct episode
- **Precision**: % of confident matches that were correct
- **Recall**: % of files that got a confident match (vs. fallback)
- **Fallback Rate**: % requiring full-file transcription
- **Average Confidence**: Mean confidence for matches
- **Average Processing Time**: Time per file
- **Chunk Utilization**: % of chunks contributing to match

**Output:**

CSV with columns:
- `method_name`, `file_path`, `show_name`, `season`
- `episode_actual`, `episode_matched`, `confidence`
- `correct`, `processing_time`, `chunks_used`, `total_chunks`
- `weighted_score`, `fallback_used`

### 3. `export_investigation_results.py`

Generates human-readable exports for manual review.

**Usage:**

```bash
# Default
uv run python -m app.matcher.scripts.export_investigation_results

# Custom paths
uv run python -m app.matcher.scripts.export_investigation_results \
    --input investigation_output/matching_results.csv \
    --transcriptions investigation_output/transcriptions \
    --output-dir investigation_output/analysis
```

**Generated Files:**

1. **master_dataset.csv**
   - One row per chunk
   - Columns: file, show, season, episode, chunk_index, start_time, transcription
   - Enables Excel pivot analysis

2. **method_comparison.csv**
   - One row per method
   - Columns: method, accuracy, precision, recall, fallback_rate, avg_confidence, avg_time

3. **error_analysis.md**
   - Markdown report with:
     - Incorrect matches by method
     - Sample transcriptions from confused chunks
     - Confusion matrix (which episodes get confused)
     - Pattern analysis

4. **visualization_data.json**
   - JSON for creating charts:
     - Method comparison metrics
     - Confidence distributions (correct vs. incorrect)
     - Chunk utilization rates

## Workflow

### Initial Investigation (Subset)

```bash
# 1. Generate transcriptions (~30-40 min)
uv run python -m app.matcher.scripts.generate_investigation_data --subset

# 2. Run matching evaluation (~2-5 min)
uv run python -m app.matcher.scripts.evaluate_matching_methods

# 3. Export results (~1 min)
uv run python -m app.matcher.scripts.export_investigation_results

# 4. Review results
cat investigation_output/analysis/method_comparison.csv
cat investigation_output/analysis/error_analysis.md
```

### Iterate on Matching Algorithms (Fast)

After initial transcription, you can quickly test new matching approaches:

```bash
# Edit evaluate_matching_methods.py to add new method

# Re-run evaluation (~2-5 min, uses cached transcriptions)
uv run python -m app.matcher.scripts.evaluate_matching_methods

# Re-export results (~1 min)
uv run python -m app.matcher.scripts.export_investigation_results
```

### Expand to Full Dataset

```bash
# Process all episodes (~2-3 hours per season)
uv run python -m app.matcher.scripts.generate_investigation_data --all

# Run full evaluation
uv run python -m app.matcher.scripts.evaluate_matching_methods
```

## Expected Results

Based on the plan hypothesis:

1. **H1**: Complete coverage will outperform sparse sampling
   - Compare `sparse_sampling` vs. `complete_coverage`

2. **H2**: Ranked-choice voting will be more accurate than early-exit
   - Compare `complete_coverage` vs. `ranked_voting`

3. **H3**: Alternative similarity metrics may improve quality
   - Compare different `alt_similarity_*` methods

4. **H4**: Threshold 0.15 is too conservative
   - Check fallback rates across different thresholds

**Success Criteria:**
- At least one method achieves >95% accuracy on test files
- Identify root cause of high fallback rate
- Provide data-driven recommendation for production improvements

## Troubleshooting

### Transcription Errors

```bash
# Check which files failed
cat investigation_output/transcription_index.json

# Re-process specific file
uv run python -m app.matcher.scripts.generate_investigation_data \
    --show "Arrested Development" --episodes 1 --force
```

### Missing Reference Subtitles

If you see "No subtitle cache found":

1. Ensure subtitles are downloaded to `~/.engram/cache/data/{show}/`
2. Check subtitle cache in Engram config
3. Manually download subtitles using the web UI or API

### Faster-Whisper Model Issues

If you get model loading errors:

```bash
# Ensure model is cached
python -c "from app.matcher.asr_models import get_cached_model; import asyncio; asyncio.run(get_cached_model('small'))"
```

### Low Accuracy Results

If all methods show low accuracy:

1. Check transcription quality in JSON files
2. Verify reference subtitles are correct
3. Ensure test files are correctly named (S01E01 format)
4. Check for language mismatches

## File Naming Requirements

Test files must follow this naming convention:

- `{Show Name} - S{season}E{episode}.mkv`
- Or organized as: `{Show Name}/Season {season}/{Show} - S{season}E{episode}.mkv`

Examples:
- ✅ `Arrested Development - S01E02.mkv`
- ✅ `The Expanse/Season 01/The Expanse - S01E03.mkv`
- ❌ `arrested_dev_1x02.mkv` (won't be parsed)

## Next Steps

After completing the investigation:

1. **Review results**: Check `method_comparison.csv` and `error_analysis.md`
2. **Identify best method**: Look for highest accuracy + lowest fallback rate
3. **Update production code**: Apply findings to `episode_identification.py`
4. **Document learnings**: Update CLAUDE.md with insights
5. **Run regression tests**: Ensure changes don't break existing functionality

## Data Privacy

These scripts process local files only. No data is sent to external services except:
- Faster-Whisper ASR (runs locally, no network)
- RapidFuzz matching (local library)

Transcription cache files contain:
- File paths (local only)
- Transcribed text from audio
- No personal information

**Important**: Do not commit `investigation_output/` to git as it may contain copyrighted content (episode transcriptions).
