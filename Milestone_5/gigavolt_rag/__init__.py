"""Project Gigavolt Milestone 5: multi-model hybrid retrieval and RAG engine.

Data sources
------------
* MariaDB (``gigavolt_silver``)             -> structured ERP facts       (citation prefix ``S``)
* ChromaDB HNSW (Milestone 4 collection)    -> technical PDF passages     (citation prefix ``V``)
* MongoDB (``gigavolt_iot.bms_telemetry``)  -> IoT/BMS telemetry events   (citation prefix ``D``)

The graph database (Neo4j) was removed from Milestone 5 by the instructor's
Nov 2 announcement, so prefix ``G`` is reserved but unused.
"""

__version__ = "5.0.0"
