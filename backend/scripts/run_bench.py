"""Wrapper to run transcript_matching benchmark and save output to file."""

import io
import sys

# Redirect stdout to capture all print output
output_capture = io.StringIO()
original_stdout = sys.stdout
sys.stdout = output_capture

try:
    # Import and run the benchmark
    sys.path.insert(0, ".")
    from scripts.transcript_matching import main

    main()
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback

    traceback.print_exc()
finally:
    sys.stdout = original_stdout

# Write captured output to file
result = output_capture.getvalue()
output_path = r"C:\Users\jonat\bench_results.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(result)

print(f"Results written to {output_path} ({len(result)} chars)")
