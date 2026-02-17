
import sqlite3
from pathlib import Path

# Database path
DB_PATH = Path("uma.db")

def add_column():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    print(f"Connecting to database at {DB_PATH}...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if column exists
        cursor.execute("PRAGMA table_info(disc_titles)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if "match_details" in columns:
            print("'match_details' column already exists.")
        else:
            print("Adding 'match_details' column...")
            cursor.execute("ALTER TABLE disc_titles ADD COLUMN match_details TEXT")
            conn.commit()
            print("Column added successfully.")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    add_column()
