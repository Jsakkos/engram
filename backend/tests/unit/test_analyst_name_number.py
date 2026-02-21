import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.analyst import DiscAnalyst


def test_name_number_parsing():
    analyst = DiscAnalyst()

    cases = [
        ("SOUTHPARK6", "SOUTHPARK", 6),
        (
            "IronMan2",
            "IronMan",
            2,
        ),  # Heuristic accepts it, Movie detection logic overrides later if duration mismatch
        ("TheOfficeS1", "TheOffice", 1),  # Should already work via S1 pattern? Wait, S1 needs 'S'
        ("ShowName20", "ShowName", 20),
        ("ShowName2024", None, None),  # Should fail (year > 100)
    ]

    print("Testing DiscAnalyst._parse_volume_label...")
    for label, expected_name, expected_season in cases:
        name, season, disc = analyst._parse_volume_label(label)
        print(f"Label: '{label}' -> Name: '{name}', Season: {season}, Disc: {disc}")

        # Determine success
        name_match = name == expected_name.title() if expected_name else name is None
        season_match = season == expected_season

        if name_match and season_match:
            print("  [PASS]")
        else:
            print(f"  [FAIL] Expected ({expected_name}, {expected_season})")


if __name__ == "__main__":
    test_name_number_parsing()
