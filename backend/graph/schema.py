"""
AgriTrace Graph Data Model & Schema Definitions
Defines node labels, edge relationships, property constraints, and indexing.
"""

NODE_TYPES = {
    "Farmer": {
        "description": "Authenticated farmer and primary identity root in the graph",
        "primary_key": "id",
        "indexed_properties": ["id", "username", "district", "state"]
    },
    "Farm": {
        "description": "Agricultural land holding managed by a farmer",
        "primary_key": "id",
        "indexed_properties": ["id", "farmer_id", "name"]
    },
    "Field": {
        "description": "Specific cultivated parcel within a farm",
        "primary_key": "id",
        "indexed_properties": ["id", "farm_id", "farmer_id", "soil_type"]
    },
    "CropCycle": {
        "description": "Specific seasonal crop cycle cultivated on a field",
        "primary_key": "id",
        "indexed_properties": ["id", "field_id", "crop_name", "status"]
    },
    "Observation": {
        "description": "Time-stamped physical, sensor, or user measurement (never overwritten)",
        "primary_key": "id",
        "indexed_properties": ["id", "crop_cycle_id", "observation_type", "observed_at"]
    },
    "Evidence": {
        "description": "Uploaded verification artifact (PDF report, lab card, photo)",
        "primary_key": "id",
        "indexed_properties": ["id", "farmer_id", "file_type"]
    },
    "Investigation": {
        "description": "Autonomous investigative process triggered by an agricultural issue",
        "primary_key": "id",
        "indexed_properties": ["id", "farmer_id", "crop_cycle_id", "status"]
    },
    "Hypothesis": {
        "description": "Candidate causal explanation evaluated against observations and domain rules",
        "primary_key": "id",
        "indexed_properties": ["id", "investigation_id", "status"]
    },
    "Finding": {
        "description": "Evidence-backed conclusion produced by an investigation",
        "primary_key": "id",
        "indexed_properties": ["id", "investigation_id", "confidence_score"]
    },
    "Dataset": {
        "description": "Imported scientific, sensor, or laboratory dataset",
        "primary_key": "id",
        "indexed_properties": ["id", "name"]
    },
    "DatasetRecord": {
        "description": "Specific benchmark or sample record within an imported dataset",
        "primary_key": "id",
        "indexed_properties": ["id", "dataset_id", "crop", "soil_type"]
    },
    "AgriculturalEntity": {
        "description": "Domain knowledge entity (e.g. Rice, Soft Red Wheat, Nitrogen Deficiency)",
        "primary_key": "name",
        "indexed_properties": ["name", "category"]
    }
}

RELATIONSHIP_TYPES = [
    ("Farmer", "MANAGES", "Farm"),
    ("Farm", "CONTAINS", "Field"),
    ("Field", "HAS_CROP_CYCLE", "CropCycle"),
    ("CropCycle", "HAS_OBSERVATION", "Observation"),
    ("CropCycle", "HAS_INVESTIGATION", "Investigation"),
    ("Investigation", "CONTAINS_OBSERVATION", "Observation"),
    ("Observation", "SUPPORTED_BY", "Evidence"),
    ("Investigation", "PRODUCED", "Finding"),
    ("Finding", "SUPPORTED_BY", "Evidence"),
    ("Investigation", "CONSIDERED", "Hypothesis"),
    ("Dataset", "CONTAINS_RECORD", "DatasetRecord"),
    ("Observation", "DERIVED_FROM", "DatasetRecord"),
    ("Finding", "DERIVED_FROM", "DatasetRecord"),
    ("CropCycle", "CULTIVATES", "AgriculturalEntity"),
    ("Hypothesis", "TARGETS", "AgriculturalEntity")
]

CYPHER_INDEX_QUERIES = [
    "CREATE INDEX IF NOT EXISTS FOR (n:Farmer) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Farm) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Field) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:CropCycle) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Observation) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Evidence) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Investigation) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Finding) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Dataset) ON (n.id)",
    "CREATE INDEX IF NOT EXISTS FOR (n:DatasetRecord) ON (n.id)"
]
