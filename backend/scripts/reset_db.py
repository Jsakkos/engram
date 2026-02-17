"""Reset the UMA database by deleting and recreating it.

Run this script when the backend is NOT running to fix schema issues.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main():
    """Delete the database file and recreate with current schema."""
    db_path = Path(__file__).parent.parent / "uma.db"

    if db_path.exists():
        try:
            db_path.unlink()
            logger.info(f"Deleted old database: {db_path}")
        except Exception as e:
            logger.error(f"Could not delete database: {e}")
            logger.error(
                "Make sure the backend is not running, then try again or manually delete uma.db"
            )
            return 1

    # Import database module and recreate
    from app.database import init_db

    try:
        await init_db()
        logger.info("Database recreated successfully with current schema")
        return 0
    except Exception as e:
        logger.error(f"Failed to recreate database: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
