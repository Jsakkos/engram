# Test Bench Quick Start

## 60 Second Setup

```bash
cd backend

# 1. Sync dependencies
uv sync

# 2. Generate ground truth template (optional for accuracy testing)
uv run python scripts/ground_truth_template.py

# 3. (Optional) Edit scripts/ground_truth.json - replace "UNKNOWN" with correct episode codes

# 4. Run a quick test
uv run python scripts/matching_test_bench.py --limit 3 --models tiny --verbose
```

## Most Common Commands

```bash
# Preview what will be tested (no execution)
uv run python scripts/matching_test_bench.py --dry-run

# Quick test: 5 files, tiny model, CPU only, warm cache
uv run python scripts/matching_test_bench.py --limit 5 --models tiny --device cpu --cache warm

# Model comparison: all models, CPU only, 10 files
uv run python scripts/matching_test_bench.py --limit 10 --models tiny,base,small --device cpu

# Full test: all configurations, all files (2-4 hours)
uv run python scripts/matching_test_bench.py

# Performance-only (skip resource monitoring for speed)
uv run python scripts/matching_test_bench.py --no-resource-monitoring
```

## Output Files

Results saved to: `backend/scripts/test_bench_results/`

- `test_bench_results_YYYYMMDD_HHMMSS.csv` - Detailed metrics (Excel/Pandas)
- `test_bench_results_YYYYMMDD_HHMMSS.json` - Summary + recommendations

## Quick Interpretation

### Console Summary Table

```
Configuration   Tests  Avg Time (s)  Errors  Accuracy
tiny_cpu_warm   31     42.5          0       28/31 (90.3%)
base_cpu_warm   31     65.2          0       30/31 (96.8%)
small_cpu_warm  31     98.7          0       31/31 (100%)
```

- **Avg Time**: Lower is faster
- **Errors**: Should be 0 (or very low)
- **Accuracy**: Only shown if ground truth provided

### JSON Report Recommendations

Check `recommendations` section in JSON report:
- **fastest**: Best if you prioritize speed
- **most_accurate**: Best if you prioritize correctness
- **best_balanced**: Best overall trade-off

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No test files found" | Check `C:\Media\Tests` exists with Show/Season/files.mkv structure |
| "Cache is empty" | Normal if you haven't run UMA yet. Use `--cache cold` |
| Tests are slow | Use `--limit 5` to test fewer files |
| Out of memory | Close other apps, use `--models tiny`, or use `--limit` |
| CUDA not available | Normal if no GPU. Tests will use CPU only |

## Test Data Structure

Expected layout:
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
        └── ...
```

## Ground Truth Format

File: `scripts/ground_truth.json`

```json
{
  "Arrested Development": {
    "Season 1": {
      "season": 1,
      "episodes": {
        "01.mkv": "S01E01",
        "02.mkv": "S01E02"
      }
    }
  }
}
```

Replace `"UNKNOWN"` with correct episode codes for accuracy testing.

## Next Steps

1. Run a quick test (`--limit 3`) to verify it works
2. Populate ground truth for ~10 episodes per show
3. Run full test overnight
4. Analyze CSV in Excel or Pandas
5. Check JSON recommendations
6. Make optimization decisions based on data

## Full Documentation

See `scripts/README.md` for complete details.
