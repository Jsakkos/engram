
import logging
import sys
from app.core.analyst import DiscAnalyst, TitleInfo, ContentType

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def test_analyst():
    analyst = DiscAnalyst()
    
    # Mock Titles for "Tropic Thunder"
    # Main feature: 107 mins (6420 secs)
    # Extras: Various short clips
    titles = [
        TitleInfo(index=0, duration_seconds=6420, size_bytes=20000000000, chapter_count=16), # Main Movie
        TitleInfo(index=1, duration_seconds=1200, size_bytes=1000000000, chapter_count=1), # Extra 20 min
        TitleInfo(index=2, duration_seconds=1240, size_bytes=1000000000, chapter_count=1), # Extra 20 min
        TitleInfo(index=3, duration_seconds=1180, size_bytes=1000000000, chapter_count=1), # Extra 19 min
        TitleInfo(index=4, duration_seconds=300, size_bytes=500000000, chapter_count=1),  # Short extra
    ]
    
    print("\n--- Test Case 1: Standard Movie Label ---")
    result = analyst.analyze(titles, "TROPIC_THUNDER")
    print(f"Result: {result.content_type} (Confidence: {result.confidence})")
    
    print("\n--- Test Case 2: Ambiguous Label (Maybe triggers Season?) ---")
    # "TROPIC_THUNDER_S_E" ? No.
    # "TROPIC_THUNDER_SEQ1" ?
    result = analyst.analyze(titles, "TROPIC_THUNDER_SE") # Special Edition?
    print(f"Result: {result.content_type} (Confidence: {result.confidence})")

    print("\n--- Test Case 3: Label with Numbers ---")
    result = analyst.analyze(titles, "TROPIC_THUNDER_1") 
    print(f"Result: {result.content_type} (Confidence: {result.confidence})")

    print("\n--- Test Case 4: Tricky Label ---")
    result = analyst.analyze(titles, "TROPIC_THUNDER_S1") # Explicitly looks like Season 1
    print(f"Result: {result.content_type} (Confidence: {result.confidence})")
    
    # Test Case 5: Actual User Label
    print("\n--- Test Case 5: TROPIC_THUNDER_AC ---")
    result = analyst.analyze(titles, "TROPIC_THUNDER_AC")
    print(f"Result: {result.content_type} (Confidence: {result.confidence})")

if __name__ == "__main__":
    test_analyst()
