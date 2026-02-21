import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    print("Attempting to import app.matcher...")

    print("Successfully imported app.matcher")

    print("Attempting to import EpisodeMatcher...")
    from app.matcher.episode_identification import EpisodeMatcher

    print("Successfully imported EpisodeMatcher")

    print("Attempting to instantiate EpisodeMatcher...")
    matcher = EpisodeMatcher(cache_dir=Path("./temp_cache"), show_name="Test Show")
    print("Successfully instantiated EpisodeMatcher")

except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)

print("VERIFICATION SUCCESSFUL")
