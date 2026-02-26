"""
Export investigation results for manual review and analysis.

This script processes matching results and generates human-readable exports
including CSV datasets, summary tables, error analysis, and visualization data.

Usage:
    uv run python -m app.matcher.scripts.export_investigation_results \
        --input investigation_output/matching_results.csv
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


def generate_master_dataset(results_df: pd.DataFrame, transcriptions_dir: Path, output_path: Path):
    """
    Generate chunk-level master dataset for detailed analysis.

    One row per chunk with transcription and match details.
    """
    print("Generating master dataset (chunk-level)...")

    rows = []

    # Group by file
    for file_path, file_group in results_df.groupby("file_path"):
        # Load transcription data for this file
        # Find the JSON file by matching show/season/episode
        first_row = file_group.iloc[0]
        show = first_row["show_name"]
        season = first_row["season"]
        episode = first_row["episode_actual"]

        json_path = (
            transcriptions_dir / show / f"Season {season:02d}" / f"S{season:02d}E{episode:02d}.json"
        )

        if not json_path.exists():
            print(f"  Warning: Transcription not found: {json_path}")
            continue

        with open(json_path, encoding="utf-8") as f:
            trans_data = json.load(f)

        # Create row for each chunk
        for chunk in trans_data["chunks"]:
            row = {
                "file_path": file_path,
                "show": show,
                "season": season,
                "episode_actual": episode,
                "chunk_index": chunk["chunk_index"],
                "start_time": chunk["start_time"],
                "duration": chunk["duration"],
                "transcription": chunk["cleaned_text"][:200],  # Truncate for CSV
                "language": chunk["language"],
            }

            # Add match results from different methods
            # Note: We don't have per-chunk match data in results, only file-level
            # This could be enhanced by storing per-chunk votes in evaluate_matching_methods.py

            rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"  ✓ Saved {len(rows)} chunk records to {output_path}")


def generate_method_comparison(results_df: pd.DataFrame, output_path: Path):
    """Generate summary table comparing all methods."""
    print("Generating method comparison summary...")

    # Calculate metrics for each method
    summary_rows = []

    for method, method_group in results_df.groupby("method_name"):
        total = len(method_group)
        correct = method_group["correct"].sum()
        confident = (~method_group["fallback_used"]).sum()
        confident_correct = ((method_group["correct"]) & (~method_group["fallback_used"])).sum()

        accuracy = correct / total if total > 0 else 0
        recall = confident / total if total > 0 else 0
        precision = confident_correct / confident if confident > 0 else 0
        fallback_rate = (total - confident) / total if total > 0 else 0

        summary_rows.append(
            {
                "method": method,
                "total_files": total,
                "correct_matches": correct,
                "accuracy": f"{accuracy:.1%}",
                "precision": f"{precision:.1%}",
                "recall": f"{recall:.1%}",
                "fallback_rate": f"{fallback_rate:.1%}",
                "avg_confidence": f"{method_group['confidence'].mean():.3f}",
                "avg_time_sec": f"{method_group['processing_time'].mean():.2f}",
                "avg_chunk_usage": f"{(method_group['chunks_used'] / method_group['total_chunks']).mean():.1%}",
            }
        )

    df = pd.DataFrame(summary_rows)
    df.to_csv(output_path, index=False)
    print(f"  ✓ Saved method comparison to {output_path}")

    # Also print to console
    print("\n" + "=" * 80)
    print("METHOD COMPARISON")
    print("=" * 80)
    print(df.to_string(index=False))
    print()


def generate_error_analysis(results_df: pd.DataFrame, transcriptions_dir: Path, output_path: Path):
    """Generate detailed error analysis in markdown format."""
    print("Generating error analysis...")

    # Get all incorrect matches
    errors = results_df[~results_df["correct"]].copy()

    if errors.empty:
        print("  No errors to analyze!")
        return

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Episode Matching Error Analysis\n\n")
        f.write(f"Total errors: {len(errors)}\n\n")

        # Group by method
        for method, method_errors in errors.groupby("method_name"):
            f.write(f"## Method: {method}\n\n")
            f.write(
                f"Errors: {len(method_errors)} / {len(results_df[results_df['method_name'] == method])}\n\n"
            )

            # List each error
            for _, error in method_errors.iterrows():
                f.write(f"### File: {Path(error['file_path']).name}\n\n")
                f.write(
                    f"- **Actual Episode**: S{error['season']:02d}E{error['episode_actual']:02d}\n"
                )

                # Format matched episode (handle None case)
                if pd.notna(error["episode_matched"]):
                    matched_ep = f"S{error['season']:02d}E{int(error['episode_matched']):02d}"
                else:
                    matched_ep = "None"
                f.write(f"- **Matched Episode**: {matched_ep}\n")

                f.write(f"- **Confidence**: {error['confidence']:.3f}\n")
                f.write(f"- **Weighted Score**: {error['weighted_score']:.3f}\n")
                f.write(
                    f"- **Chunks Used**: {int(error['chunks_used'])} / {int(error['total_chunks'])}\n"
                )
                f.write(f"- **Fallback Used**: {error['fallback_used']}\n")

                # Load transcription sample if available
                show = error["show_name"]
                season = error["season"]
                episode = error["episode_actual"]

                json_path = (
                    transcriptions_dir
                    / show
                    / f"Season {season:02d}"
                    / f"S{season:02d}E{episode:02d}.json"
                )

                if json_path.exists():
                    with open(json_path, encoding="utf-8") as tf:
                        trans_data = json.load(tf)

                    # Show first few chunks
                    f.write("\n**Sample Transcriptions:**\n\n")
                    for i, chunk in enumerate(trans_data["chunks"][:3]):
                        f.write(
                            f"- Chunk {i} ({chunk['start_time']:.0f}s): {chunk['cleaned_text'][:150]}...\n"
                        )

                f.write("\n---\n\n")

        # Confusion matrix
        f.write("## Confusion Patterns\n\n")
        f.write("Episodes that are frequently confused:\n\n")

        confusion = defaultdict(int)
        for _, error in errors.iterrows():
            if pd.notna(error["episode_matched"]):
                key = (error["episode_actual"], int(error["episode_matched"]))
                confusion[key] += 1

        for (actual, matched), count in sorted(confusion.items(), key=lambda x: x[1], reverse=True):
            f.write(f"- E{actual:02d} → E{matched:02d}: {count} times\n")

    print(f"  ✓ Saved error analysis to {output_path}")


def generate_visualization_data(results_df: pd.DataFrame, output_path: Path):
    """Generate JSON data for creating charts and visualizations."""
    print("Generating visualization data...")

    viz_data = {
        "method_comparison": {},
        "confidence_distributions": {},
        "chunk_utilization": {},
    }

    # Method comparison data
    for method, method_group in results_df.groupby("method_name"):
        viz_data["method_comparison"][method] = {
            "accuracy": float(method_group["correct"].sum() / len(method_group)),
            "avg_confidence": float(method_group["confidence"].mean()),
            "fallback_rate": float(method_group["fallback_used"].sum() / len(method_group)),
            "avg_processing_time": float(method_group["processing_time"].mean()),
        }

        # Confidence distribution
        viz_data["confidence_distributions"][method] = {
            "correct": method_group[method_group["correct"]]["confidence"].tolist(),
            "incorrect": method_group[~method_group["correct"]]["confidence"].tolist(),
        }

        # Chunk utilization
        viz_data["chunk_utilization"][method] = {
            "chunks_used": method_group["chunks_used"].tolist(),
            "total_chunks": method_group["total_chunks"].tolist(),
            "utilization_rates": (
                method_group["chunks_used"] / method_group["total_chunks"]
            ).tolist(),
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(viz_data, f, indent=2)

    print(f"  ✓ Saved visualization data to {output_path}")


def analyze_confusion_patterns(results_df: pd.DataFrame):
    """Analyze which chunks most frequently cause confusion."""
    print("\n" + "=" * 80)
    print("CONFUSION PATTERN ANALYSIS")
    print("=" * 80)

    errors = results_df[~results_df["correct"]]

    if errors.empty:
        print("No errors to analyze!")
        return

    # Analyze by file
    print("\nFiles with most errors across methods:")
    file_error_counts = errors.groupby("file_path").size().sort_values(ascending=False)
    for file_path, count in file_error_counts.head(5).items():
        print(f"  {Path(file_path).name}: {count} errors")

    # Analyze by method
    print("\nMethods with most errors:")
    method_error_counts = errors.groupby("method_name").size().sort_values(ascending=False)
    for method, count in method_error_counts.items():
        total = len(results_df[results_df["method_name"] == method])
        print(f"  {method}: {count}/{total} ({count / total:.1%})")

    # Fallback analysis
    print("\nFallback rates by method:")
    for method, method_group in results_df.groupby("method_name"):
        fallback_rate = method_group["fallback_used"].sum() / len(method_group)
        print(f"  {method}: {fallback_rate:.1%}")


def main():
    parser = argparse.ArgumentParser(description="Export investigation results for manual review")
    parser.add_argument(
        "--input",
        type=str,
        default="investigation_output/matching_results.csv",
        help="Input CSV file from evaluate_matching_methods.py",
    )
    parser.add_argument(
        "--transcriptions",
        type=str,
        default="investigation_output/transcriptions",
        help="Path to transcription cache",
    )
    parser.add_argument(
        "--output-dir", type=str, default="investigation_output/analysis", help="Output directory"
    )

    args = parser.parse_args()

    # Load results
    print(f"Loading results from {args.input}...")
    results_df = pd.read_csv(args.input)
    print(f"  Loaded {len(results_df)} result records")

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate exports
    print("\n--- Generating Exports ---\n")

    generate_master_dataset(
        results_df, Path(args.transcriptions), output_dir / "master_dataset.csv"
    )

    generate_method_comparison(results_df, output_dir / "method_comparison.csv")

    generate_error_analysis(results_df, Path(args.transcriptions), output_dir / "error_analysis.md")

    generate_visualization_data(results_df, output_dir / "visualization_data.json")

    # Run analysis
    analyze_confusion_patterns(results_df)

    print("\n" + "=" * 80)
    print("EXPORT COMPLETE")
    print("=" * 80)
    print(f"\nAll files saved to: {output_dir}")
    print("\nGenerated files:")
    print("  - master_dataset.csv: Chunk-level detailed data")
    print("  - method_comparison.csv: Summary comparison table")
    print("  - error_analysis.md: Detailed error breakdown")
    print("  - visualization_data.json: Data for charts/graphs")


if __name__ == "__main__":
    main()
