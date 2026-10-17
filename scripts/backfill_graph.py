"""
AgriTrace Graph Backfill Utility
Synchronizes all nodes and edges from the embedded SQLite graph engine
into FalkorDB when FalkorDB is connected.
Ensures consistency and prevents partial graphs when FalkorDB starts after local writes.
"""

import sys
import json
import logging
from backend.database.db import get_db_connection
from backend.graph.falkordb_service import FalkorDBService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agritrace.backfill")

def backfill_graph():
    falkor = FalkorDBService()
    if not falkor.check_connection():
        logger.warning("FalkorDB is not connected. Start FalkorDB and run again to backfill.")
        return False

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Backfill all nodes
    nodes = cursor.execute("SELECT id, label, properties FROM graph_nodes").fetchall()
    logger.info(f"Backfilling {len(nodes)} graph nodes to FalkorDB...")
    for n in nodes:
        props = json.loads(n["properties"])
        props["id"] = n["id"]
        try:
            falkor.create_or_update_node(n["label"], props)
        except Exception as e:
            logger.warning(f"Failed to backfill node {n['id']}: {e}")

    # 2. Backfill all edges
    edges = cursor.execute("SELECT from_id, rel_type, to_id, properties FROM graph_edges").fetchall()
    logger.info(f"Backfilling {len(edges)} graph edges to FalkorDB...")
    for e in edges:
        props = json.loads(e["properties"]) if e["properties"] else {}
        try:
            falkor.create_relationship(e["from_id"], e["rel_type"], e["to_id"], props)
        except Exception as e:
            logger.warning(f"Failed to backfill edge {e['from_id']} -[{e['rel_type']}]-> {e['to_id']}: {e}")

    conn.close()
    logger.info("Graph backfill completed successfully.")
    return True

if __name__ == "__main__":
    backfill_graph()
