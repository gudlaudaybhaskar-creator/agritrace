from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class GraphInterface(ABC):
    @abstractmethod
    def create_or_update_node(self, label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Creates or updates a graph node with the given label and properties (must contain 'id')."""
        pass

    @abstractmethod
    def create_relationship(self, from_id: str, rel_type: str, to_id: str, properties: Optional[Dict[str, Any]] = None) -> bool:
        """Creates a directed relationship between from_id and to_id."""
        pass

    @abstractmethod
    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Executes an openCypher query with parameters."""
        pass

    @abstractmethod
    def get_subgraph_for_farmer(self, farmer_id: str, depth: int = 3) -> Dict[str, Any]:
        """Returns nodes and edges forming the subgraph belonging to a farmer."""
        pass

    @abstractmethod
    def get_subgraph_for_investigation(self, investigation_id: str) -> Dict[str, Any]:
        """Returns nodes and edges directly involved in an investigation."""
        pass

    @abstractmethod
    def find_paths_between(self, from_id: str, to_id: str, max_depth: int = 4) -> List[List[Dict[str, Any]]]:
        """Finds multi-hop paths connecting two nodes."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Returns health status, node count, edge count, and backend engine details."""
        pass
