# Episode Matching Investigation - Implementation Summary

## ✅ Completed Implementation

I've successfully implemented all three phases of the episode matching investigation plan:

### Phase 1: Data Generation Script ✅
**File**: `generate_investigation_data.py` (~540 lines)

**Features**:
- Discovers `.mkv` files in test directory with flexible filtering
- Generates complete chunk coverage (every 30 seconds, no gaps)
- Transcribes each chunk using Faster-Whisper "small" model (CPU mode)
- Caches results to JSON files (one per episode) for fast iteration
- Loads reference subtitles from subtitle cache
- Supports subset processing (5 Arrested Dev + 3 Expanse = ~30-40 min)
- Supports full dataset processing (all files in test directory)
- Resume capability (skips already-processed files)
- Progress tracking with tqdm

**Usage**:
```bash
# Subset (default)
uv run python -m app.matcher.scripts.generate_investigation_data --subset

# All files
uv run python -m app.matcher.scripts.generate_investigation_data --all

# Specific show/episodes
uv run python -m app.matcher.scripts.generate_investigation_data \
    --show "Arrested Development" --episodes 1-5

# Force re-processing
uv run python -m app.matcher.scripts.generate_investigation_data --subset --force
```

**Output Structure**:
```
investigation_output/
├── transcriptions/
│   ├── {show}/
│   │   └── Season {season}/
│   │       └── S{season}E{episode}.json  # Chunks with transcriptions
│   └── ...
├── references/
│   └── {show}_S{season}.json  # Reference subtitle data
└── transcription_index.json  # Index of all processed files
```

### Phase 2: Matching Evaluation Script ✅
**File**: `evaluate_matching_methods.py` (~650 lines)

**Features**:
- Loads cached transcriptions (fast - ~2-5 minutes total)
- Tests 7 matching methods:
  1. **sparse_sampling** - Current production behavior (baseline)
  2. **complete_coverage** - All chunks with current scoring
  3. **ranked_voting** - Weighted confidence voting algorithm
  4. **simple_vote** - Unweighted vote count
  5. **alt_similarity_balanced** - 50/50 token_sort/partial
  6. **alt_similarity_pure_token_sort** - 100% token_sort
  7. **alt_similarity_token_set_heavy** - 80/20 token_set/partial
- Tests multiple confidence thresholds (0.1, 0.15, 0.2, 0.25, 0.3)
- Calculates comprehensive metrics per method:
  - Accuracy, precision, recall
  - Fallback rate
  - Average confidence, processing time
  - Chunk utilization

**Usage**:
```bash
# Run all methods
uv run python -m app.matcher.scripts.evaluate_matching_methods

# Run specific methods
uv run python -m app.matcher.scripts.evaluate_matching_methods \
    --methods ranked_voting sparse_sampling

# Test specific thresholds
uv run python -m app.matcher.scripts.evaluate_matching_methods \
    --thresholds 0.1 0.15 0.2
```

**Output**: CSV file with per-file, per-method, per-threshold results

### Phase 3: Export & Analysis Script ✅
**File**: `export_investigation_results.py` (~400 lines)

**Features**:
- Generates multiple export formats from matching results
- **master_dataset.csv** - Chunk-level detailed data for Excel analysis
- **method_comparison.csv** - Summary table comparing all methods
- **error_analysis.md** - Markdown report with error breakdown
- **visualization_data.json** - Data for creating charts/graphs
- Analyzes confusion patterns (which episodes get confused)
- Identifies files with most errors
- Console output with summary statistics

**Usage**:
```bash
uv run python -m app.matcher.scripts.export_investigation_results \
    --input investigation_output/matching_results.csv
```

**Output**: Multiple files in `investigation_output/analysis/`

### Documentation ✅
**File**: `README.md` (~480 lines)

Comprehensive documentation including:
- Quick start guide (3-step workflow)
- Detailed script descriptions
- All command-line options with examples
- Processing time estimates
- Troubleshooting section
- File naming requirements
- Expected results and hypothesis testing
- Workflow for iterating on matching algorithms

## 🔧 Technical Details

### Key Improvements Made

1. **Two-Phase Architecture**: Separate transcription (slow, cached) from matching (fast, iterative)
   - Enables rapid iteration on matching algorithms without re-transcribing
   - Cache stored as JSON files per episode for easy inspection

2. **Fixed Import Issues**:
   - Removed non-existent `extract_subtitle_text` import
   - Imported `get_video_duration` and `extract_audio_chunk` from `core.utils`
   - Fixed `get_cached_model` to use dict config instead of string
   - Changed async functions to sync where appropriate

3. **Model Configuration**:
   - Forced CPU mode for Faster-Whisper to avoid CUDA library issues
   - Uses "small" model for balance of speed and accuracy

4. **Data Structures**:
   - Created dataclasses for clean data handling
   - ChunkData, FileData, ReferenceEpisode, ReferenceData
   - Easy serialization to/from JSON

### Matching Methods Implemented

1. **SparseMethod** - Simulates current production behavior
   - Dense (≤900s): uses all chunks
   - Sparse (>900s): every 5th chunk (150s intervals)
   - Early exit on confidence > 0.92
   - Weighted score: avg_confidence × file_coverage

2. **CompleteCoverageMethod** - Uses all chunks, no early exit
   - Same weighted scoring as sparse
   - Tests if complete coverage improves accuracy

3. **RankedVotingMethod** - Novel voting algorithm
   - Each chunk votes for all matching episodes (score > 0.6)
   - Winner: highest `sum(confidence × chunk_weight) / sum(weights)`
   - No early exit, considers all evidence

4. **SimpleVoteMethod** - Unweighted democracy
   - Each chunk match = 1 vote
   - Winner: most votes
   - Tie-breaker: highest average confidence

5-7. **Alternative Similarity Methods** - Test different RapidFuzz algorithm weights

### Evaluation Metrics

For each method:
- **Accuracy**: % of files matched to correct episode (ground truth from filename)
- **Precision**: % of confident matches that were correct
- **Recall**: % of files that got a confident match (vs. fallback)
- **Fallback Rate**: % of files requiring full-file transcription
- **Average Confidence**: Mean confidence for matches
- **Average Processing Time**: Time per file
- **Chunk Utilization**: % of chunks contributing to final decision

## 📊 Expected Workflow

### 1. Initial Investigation (Subset - ~35 minutes total)

```bash
# Phase 1: Generate transcriptions (~30-40 min)
uv run python -m app.matcher.scripts.generate_investigation_data --subset

# Phase 2: Run matching evaluation (~2-5 min)
uv run python -m app.matcher.scripts.evaluate_matching_methods

# Phase 3: Export results (~1 min)
uv run python -m app.matcher.scripts.export_investigation_results

# Review results
cat investigation_output/analysis/method_comparison.csv
cat investigation_output/analysis/error_analysis.md
```

### 2. Iterate on Algorithms (Fast - ~5 minutes)

```bash
# Edit evaluate_matching_methods.py to add new method or adjust weights

# Re-run evaluation (uses cached transcriptions)
uv run python -m app.matcher.scripts.evaluate_matching_methods

# Re-export
uv run python -m app.matcher.scripts.export_investigation_results
```

### 3. Expand to Full Dataset (if needed)

```bash
# Process all episodes (~2-3 hours per season)
uv run python -m app.matcher.scripts.generate_investigation_data --all

# Run full evaluation
uv run python -m app.matcher.scripts.evaluate_matching_methods
```

## 🎯 Success Criteria

From the plan:

1. ✅ **H1**: Complete coverage will outperform sparse sampling
   - Compare `sparse_sampling` vs. `complete_coverage` accuracy

2. ✅ **H2**: Ranked-choice voting will be more accurate than early-exit
   - Compare `complete_coverage` vs. `ranked_voting`

3. ✅ **H3**: Alternative similarity metrics may improve quality
   - Compare different `alt_similarity_*` methods

4. ✅ **H4**: Threshold 0.15 is too conservative
   - Measure fallback rates at different thresholds

**Target**: At least one method achieves >95% accuracy on test files

## 🔄 Next Steps

After running the investigation:

1. **Review Results**: Check `method_comparison.csv` for highest accuracy
2. **Analyze Errors**: Review `error_analysis.md` for patterns
3. **Test Thresholds**: Find optimal confidence threshold
4. **Update Production**: Apply findings to `episode_identification.py`
5. **Document Learnings**: Update CLAUDE.md with insights
6. **Run Tests**: Ensure changes don't break existing functionality

## 📁 Files Created

- ✅ `generate_investigation_data.py` - Phase 1 (transcription)
- ✅ `evaluate_matching_methods.py` - Phase 2 (matching)
- ✅ `export_investigation_results.py` - Phase 3 (analysis)
- ✅ `README.md` - Comprehensive documentation
- ✅ `IMPLEMENTATION_SUMMARY.md` - This file
- ✅ `__init__.py` - Package marker

## 🐛 Known Issues & Fixes

### Issue 1: CUDA Library Error ✅ FIXED
- **Error**: `RuntimeError: Library cublas64_12.dll is not found`
- **Cause**: Faster-Whisper trying to use GPU without CUDA libraries
- **Fix**: Force CPU mode in model config: `"device": "cpu"`

### Issue 2: Import Errors ✅ FIXED
- **Error**: `cannot import name 'extract_subtitle_text'`
- **Cause**: Function doesn't exist in `core.utils`
- **Fix**: Manually parse SRT files using `SubtitleReader` methods

### Issue 3: Async/Sync Mismatch ✅ FIXED
- **Error**: `await get_cached_model("small")`
- **Cause**: `get_cached_model` is sync, not async; expects dict config
- **Fix**: Remove `await` and pass proper dict config

## 🎉 Implementation Status: COMPLETE

All three scripts are implemented, tested for basic functionality, and fully documented. Ready for user to run the investigation workflow.

**Estimated Total Time**:
- Subset investigation: ~35-45 minutes
- Full season investigation: ~2.5-3.5 hours per season
- Algorithm iteration: ~5 minutes per iteration
