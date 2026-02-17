# Matching Performance Analysis Test Bench

Comprehensive benchmarking tool for analyzing the performance, resource usage, and accuracy of the episode matching system.

## Overview

The test bench evaluates the matching pipeline across multiple dimensions:
- **Models**: tiny, base, small Whisper models
- **Devices**: CPU and CUDA (if available)
- **Cache states**: warm (subtitles pre-downloaded) and cold (fresh download)

It collects:
- Per-stage timing metrics
- Resource usage (CPU, memory, GPU)
- Matching accuracy (if ground truth provided)
- Detailed profiling data

## Setup

### 1. Install Dependencies

Dependencies are already included in `pyproject.toml`:
```bash
cd backend
uv sync
```

### 2. Prepare Test Data

Your test data should be organized as:
```
C:\Media\Tests\
├── Arrested Development\
│   └── Season 1\
│       ├── 01.mkv
│       ├── 02.mkv
│       └── ...
└── The Expanse\
    └── Season 1\
        ├── 23.mkv
        ├── 24.mkv
        └── ...
```

### 3. Generate Ground Truth Template (Optional)

For accuracy testing, generate and populate the ground truth file:

```bash
python scripts/ground_truth_template.py
```

This creates `scripts/ground_truth.json`. Edit this file and replace `"UNKNOWN"` values with correct episode codes (e.g., `"S01E01"`).

**Example ground truth entry:**
```json
{
  "Arrested Development": {
    "Season 1": {
      "season": 1,
      "episodes": {
        "01.mkv": "S01E01",
        "02.mkv": "S01E02",
        "03.mkv": "S01E03"
      }
    }
  }
}
```

If you skip this step, the test bench will run in **performance-only mode** (no accuracy metrics).

### 4. Ensure Subtitle Cache Exists (Optional)

For warm cache tests, ensure subtitles are pre-downloaded in `~/.uma/cache/data/`.

If the cache is empty, the "warm" cache tests will behave like "cold" tests.

## Usage

### Basic Usage

```bash
# Full test (all configurations, all files)
python scripts/matching_test_bench.py

# Dry-run (preview what would be tested)
python scripts/matching_test_bench.py --dry-run

# Quick test (5 files only)
python scripts/matching_test_bench.py --limit 5
```

### Configuration Options

```bash
# Test specific models
python scripts/matching_test_bench.py --models tiny,base

# Test specific cache state
python scripts/matching_test_bench.py --cache warm

# Test specific device
python scripts/matching_test_bench.py --device cpu

# Test specific show
python scripts/matching_test_bench.py --show "Arrested Development"

# Combine options
python scripts/matching_test_bench.py --models tiny --device cpu --cache warm --limit 10
```

### Advanced Options

```bash
# Skip resource monitoring (faster, less data)
python scripts/matching_test_bench.py --no-resource-monitoring

# Custom output directory
python scripts/matching_test_bench.py --output-dir ./my_results

# Custom test data location
python scripts/matching_test_bench.py --test-dir "D:\TestMedia"

# Custom cache directory
python scripts/matching_test_bench.py --cache-dir "D:\uma-cache"

# Verbose logging
python scripts/matching_test_bench.py --verbose
```

## Output

The test bench generates three types of output:

### 1. Console Output

Real-time progress bars and summary tables showing:
- Configuration ID (e.g., `tiny_cpu_warm`)
- Number of tests run
- Average time per file
- Error count
- Accuracy rate (if ground truth available)

### 2. CSV Report

File: `test_bench_results/test_bench_results_YYYYMMDD_HHMMSS.csv`

One row per test with columns:
- Configuration details (model, device, cache state)
- File information (show, season, filename)
- Timing metrics (total time, model load time, per-stage timings)
- Matching results (predicted episode, confidence, score)
- Resource usage (CPU, memory, GPU)
- Accuracy (if ground truth available)

**Use for:** Detailed analysis in Excel, Pandas, or other data analysis tools.

### 3. JSON Report

File: `test_bench_results/test_bench_results_YYYYMMDD_HHMMSS.json`

Structured report with:
- Metadata (timestamp, test configuration, system info)
- Configuration summaries (avg time, accuracy rate, error count)
- Recommendations (fastest, most accurate, best balanced)
- Detailed results for every test

**Use for:** Programmatic analysis, archiving, comparison across test runs.

## Example Workflow

### Quick Performance Test

Test a few files with the tiny model to get baseline performance:

```bash
python scripts/matching_test_bench.py --limit 5 --models tiny --device cpu --cache warm
```

Expected output:
```
Running 5 tests (5 files × 1 configs)

┌─────────────┬───────┬──────────────┬────────┬──────────┐
│ Config      │ Tests │ Avg Time (s) │ Errors │ Accuracy │
├─────────────┼───────┼──────────────┼────────┼──────────┤
│ tiny_cpu... │ 5     │ 45.2         │ 0      │ N/A      │
└─────────────┴───────┴──────────────┴────────┴──────────┘

Reports saved to: backend/scripts/test_bench_results
```

### Full Comparison Test

Compare all models (tiny, base, small) on CPU with warm cache:

```bash
python scripts/matching_test_bench.py --models tiny,base,small --device cpu --cache warm
```

This will test all 31 files × 3 models = 93 tests.
Estimated time: 1-2 hours.

### Accuracy Test (with Ground Truth)

1. Populate `ground_truth.json` with correct episode codes
2. Run the test bench:

```bash
python scripts/matching_test_bench.py
```

The output will include accuracy metrics showing which model performs best.

### GPU vs CPU Comparison

If you have CUDA available:

```bash
python scripts/matching_test_bench.py --models base --device both --limit 10
```

This tests the same model on both CPU and GPU to compare performance.

## Interpreting Results

### Timing Metrics

- **total_duration_ms**: End-to-end matching time for one file
- **model_load_ms**: Time to load the Whisper model (only first file)
- Look for bottlenecks: Is most time spent in audio extraction? Transcription? Subtitle loading?

### Resource Usage

- **avg_cpu_percent**: Average CPU utilization during matching
- **peak_memory_mb**: Peak memory usage
- **avg_gpu_percent**: Average GPU utilization (if CUDA available)
- High CPU but low GPU usage might indicate GPU isn't being fully utilized

### Accuracy Metrics

- **correct**: Boolean indicating if prediction matched ground truth
- **confidence**: Match confidence score (0.0-1.0)
- Look for patterns: Does a faster model have acceptable accuracy loss?

### Recommendations Section (JSON Report)

The JSON report includes automatic recommendations:
- **fastest**: Configuration with lowest average time
- **most_accurate**: Configuration with highest accuracy rate (if ground truth available)
- **best_balanced**: Configuration with best speed/accuracy trade-off

## Troubleshooting

### "No test files found"

- Verify test directory exists: `C:\Media\Tests`
- Check directory structure matches expected format (Show/Season N/files.mkv)
- Use `--test-dir` to specify custom location

### "Cache is empty, cannot warm"

- This is just a warning, tests will proceed as "cold" cache tests
- To fix: Run UMA normally to download subtitles first, or use `--cache cold`

### "CUDA not available, skipping GPU tests"

- CUDA device not detected or driver issue
- Tests will run on CPU only
- Use `--device cpu` to skip this check

### Tests are very slow

- Use `--limit 5` to test fewer files
- Use `--models tiny` to test only the fastest model
- Use `--no-resource-monitoring` to skip resource collection

### High memory usage

- The Whisper models stay loaded in memory
- Each model (tiny/base/small) requires different amounts of RAM
- Close other applications or use `--limit` to reduce load

## File Structure

```
backend/scripts/
├── README.md                          # This file
├── matching_test_bench.py             # Main test bench script
├── ground_truth_template.py           # Ground truth generator
├── ground_truth.json                  # Ground truth mappings (user-populated)
└── test_bench_results/                # Output directory
    ├── test_bench_results_20260208_141530.csv
    └── test_bench_results_20260208_141530.json
```

## Dependencies

All required dependencies are in `backend/pyproject.toml`:
- `psutil>=5.9.0` - CPU/memory monitoring
- `gputil>=1.4.0` - GPU monitoring (optional)
- `rich>=13.0.0` - Progress bars and tables (already present)
- `loguru` - Logging (already present via matcher)

## Next Steps

After running the test bench and analyzing results:

1. **Identify Bottlenecks**: Which stage takes the most time?
2. **Model Selection**: Can you use a smaller model with acceptable accuracy?
3. **Optimization Opportunities**: Should you invest in GPU? Reduce checkpoints?
4. **Re-benchmark**: After making changes, run the test bench again to measure impact

## Tips

- Start with a small test (`--limit 5`) to verify everything works
- Use `--dry-run` first to preview what will be tested
- Test one configuration at a time initially (`--models tiny --device cpu --cache warm`)
- Once comfortable, run full tests overnight (2-4 hours for all 31 files × all configs)
- Keep the CSV/JSON reports for historical comparison
- Use ground truth for the first ~10 episodes of each show, then skip for performance-only testing

## Support

For issues or questions:
- Check the main UMA documentation: `CLAUDE.md`
- Review test bench code: `scripts/matching_test_bench.py`
- File an issue in the UMA repository
