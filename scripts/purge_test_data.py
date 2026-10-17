"""
AgriTrace Test Data Purge Utility
Safely removes automated test accounts (test_farmer*, f1_*, f2_*) from
the persistent SQLite database and graph tables.
Preserves genuine farmer profiles (uday, uday1).
Always backs up database before any mutation.
"""

import sys
import os
import shutil
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "agritrace.db"

def backup_db(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"agritrace_backup_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path

def purge_test_data(execute: bool = False):
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Nothing to purge.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find candidate test farmers
    cursor.execute("""
        SELECT id, username, display_name FROM farmers
        WHERE id LIKE 'test_farmer%' OR id LIKE 'f1_%' OR id LIKE 'f2_%'
           OR username LIKE 'usr_test_farmer%' OR username LIKE 'usr_f1_%' OR username LIKE 'usr_f2_%'
    """)
    test_farmers = cursor.fetchall()
    farmer_ids = [r["id"] for r in test_farmers]

    # Double check genuine accounts are protected
    cursor.execute("SELECT id, username FROM farmers WHERE username IN ('uday', 'uday1')")
    genuine_accounts = cursor.fetchall()

    print(f"=== AgriTrace Test Data Purge ===")
    print(f"Mode: {'EXECUTE (MUTATING)' if execute else 'DRY RUN (NO CHANGES)'}")
    print(f"Database: {DB_PATH}")
    print(f"Found {len(test_farmers)} test farmer account(s) to purge:")
    for tf in test_farmers:
        print(f"  - [{tf['id']}] {tf['username']} ({tf['display_name']})")

    print(f"\nProtected genuine accounts ({len(genuine_accounts)}):")
    for ga in genuine_accounts:
        print(f"  * [{ga['id']}] {ga['username']} (PRESERVED)")

    if not farmer_ids:
        print("\nNo test farmers found to purge.")
        conn.close()
        return

    # Count cascade rows
    placeholders = ",".join(["?"] * len(farmer_ids))

    farms_count = cursor.execute(f"SELECT COUNT(*) FROM farms WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]
    fields_count = cursor.execute(f"SELECT COUNT(*) FROM fields WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]
    cycles_count = cursor.execute(f"SELECT COUNT(*) FROM crop_cycles WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]
    obs_count = cursor.execute(f"SELECT COUNT(*) FROM observations WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]
    ev_count = cursor.execute(f"SELECT COUNT(*) FROM evidence WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]
    invs_count = cursor.execute(f"SELECT COUNT(*) FROM investigations WHERE farmer_id IN ({placeholders})", farmer_ids).fetchone()[0]

    # Findings & hypotheses tied to these investigations
    findings_count = cursor.execute(f"""
        SELECT COUNT(*) FROM findings
        WHERE investigation_id IN (SELECT id FROM investigations WHERE farmer_id IN ({placeholders}))
    """, farmer_ids).fetchone()[0]
    hyp_count = cursor.execute(f"""
        SELECT COUNT(*) FROM hypotheses
        WHERE investigation_id IN (SELECT id FROM investigations WHERE farmer_id IN ({placeholders}))
    """, farmer_ids).fetchone()[0]

    # Graph tables
    graph_nodes_count = 0
    graph_edges_count = 0
    has_graph = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_nodes'").fetchone()
    if has_graph:
        graph_nodes_count = cursor.execute(f"""
            SELECT COUNT(*) FROM graph_nodes
            WHERE id IN ({placeholders})
               OR id IN (SELECT id FROM farms WHERE farmer_id IN ({placeholders}))
               OR id IN (SELECT id FROM fields WHERE farmer_id IN ({placeholders}))
               OR id IN (SELECT id FROM crop_cycles WHERE farmer_id IN ({placeholders}))
               OR id IN (SELECT id FROM observations WHERE farmer_id IN ({placeholders}))
               OR id IN (SELECT id FROM investigations WHERE farmer_id IN ({placeholders}))
        """, farmer_ids * 6).fetchone()[0]

    print(f"\nAssociated entities to delete:")
    print(f"  - Farms: {farms_count}")
    print(f"  - Fields: {fields_count}")
    print(f"  - Crop Cycles: {cycles_count}")
    print(f"  - Observations: {obs_count}")
    print(f"  - Evidence: {ev_count}")
    print(f"  - Investigations: {invs_count}")
    print(f"  - Findings: {findings_count}")
    print(f"  - Hypotheses: {hyp_count}")
    print(f"  - Graph Nodes (approx): {graph_nodes_count}")

    if not execute:
        print("\n[DRY RUN COMPLETE] To actually purge test data, re-run with --execute")
        conn.close()
        return

    # Execution path
    backup = backup_db(DB_PATH)
    print(f"\n[BACKUP CREATED] Backup saved to: {backup}")

    # Delete in child-to-parent order with foreign keys enabled
    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute(f"""
        DELETE FROM findings
        WHERE investigation_id IN (SELECT id FROM investigations WHERE farmer_id IN ({placeholders}))
    """, farmer_ids)

    cursor.execute(f"""
        DELETE FROM hypotheses
        WHERE investigation_id IN (SELECT id FROM investigations WHERE farmer_id IN ({placeholders}))
    """, farmer_ids)

    cursor.execute(f"DELETE FROM investigations WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM evidence WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM observations WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM crop_cycles WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM fields WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM farms WHERE farmer_id IN ({placeholders})", farmer_ids)
    cursor.execute(f"DELETE FROM farmers WHERE id IN ({placeholders})", farmer_ids)

    if has_graph:
        # Delete graph edges referencing orphan nodes
        cursor.execute(f"""
            DELETE FROM graph_edges
            WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
        """, farmer_ids * 2)

        cursor.execute(f"DELETE FROM graph_nodes WHERE id IN ({placeholders})", farmer_ids)

    conn.commit()
    conn.close()
    print("\n[PURGE SUCCESSFUL] Test data permanently deleted and database sanitized.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge test data from AgriTrace database.")
    parser.add_argument("--execute", action="store_true", help="Execute the deletion (default is dry-run)")
    args = parser.parse_args()
    purge_test_data(execute=args.execute)
