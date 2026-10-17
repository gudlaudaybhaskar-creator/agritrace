"""
FalkorDB Graph Database Service
Implements GraphInterface using official falkordb-py client.
Executes parameterized openCypher queries, multi-hop path traversals,
and manages graph constraints and indexes.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from backend.config import settings
from backend.graph.graph_interface import GraphInterface
from backend.graph.schema import CYPHER_INDEX_QUERIES

logger = logging.getLogger("agritrace.falkordb")

class FalkorDBService(GraphInterface):
    def __init__(self):
        self.host = settings.FALKORDB_HOST
        self.port = settings.FALKORDB_PORT
        self.password = settings.FALKORDB_PASSWORD
        self.graph_name = settings.FALKORDB_GRAPH_NAME
        self.driver = None
        self.graph = None
        self.is_connected = False
        self.last_check_time = 0
        self.retry_cooldown_seconds = 30
        self.logged_failure_once = False
        self._connect()

    def _connect(self):
        import time
        self.last_check_time = time.time()
        try:
            from falkordb import FalkorDB
            if self.password:
                self.driver = FalkorDB(host=self.host, port=self.port, password=self.password)
            else:
                self.driver = FalkorDB(host=self.host, port=self.port)
            self.graph = self.driver.select_graph(self.graph_name)
            self.graph.query("RETURN 1")
            self.is_connected = True
            self.logged_failure_once = False
            logger.info(f"Connected to FalkorDB at {self.host}:{self.port}, graph: {self.graph_name}")
            self._ensure_indexes()
        except Exception as e:
            self.is_connected = False
            self.driver = None
            self.graph = None
            if not self.logged_failure_once:
                logger.info(f"FalkorDB service not detected on {self.host}:{self.port}. Operating in persistent local graph engine mode.")
                self.logged_failure_once = True

    def _ensure_indexes(self):
        if not self.is_connected or not self.graph:
            return
        for query in CYPHER_INDEX_QUERIES:
            try:
                self.graph.query(query)
            except Exception as e:
                logger.warning(f"FalkorDB index creation notice for '{query}': {e}")

    def check_connection(self) -> bool:
        import time
        if not self.is_connected or not self.graph:
            # Throttle reconnection checks so it doesn't spam on every node insertion
            if time.time() - self.last_check_time > self.retry_cooldown_seconds:
                self._connect()
        return self.is_connected

    def create_or_update_node(self, label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        node_id = str(properties.get("id"))
        cypher = f"""
        MERGE (n:{label} {{id: $id}})
        SET n += $props
        RETURN n
        """
        params = {"id": node_id, "props": properties}
        res = self.graph.query(cypher, params)
        return {"id": node_id, "label": label, "properties": properties}

    def create_relationship(self, from_id: str, rel_type: str, to_id: str, properties: Optional[Dict[str, Any]] = None) -> bool:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        props = properties or {}
        cypher = f"""
        MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $props
        RETURN count(r) as created
        """
        params = {"from_id": str(from_id), "to_id": str(to_id), "props": props}
        res = self.graph.query(cypher, params)
        return bool(res.result_set and len(res.result_set) > 0)

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        params = params or {}
        res = self.graph.query(cypher, params)
        output = []
        if res.result_set:
            headers = [h[1] if isinstance(h, tuple) else str(h) for h in res.header] if hasattr(res, "header") else []
            for row in res.result_set:
                if headers and len(headers) == len(row):
                    output.append({headers[i]: row[i] for i in range(len(row))})
                else:
                    output.append({"result": row})
        return output

    def get_subgraph_for_farmer(self, farmer_id: str, depth: int = 3) -> Dict[str, Any]:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        cypher = f"""
        MATCH p = (f:Farmer {{id: $farmer_id}})-[*1..{depth}]-(m)
        RETURN nodes(p) as path_nodes, relationships(p) as path_rels
        """
        res = self.graph.query(cypher, {"farmer_id": farmer_id})
        nodes_map = {}
        edges_map = {}

        for row in res.result_set:
            path_nodes = row[0]
            path_rels = row[1]
            for n in path_nodes:
                nid = n.properties.get("id", str(n.id))
                label = n.labels[0] if n.labels else "Entity"
                nodes_map[nid] = {"id": nid, "label": label, "properties": n.properties}
            for r in path_rels:
                rid = f"{r.src_node}_{r.relation}_{r.dest_node}"
                edges_map[rid] = {
                    "id": rid,
                    "source": str(r.src_node),
                    "type": r.relation,
                    "target": str(r.dest_node),
                    "properties": r.properties
                }

        return {
            "root_id": farmer_id,
            "node_count": len(nodes_map),
            "edge_count": len(edges_map),
            "nodes": list(nodes_map.values()),
            "edges": list(edges_map.values())
        }

    def get_subgraph_for_investigation(self, investigation_id: str) -> Dict[str, Any]:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        cypher = """
        MATCH p = (inv:Investigation {id: $inv_id})-[*1..2]-(m)
        RETURN nodes(p) as path_nodes, relationships(p) as path_rels
        """
        res = self.graph.query(cypher, {"inv_id": investigation_id})
        nodes_map = {}
        edges_map = {}
        for row in res.result_set:
            for n in row[0]:
                nid = n.properties.get("id", str(n.id))
                label = n.labels[0] if n.labels else "Entity"
                nodes_map[nid] = {"id": nid, "label": label, "properties": n.properties}
            for r in row[1]:
                rid = f"{r.src_node}_{r.relation}_{r.dest_node}"
                edges_map[rid] = {
                    "id": rid,
                    "source": str(r.src_node),
                    "type": r.relation,
                    "target": str(r.dest_node),
                    "properties": r.properties
                }

        return {
            "investigation_id": investigation_id,
            "node_count": len(nodes_map),
            "edge_count": len(edges_map),
            "nodes": list(nodes_map.values()),
            "edges": list(edges_map.values())
        }

    def find_paths_between(self, from_id: str, to_id: str, max_depth: int = 4) -> List[List[Dict[str, Any]]]:
        if not self.check_connection():
            raise ConnectionError(f"FalkorDB not connected on {self.host}:{self.port}")

        cypher = f"""
        MATCH p = (a {{id: $from_id}})-[*1..{max_depth}]-(b {{id: $to_id}})
        RETURN p LIMIT 10
        """
        res = self.graph.query(cypher, {"from_id": from_id, "to_id": to_id})
        formatted_paths = []
        for row in res.result_set:
            p = row[0]
            step_list = []
            for n in p.nodes:
                step_list.append({"node_id": n.properties.get("id", str(n.id)), "label": n.labels[0] if n.labels else ""})
            formatted_paths.append(step_list)
        return formatted_paths

    def get_status(self) -> Dict[str, Any]:
        connected = self.check_connection()
        if not connected:
            return {
                "engine": "FalkorDB",
                "connected": False,
                "host": self.host,
                "port": self.port,
                "graph_name": self.graph_name,
                "message": f"Connection refused or FalkorDB service not active on {self.host}:{self.port}"
            }

        try:
            nodes_res = self.graph.query("MATCH (n) RETURN count(n) as node_count")
            edges_res = self.graph.query("MATCH ()-[r]->() RETURN count(r) as edge_count")
            nc = nodes_res.result_set[0][0] if nodes_res.result_set else 0
            ec = edges_res.result_set[0][0] if edges_res.result_set else 0
            return {
                "engine": "FalkorDB",
                "connected": True,
                "host": self.host,
                "port": self.port,
                "graph_name": self.graph_name,
                "node_count": nc,
                "edge_count": ec
            }
        except Exception as e:
            return {
                "engine": "FalkorDB",
                "connected": False,
                "error": str(e)
            }
