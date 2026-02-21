import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.matcher.testing_service import download_subtitles

print("Downloading subtitles for The Expanse Season 1...")
try:
    result = download_subtitles("The Expanse", 1)
    print(f"Downloaded {len(result['episodes'])} episodes.")
    for ep in result["episodes"]:
        print(f"{ep['code']}: {ep['status']}")
except Exception as e:
    print(f"Error: {e}")
