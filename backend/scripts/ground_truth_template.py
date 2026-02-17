"""
Ground Truth Template Generator

Scans the test data directory and generates a template JSON file
that users can populate with correct episode mappings.
"""

import json
import re
from pathlib import Path


def generate_ground_truth_template(test_data_dir: Path, output_file: Path):
    """Generate ground truth template from test data directory."""

    ground_truth = {}

    # Scan for show directories
    for show_dir in test_data_dir.iterdir():
        if not show_dir.is_dir():
            continue

        show_name = show_dir.name
        ground_truth[show_name] = {}

        # Scan for season directories
        for season_dir in show_dir.iterdir():
            if not season_dir.is_dir() or not season_dir.name.startswith("Season"):
                continue

            # Extract season number
            season_num_str = season_dir.name.split()[-1]
            try:
                season_num = int(season_num_str)
            except ValueError:
                continue

            # Collect all MKV files
            episodes = {}
            for mkv_file in sorted(season_dir.glob("*.mkv")):
                # Try to extract episode code (e.g., S01E01) from filename
                match = re.search(r"S(\d+)E(\d+)", mkv_file.name, re.IGNORECASE)
                if match:
                    s_num = int(match.group(1))
                    e_num = int(match.group(2))
                    episodes[mkv_file.name] = f"S{s_num:02d}E{e_num:02d}"
                else:
                    episodes[mkv_file.name] = "UNKNOWN"  # Placeholder

            if episodes:
                ground_truth[show_name][f"Season {season_num}"] = {
                    "season": season_num,
                    "episodes": episodes
                }

    # Write template
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2, ensure_ascii=False)

    print(f"[OK] Ground truth template generated: {output_file}")
    print(f"     Found {sum(len(seasons) for show in ground_truth.values() for seasons in show.values() if isinstance(seasons, dict))} shows/seasons")

    # Count total files
    total_files = 0
    extracted_count = 0
    for show in ground_truth.values():
        for season_data in show.values():
            if isinstance(season_data, dict) and "episodes" in season_data:
                eps = season_data["episodes"]
                total_files += len(eps)
                extracted_count += sum(1 for v in eps.values() if v != "UNKNOWN")

    print(f"     Found {total_files} MKV files")
    print(f"     Pre-filled {extracted_count} episode codes from filenames")
    print()
    print("[!] Please edit this file and replace any remaining 'UNKNOWN' values with correct episode codes.")
    print("    Or leave as-is to run performance-only tests without accuracy metrics.")


if __name__ == "__main__":
    test_dir = Path(r"C:\Media\Tests")
    output = Path(__file__).parent / "ground_truth.json"

    if not test_dir.exists():
        print(f"[ERROR] Test directory not found: {test_dir}")
        exit(1)

    generate_ground_truth_template(test_dir, output)
