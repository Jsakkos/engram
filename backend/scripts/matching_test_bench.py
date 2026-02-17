"""
Matching Performance Analysis Test Bench

Comprehensive benchmarking tool for the episode matching system.
Tests multiple configurations (models, cache states, devices) and collects:
- Per-stage timing metrics
- Resource usage (CPU, memory, GPU)
- Matching accuracy (if ground truth provided)
- Detailed profiling data

Usage:
    python matching_test_bench.py                    # Full test
    python matching_test_bench.py --dry-run          # Preview
    python matching_test_bench.py --limit 5          # Test 5 files
    python matching_test_bench.py --models tiny,base # Specific models
    python matching_test_bench.py --verbose          # Debug output
"""

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import psutil
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
import psutil

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.matcher.asr_models import get_cached_model
from app.matcher.episode_identification import EpisodeMatcher

console = Console()

# Try to import GPUtil for GPU monitoring
try:
    import GPUtil

    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

# Default test data path
DEFAULT_TEST_DIR = Path(r"C:\Media\Tests")
DEFAULT_CACHE_DIR = Path.home() / ".uma" / "cache"


@dataclass
class ResourceSnapshot:
    """Single point-in-time resource usage measurement."""

    timestamp: float
    cpu_percent: float  # Average across all cores
    cpu_per_core: list[float]
    memory_mb: float
    memory_percent: float
    gpu_percent: float | None = None
    gpu_memory_mb: float | None = None
    gpu_temp_c: float | None = None


@dataclass
class StageMetrics:
    """Metrics for a single processing stage."""

    stage_name: str
    duration_ms: float
    start_time: float
    end_time: float


@dataclass
class MatchingMetrics:
    """Complete metrics for one file matching attempt."""

    # Test configuration
    config_id: str
    model_name: str
    device: str
    cache_state: Literal["warm", "cold"]

    # File info
    show_name: str
    season_number: int
    file_name: str
    file_path: str

    # Timing metrics (milliseconds)
    total_duration_ms: float
    model_load_ms: float
    stage_timings: list[StageMetrics] = field(default_factory=list)

    # Matching results
    predicted_episode: str | None = None
    confidence: float | None = None
    match_score: float | None = None
    chunks_processed: int = 0
    chunks_list: list[float] = field(default_factory=list)
    fail_fast_triggered: bool = False

    # Accuracy (if ground truth available)
    ground_truth_episode: str | None = None
    correct: bool | None = None

    # Resource usage
    avg_cpu_percent: float = 0.0
    peak_cpu_percent: float = 0.0
    avg_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    avg_gpu_percent: float | None = None
    peak_gpu_percent: float | None = None
    avg_gpu_memory_mb: float | None = None
    peak_gpu_memory_mb: float | None = None

    # Error tracking
    error: str | None = None
    success: bool = True


class ResourceMonitor:
    """Background thread that monitors CPU, memory, and GPU usage."""

    def __init__(self, sample_interval: float = 0.5):
        self.sample_interval = sample_interval
        self.snapshots: list[ResourceSnapshot] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.process = psutil.Process()

    def start(self):
        """Start monitoring in background thread."""
        self._stop_event.clear()
        self.snapshots = []
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring and return collected snapshots."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        return self.snapshots

    def _monitor_loop(self):
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            snapshot = self._take_snapshot()
            self.snapshots.append(snapshot)
            time.sleep(self.sample_interval)

    def _take_snapshot(self) -> ResourceSnapshot:
        """Capture current resource usage."""
        cpu_per_core = psutil.cpu_percent(percpu=True)
        cpu_avg = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0

        mem_info = self.process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)

        system_mem = psutil.virtual_memory()
        mem_percent = system_mem.percent

        # GPU monitoring
        gpu_percent = None
        gpu_memory_mb = None
        gpu_temp = None

        if GPU_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]  # First GPU
                    gpu_percent = gpu.load * 100
                    gpu_memory_mb = gpu.memoryUsed
                    gpu_temp = gpu.temperature
            except Exception:
                pass  # GPU monitoring failed, skip

        return ResourceSnapshot(
            timestamp=time.time(),
            cpu_percent=cpu_avg,
            cpu_per_core=cpu_per_core,
            memory_mb=mem_mb,
            memory_percent=mem_percent,
            gpu_percent=gpu_percent,
            gpu_memory_mb=gpu_memory_mb,
            gpu_temp_c=gpu_temp,
        )

    def calculate_summary(self) -> dict[str, Any]:
        """Calculate summary statistics from snapshots."""
        if not self.snapshots:
            return {}

        cpu_values = [s.cpu_percent for s in self.snapshots]
        mem_values = [s.memory_mb for s in self.snapshots]

        summary = {
            "avg_cpu_percent": sum(cpu_values) / len(cpu_values),
            "peak_cpu_percent": max(cpu_values),
            "avg_memory_mb": sum(mem_values) / len(mem_values),
            "peak_memory_mb": max(mem_values),
        }

        # GPU summary
        gpu_values = [s.gpu_percent for s in self.snapshots if s.gpu_percent is not None]
        if gpu_values:
            summary["avg_gpu_percent"] = sum(gpu_values) / len(gpu_values)
            summary["peak_gpu_percent"] = max(gpu_values)

        gpu_mem_values = [s.gpu_memory_mb for s in self.snapshots if s.gpu_memory_mb is not None]
        if gpu_mem_values:
            summary["avg_gpu_memory_mb"] = sum(gpu_mem_values) / len(gpu_mem_values)
            summary["peak_gpu_memory_mb"] = max(gpu_mem_values)

        return summary


class MatchingProfiler:
    """Wraps EpisodeMatcher with instrumentation."""

    def __init__(self, matcher: EpisodeMatcher):
        self.matcher = matcher
        self.stage_timings: list[StageMetrics] = []
        self.current_stage_start: float | None = None

    def _start_stage(self, stage_name: str):
        """Mark the start of a processing stage."""
        self.current_stage_start = time.perf_counter()

    def _end_stage(self, stage_name: str):
        """Mark the end of a processing stage."""
        if self.current_stage_start is not None:
            duration = (time.perf_counter() - self.current_stage_start) * 1000
            self.stage_timings.append(
                StageMetrics(
                    stage_name=stage_name,
                    duration_ms=duration,
                    start_time=self.current_stage_start,
                    end_time=time.perf_counter(),
                )
            )
            self.current_stage_start = None

    def identify_episode_profiled(
        self, video_file: Path, temp_dir: Path, season_number: int
    ) -> tuple[dict | None, list[StageMetrics]]:
        """
        Run episode identification with profiling.

        Returns:
            Tuple of (result dict, list of stage timings)
        """
        self.stage_timings = []

        # Profile the full identify_episode call
        # Note: We can't instrument internal stages without modifying the matcher code,
        # so we'll track the overall call and infer stages from logs if needed

        start_time = time.perf_counter()
        result = self.matcher.identify_episode(video_file, temp_dir, season_number)
        duration = (time.perf_counter() - start_time) * 1000

        self.stage_timings.append(
            StageMetrics(
                stage_name="full_matching",
                duration_ms=duration,
                start_time=start_time,
                end_time=time.perf_counter(),
            )
        )

        return result, self.stage_timings


@dataclass
class TestConfiguration:
    """Represents one test configuration."""

    model_name: Literal["tiny", "base", "small"]
    device: Literal["cpu", "cuda"]
    cache_state: Literal["warm", "cold"]

    @property
    def id(self) -> str:
        """Unique identifier for this configuration."""
        return f"{self.model_name}_{self.device}_{self.cache_state}"


class TestBench:
    """Main test orchestrator."""

    def __init__(
        self,
        test_dir: Path,
        cache_dir: Path,
        output_dir: Path,
        ground_truth_file: Path | None = None,
        enable_resource_monitoring: bool = True,
    ):
        self.test_dir = test_dir
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        self.ground_truth_file = ground_truth_file
        self.enable_resource_monitoring = enable_resource_monitoring

        self.ground_truth: dict[str, Any] = {}
        self.results: list[MatchingMetrics] = []

        # Load ground truth if available
        if ground_truth_file and ground_truth_file.exists():
            with open(ground_truth_file, "r", encoding="utf-8") as f:
                self.ground_truth = json.load(f)
                console.print(f"[green]Loaded ground truth from {ground_truth_file}")
        else:
            console.print("[yellow]No ground truth file, running performance-only tests")

    def discover_test_files(self, show_filter: str | None = None) -> list[dict[str, Any]]:
        """
        Discover all MKV test files.

        Returns:
            List of dicts with file info: {path, show_name, season_number, file_name}
        """
        files = []

        for show_dir in self.test_dir.iterdir():
            if not show_dir.is_dir():
                continue

            show_name = show_dir.name
            if show_filter and show_name.lower() != show_filter.lower():
                continue

            for season_dir in show_dir.iterdir():
                if not season_dir.is_dir() or not season_dir.name.startswith("Season"):
                    continue

                season_num = int(season_dir.name.split()[-1])

                for mkv_file in sorted(season_dir.glob("*.mkv")):
                    files.append(
                        {
                            "path": mkv_file,
                            "show_name": show_name,
                            "season_number": season_num,
                            "file_name": mkv_file.name,
                        }
                    )

        return files

    def prepare_cache_state(self, cache_state: Literal["warm", "cold"]):
        """Prepare subtitle cache to desired state."""
        cache_data_dir = self.cache_dir / "data"

        if cache_state == "cold":
            pass
            # Clear cache
            # if cache_data_dir.exists():
            #     console.print(f"[yellow]Clearing cache: {cache_data_dir}")
            #     shutil.rmtree(cache_data_dir, ignore_errors=True)
            # cache_data_dir.mkdir(parents=True, exist_ok=True)
        elif cache_state == "warm":
            # Ensure cache exists
            if not cache_data_dir.exists():
                console.print("[yellow]Warning: Cache is empty, cannot warm")
                cache_data_dir.mkdir(parents=True, exist_ok=True)
            else:
                console.print(f"[green]Cache is warm: {cache_data_dir}")

    def run_single_test(
        self, file_info: dict[str, Any], config: TestConfiguration, progress: Progress, task_id: TaskID
    ) -> MatchingMetrics:
        """Run a single test: one file with one configuration."""

        video_file = file_info["path"]
        show_name = file_info["show_name"]
        season_number = file_info["season_number"]
        file_name = file_info["file_name"]

        progress.update(task_id, description=f"[cyan]{config.id}[/] - {file_name}")

        # Initialize metrics
        metrics = MatchingMetrics(
            config_id=config.id,
            model_name=config.model_name,
            device=config.device,
            cache_state=config.cache_state,
            show_name=show_name,
            season_number=season_number,
            file_name=file_name,
            file_path=str(video_file),
            total_duration_ms=0.0,
            model_load_ms=0.0,
        )

        # Get ground truth if available
        if self.ground_truth:
            season_key = f"Season {season_number}"
            if show_name in self.ground_truth:
                if season_key in self.ground_truth[show_name]:
                    episodes = self.ground_truth[show_name][season_key].get("episodes", {})
                    metrics.ground_truth_episode = episodes.get(file_name, "UNKNOWN")

        # Start resource monitoring
        monitor = ResourceMonitor() if self.enable_resource_monitoring else None
        if monitor:
            monitor.start()

        try:
            # Create matcher
            with tempfile.TemporaryDirectory() as temp_dir:
                matcher = EpisodeMatcher(
                    cache_dir=self.cache_dir,
                    show_name=show_name,
                    device=config.device,
                    model_name=config.model_name,
                )

                # Instrument model loading time (first call only)
                model_config = {"type": "whisper", "name": config.model_name, "device": config.device}

                model_load_start = time.perf_counter()
                model = get_cached_model(model_config)
                model_load_duration = (time.perf_counter() - model_load_start) * 1000
                metrics.model_load_ms = model_load_duration

                # Run matching with profiling
                profiler = MatchingProfiler(matcher)

                start_time = time.perf_counter()
                result, stage_timings = profiler.identify_episode_profiled(
                    video_file, Path(temp_dir), season_number
                )
                total_duration = (time.perf_counter() - start_time) * 1000

                metrics.total_duration_ms = total_duration
                metrics.stage_timings = stage_timings

                # Extract results
                if result:
                    season = result.get("season")
                    episode = result.get("episode")
                    metrics.predicted_episode = f"S{season:02d}E{episode:02d}" if season and episode else None
                    metrics.confidence = result.get("confidence")
                    metrics.match_score = result.get("score")
                    metrics.success = True

                    # Check accuracy
                    if metrics.ground_truth_episode and metrics.ground_truth_episode != "UNKNOWN":
                        metrics.correct = metrics.predicted_episode == metrics.ground_truth_episode
                else:
                    metrics.success = False

        except Exception as e:
            logger.error(f"Error testing {file_name} with {config.id}: {e}")
            metrics.error = str(e)
            metrics.success = False

        finally:
            # Stop resource monitoring and collect summary
            if monitor:
                monitor.stop()
                resource_summary = monitor.calculate_summary()

                metrics.avg_cpu_percent = resource_summary.get("avg_cpu_percent", 0.0)
                metrics.peak_cpu_percent = resource_summary.get("peak_cpu_percent", 0.0)
                metrics.avg_memory_mb = resource_summary.get("avg_memory_mb", 0.0)
                metrics.peak_memory_mb = resource_summary.get("peak_memory_mb", 0.0)
                metrics.avg_gpu_percent = resource_summary.get("avg_gpu_percent")
                metrics.peak_gpu_percent = resource_summary.get("peak_gpu_percent")
                metrics.avg_gpu_memory_mb = resource_summary.get("avg_gpu_memory_mb")
                metrics.peak_gpu_memory_mb = resource_summary.get("peak_gpu_memory_mb")

        progress.advance(task_id)
        return metrics

    def run_all_tests(
        self,
        files: list[dict[str, Any]],
        configurations: list[TestConfiguration],
    ) -> list[MatchingMetrics]:
        """Run all test combinations."""

        total_tests = len(files) * len(configurations)
        console.print(f"\n[bold]Running {total_tests} tests[/] ({len(files)} files × {len(configurations)} configs)\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            main_task = progress.add_task("[cyan]Overall progress", total=total_tests)

            for config in configurations:
                # Prepare cache state for this configuration
                self.prepare_cache_state(config.cache_state)

                for file_info in files:
                    metrics = self.run_single_test(file_info, config, progress, main_task)
                    self.results.append(metrics)

        return self.results

    def generate_csv_report(self, output_path: Path):
        """Generate CSV report with all metrics."""
        import csv

        if not self.results:
            console.print("[yellow]No results to export")
            return

        # Flatten metrics to CSV rows
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "config_id",
                "model_name",
                "device",
                "cache_state",
                "show_name",
                "season_number",
                "file_name",
                "total_duration_ms",
                "model_load_ms",
                "predicted_episode",
                "confidence",
                "match_score",
                "chunks_processed",
                "fail_fast_triggered",
                "ground_truth_episode",
                "correct",
                "avg_cpu_percent",
                "peak_cpu_percent",
                "avg_memory_mb",
                "peak_memory_mb",
                "avg_gpu_percent",
                "peak_gpu_percent",
                "avg_gpu_memory_mb",
                "peak_gpu_memory_mb",
                "success",
                "error",
            ]

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for metrics in self.results:
                row = {k: getattr(metrics, k, None) for k in fieldnames}
                writer.writerow(row)

        console.print(f"[green]CSV report saved: {output_path}")

    def generate_json_report(self, output_path: Path):
        """Generate structured JSON report with summaries."""

        if not self.results:
            console.print("[yellow]No results to export")
            return

        # Calculate summaries by configuration
        summaries_by_config = defaultdict(lambda: {"times": [], "accuracies": [], "errors": 0})

        for metrics in self.results:
            config_id = metrics.config_id
            summaries_by_config[config_id]["times"].append(metrics.total_duration_ms)

            if metrics.correct is not None:
                summaries_by_config[config_id]["accuracies"].append(1 if metrics.correct else 0)

            if not metrics.success:
                summaries_by_config[config_id]["errors"] += 1

        # Build summary dicts
        config_summaries = {}
        for config_id, data in summaries_by_config.items():
            times = data["times"]
            accuracies = data["accuracies"]

            summary = {
                "total_tests": len(times),
                "avg_time_ms": sum(times) / len(times) if times else 0,
                "min_time_ms": min(times) if times else 0,
                "max_time_ms": max(times) if times else 0,
                "errors": data["errors"],
            }

            if accuracies:
                summary["accuracy_rate"] = sum(accuracies) / len(accuracies)
                summary["correct_count"] = sum(accuracies)
                summary["total_with_ground_truth"] = len(accuracies)

            config_summaries[config_id] = summary

        # Generate recommendations
        recommendations = self._generate_recommendations(config_summaries)

        # Build full report
        report = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "test_dir": str(self.test_dir),
                "cache_dir": str(self.cache_dir),
                "total_tests": len(self.results),
                "gpu_available": GPU_AVAILABLE,
            },
            "system_info": {
                "cpu_count": psutil.cpu_count(),
                "total_memory_gb": psutil.virtual_memory().total / (1024**3),
            },
            "configuration_summaries": config_summaries,
            "recommendations": recommendations,
            "detailed_results": [asdict(m) for m in self.results],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        console.print(f"[green]JSON report saved: {output_path}")

    def _generate_recommendations(self, summaries: dict[str, dict]) -> dict[str, Any]:
        """Analyze results and generate recommendations."""

        recommendations = {}

        # Find fastest configuration
        fastest_config = min(summaries.items(), key=lambda x: x[1]["avg_time_ms"])
        recommendations["fastest"] = {
            "config_id": fastest_config[0],
            "avg_time_ms": fastest_config[1]["avg_time_ms"],
        }

        # Find most accurate configuration (if ground truth available)
        configs_with_accuracy = {k: v for k, v in summaries.items() if "accuracy_rate" in v}
        if configs_with_accuracy:
            most_accurate = max(configs_with_accuracy.items(), key=lambda x: x[1]["accuracy_rate"])
            recommendations["most_accurate"] = {
                "config_id": most_accurate[0],
                "accuracy_rate": most_accurate[1]["accuracy_rate"],
            }

        # Find best balanced (speed vs accuracy trade-off)
        # Simple heuristic: Normalize both metrics to 0-1, then maximize sum
        if configs_with_accuracy:
            max_time = max(s["avg_time_ms"] for s in summaries.values())
            min_time = min(s["avg_time_ms"] for s in summaries.values())
            time_range = max_time - min_time if max_time > min_time else 1

            balanced_scores = {}
            for config_id, summary in configs_with_accuracy.items():
                # Normalized speed (inverted, so faster = higher score)
                speed_score = 1 - ((summary["avg_time_ms"] - min_time) / time_range)
                # Normalized accuracy
                accuracy_score = summary["accuracy_rate"]
                # Balanced score (equal weight)
                balanced_scores[config_id] = (speed_score + accuracy_score) / 2

            best_balanced = max(balanced_scores.items(), key=lambda x: x[1])
            recommendations["best_balanced"] = {
                "config_id": best_balanced[0],
                "balanced_score": best_balanced[1],
            }

        return recommendations

    def print_summary(self):
        """Print summary table to console."""

        if not self.results:
            console.print("[yellow]No results to display")
            return

        # Group by configuration
        by_config = defaultdict(list)
        for m in self.results:
            by_config[m.config_id].append(m)

        # Create summary table
        table = Table(title="Test Bench Results Summary", show_header=True, header_style="bold magenta")
        table.add_column("Configuration", style="cyan")
        table.add_column("Tests", justify="right")
        table.add_column("Avg Time (s)", justify="right")
        table.add_column("Errors", justify="right")
        table.add_column("Accuracy", justify="right")

        for config_id, metrics_list in sorted(by_config.items()):
            times = [m.total_duration_ms for m in metrics_list]
            avg_time_s = (sum(times) / len(times)) / 1000 if times else 0
            errors = sum(1 for m in metrics_list if not m.success)

            # Calculate accuracy if available
            with_gt = [m for m in metrics_list if m.ground_truth_episode and m.ground_truth_episode != "UNKNOWN"]
            if with_gt:
                correct = sum(1 for m in with_gt if m.correct)
                accuracy_str = f"{correct}/{len(with_gt)} ({correct/len(with_gt)*100:.1f}%)"
            else:
                accuracy_str = "N/A"

            table.add_row(config_id, str(len(metrics_list)), f"{avg_time_s:.2f}", str(errors), accuracy_str)

        console.print("\n")
        console.print(table)
        console.print("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Episode Matching Performance Analysis Test Bench",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be tested without executing",
    )

    parser.add_argument(
        "--models",
        type=str,
        default="tiny,base,small",
        help="Comma-separated list of models to test (default: tiny,base,small)",
    )

    parser.add_argument(
        "--cache",
        type=str,
        choices=["warm", "cold", "both"],
        default="both",
        help="Cache state to test (default: both)",
    )

    parser.add_argument(
        "--device",
        type=str,
        choices=["cpu", "cuda", "both"],
        default="both",
        help="Device to test (default: both)",
    )

    parser.add_argument(
        "--show",
        type=str,
        help="Only test files from this show",
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to test",
    )

    parser.add_argument(
        "--no-resource-monitoring",
        action="store_true",
        help="Skip resource monitoring (faster, less data)",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "test_bench_results",
        help="Output directory for reports",
    )

    parser.add_argument(
        "--test-dir",
        type=Path,
        default=DEFAULT_TEST_DIR,
        help="Test data directory",
    )

    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Subtitle cache directory",
    )

    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path(__file__).parent / "ground_truth.json",
        help="Ground truth JSON file",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    # Validate test directory
    if not args.test_dir.exists():
        console.print(f"[red]Error: Test directory not found: {args.test_dir}")
        sys.exit(1)

    # Create test bench
    bench = TestBench(
        test_dir=args.test_dir,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        ground_truth_file=args.ground_truth if args.ground_truth.exists() else None,
        enable_resource_monitoring=not args.no_resource_monitoring,
    )

    # Discover test files
    files = bench.discover_test_files(show_filter=args.show)

    if args.limit:
        files = files[: args.limit]

    if not files:
        console.print("[red]No test files found!")
        sys.exit(1)

    console.print(f"[green]Found {len(files)} test files")

    # Build configuration matrix
    models = args.models.split(",")
    cache_states = ["warm", "cold"] if args.cache == "both" else [args.cache]
    devices = ["cpu", "cuda"] if args.device == "both" else [args.device]

    # Filter out CUDA if not available
    if "cuda" in devices:
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() == 0:
                console.print("[yellow]CUDA not available, skipping GPU tests")
                devices = ["cpu"]
        except Exception:
            console.print("[yellow]CUDA check failed, skipping GPU tests")
            devices = ["cpu"]

    configurations = [
        TestConfiguration(model_name=m, device=d, cache_state=c)
        for m in models
        for d in devices
        for c in cache_states
    ]

    console.print(f"[green]Testing {len(configurations)} configurations:")
    for config in configurations:
        console.print(f"  - {config.id}")

    # Dry run mode
    if args.dry_run:
        console.print("\n[yellow]DRY RUN - No tests will be executed")
        console.print(f"\nWould test {len(files)} files × {len(configurations)} configs = {len(files) * len(configurations)} total tests")
        return

    # Run tests
    console.print("\n[bold cyan]Starting test bench...[/]\n")

    start_time = time.time()
    bench.run_all_tests(files, configurations)
    elapsed = time.time() - start_time

    console.print(f"\n[bold green]Tests completed in {elapsed/60:.1f} minutes[/]\n")

    # Generate reports
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = args.output_dir / f"test_bench_results_{timestamp}.csv"
    json_path = args.output_dir / f"test_bench_results_{timestamp}.json"

    bench.generate_csv_report(csv_path)
    bench.generate_json_report(json_path)

    # Print summary
    bench.print_summary()

    console.print(f"\n[bold green]Reports saved to: {args.output_dir}[/]")


if __name__ == "__main__":
    main()
