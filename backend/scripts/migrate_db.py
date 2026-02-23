import sqlite3
from pathlib import Path

db_path = Path(r"c:\Github\engram\backend\engram.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

columns = [
    ("subtitles_downloaded", "INTEGER DEFAULT 0"),
    ("subtitles_total", "INTEGER DEFAULT 0"),
    ("subtitles_failed", "INTEGER DEFAULT 0"),
]

for col_name, col_type in columns:
    try:
        cursor.execute(f"ALTER TABLE disc_jobs ADD COLUMN {col_name} {col_type}")
        print(f"Added column {col_name}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print(f"Column {col_name} already exists")
        else:
            print(f"Error adding {col_name}: {e}")

conn.commit()
conn.close()
