
from pathlib import Path
import os

log_path = Path(r"C:\Users\jonat\.uma\uma.log")
lines_to_read = 200

if not log_path.exists():
    print(f"File not found: {log_path}")
else:
    with open(log_path, "rb") as f:
        # Move to end of file
        f.seek(0, os.SEEK_END)
        end_pos = f.tell()
        current_pos = end_pos
        line_count = 0
        block_size = 1024
        buffer = []
        
        while current_pos > 0 and line_count < lines_to_read:
            read_size = min(block_size, current_pos)
            current_pos -= read_size
            f.seek(current_pos)
            block = f.read(read_size)
            
            # Decode carefully
            try:
                decoded = block.decode("utf-8", errors="ignore")
            except:
                decoded = str(block)
                
            buffer.insert(0, decoded)
            line_count += decoded.count('\n')
            
        content = "".join(buffer)
        lines = content.splitlines()
        print("\n".join(lines[-lines_to_read:]))
