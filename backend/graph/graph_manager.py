"""
Graph Manager Singleton
Orchestrates FalkorDB as primary graph database and EmbeddedGraphEngine
as reliable persistent fallback and local mirror.
"""

import logging
from typing import Any, Dict, List, Optional
from backend.graph.graph_interface import GraphInterface
from backend.graph.falkordb_service import FalkorDBService
from backend.graph.embedded_graph import EmbeddedGraphEngine

logger = logging.getLogger("agritrace.graph_manager")

class GraphManager(GraphInterface):
    def __init__(self):
        self.falkor_service = FalkorDBService()
        self.embedded_service = EmbeddedGraphEngine()

    @property
    def is_falkordb_active(self) -> bool:
        return self.falkor_service.check_connection()

    def create_or_update_node(self, label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        # Always write to embedded graph engine for persistent local copy
        res = self.embedded_service.create_or_update_node(label, properties)

        # If FalkorDB is active, write to FalkorDB as well
        if self.is_falkordb_active:
            try:
                self.falkor_service.create_or_update_node(label, properties)
            except Exception as e:
                logger.error(f"Failed to sync node to FalkorDB: {e}")

        return res

    def create_relationship(self, from_id: str, rel_type: str, to_id: str, properties: Optional[Dict[str, Any]] = None) -> bool:
        res = self.embedded_service.create_relationship(from_id, rel_type, to_id, properties)

        if self.is_falkordb_active:
            try:
                self.falkor_service.create_relationship(from_id, rel_type, to_id, properties)
            except Exception as e:
                logger.error(f"Failed to sync relationship to FalkorDB: {e}")

        return res

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if self.is_falkordb_active:
            try:
                return self.falkor_service.query(cypher, params)
            except Exception as e:
                logger.warning(f"FalkorDB query error, fallback to embedded: {e}")
                return self.embedded_service.query(cypher, params)
        return self.embedded_service.query(cypher, params)

    def get_subgraph_for_farmer(self, farmer_id: str, depth: int = 3) -> Dict[str, Any]:
        if self.is_falkordb_active:
            try:
                res = self.falkor_service.get_subgraph_for_farmer(farmer_id, depth)
                if res.get("node_count", 0) > 0:
                    return res
            except Exception as e:
                logger.warning(f"FalkorDB subgraph query error: {e}")
        return self.embedded_service.get_subgraph_for_farmer(farmer_id, depth)

    def get_subgraph_for_investigation(self, investigation_id: str) -> Dict[str, Any]:
        if self.is_falkordb_active:
            try:
                res = self.falkor_service.get_subgraph_for_investigation(investigation_id)
                if res.get("node_count", 0) > 0:
                    return res
            except Exception as e:
                logger.warning(f"FalkorDB investigation subgraph error: {e}")
        return self.embedded_service.get_subgraph_for_investigation(investigation_id)

    def find_paths_between(self, from_id: str, to_id: str, max_depth: int = 4) -> List[List[Dict[str, Any]]]:
        if self.is_falkordb_active:
            try:
                res = self.falkor_service.find_paths_between(from_id, to_id, max_depth)
                if res:
                    return res
            except Exception as e:
                logger.warning(f"FalkorDB pathfinding error: {e}")
        return self.embedded_service.find_paths_between(from_id, to_id, max_depth)

    def get_status(self) -> Dict[str, Any]:
        falkor_status = self.falkor_service.get_status()
        embedded_status = self.embedded_service.get_status()

        active_primary = "FalkorDB" if self.is_falkordb_active else "EmbeddedGraphEngine (Fallback)"
        return {
            "active_primary": active_primary,
            "falkordb": falkor_status,
            "embedded": embedded_status
        }

graph_manager = GraphManager()
