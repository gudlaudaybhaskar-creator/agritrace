"""
Knowledge Graph Router
Enforces strict per-farmer subgraph boundaries, caps graph query depths (1 to 4),
restricts openCypher execution to authenticated administrators with read-only validation,
and verifies ownership on investigation subgraphs.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.database.db import get_db_connection
from backend.graph.graph_manager import graph_manager
from backend.routers.auth import get_current_farmer, get_current_admin

router = APIRouter(prefix="/graph", tags=["Knowledge Graph"])

class CypherQueryRequest(BaseModel):
    query: str
    params: Optional[Dict[str, Any]] = None

@router.get("/subgraph")
def get_farmer_subgraph(
    depth: int = Query(default=2, ge=1, le=4),
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """
    Returns the ego knowledge graph for the authenticated farmer.
    Capped at depth 1-4 to guarantee performance on mobile devices.
    """
    subgraph = graph_manager.get_subgraph_for_farmer(farmer["id"], depth=depth)
    return {
        "status": "success",
        "farmer_id": farmer["id"],
        "subgraph": subgraph
    }

@router.get("/investigation/{inv_id}")
def get_investigation_subgraph(
    inv_id: str,
    farmer: Dict[str, Any] = Depends(get_current_farmer)
):
    """
    Returns the subgraph involved in an investigation.
    Strictly verifies investigation belongs to the caller.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM investigations WHERE id = ? AND farmer_id = ?", (inv_id, farmer["id"])).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Investigation not found or access denied")

    subgraph = graph_manager.get_subgraph_for_investigation(inv_id)
    return {
        "status": "success",
        "investigation_id": inv_id,
        "subgraph": subgraph
    }

@router.post("/cypher")
def run_admin_cypher_query(
    req: CypherQueryRequest,
    admin: Dict[str, Any] = Depends(get_current_admin)
):
    """
    Admin-only read-only openCypher query executor.
    Standard farmer accounts are strictly prohibited from arbitrary graph queries.
    Blocks any mutating clauses (CREATE, DELETE, SET, REMOVE, MERGE, DROP).
    """
    cleaned = req.query.strip().upper()
    disallowed_clauses = ["DELETE", "CREATE", "SET", "REMOVE", "MERGE", "DROP", "DETACH"]
    for clause in disallowed_clauses:
        if clause in cleaned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Mutating Cypher clause '{clause}' is strictly forbidden in read-only console."
            )

    try:
        results = graph_manager.query(req.query, req.params)
        return {
            "status": "success",
            "query": req.query,
            "results_count": len(results),
            "results": results[:100]  # Cap results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid Cypher query execution error")

@router.get("/status")
def get_graph_status(farmer: Dict[str, Any] = Depends(get_current_farmer)):
    """Returns sanitized status of graph engines without leaking backend connection credentials."""
    raw_status = graph_manager.get_status()
    return {
        "active_primary": raw_status.get("active_primary", "EmbeddedGraphEngine (Built-in)"),
        "falkordb_connected": raw_status.get("falkordb", {}).get("connected", False),
        "embedded_graph_ready": raw_status.get("embedded", {}).get("connected", True)
    }
