"""ApexGraph Database Migration Script

Applies all database tables, constraints, and indexes.
Can be executed directly via `python -m packages.core.migrations`.
"""

import sys
import logging
from packages.core.database import init_db, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apexgraph.migrations")


def run_migrations():
    logger.info("Starting database migration for ApexGraph...")
    try:
        init_db(engine)
        logger.info("Successfully created/verified all database tables and indexes.")
        return 0
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(run_migrations())
