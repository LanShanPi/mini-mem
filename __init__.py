"""
MiniMem - 类人记忆网络

一个基于 Neo4j 的简化记忆存储系统。
"""
from .memory_graph import MemoryGraph, get_graph, close_graph
from .store import store_memory, store_event, analyze_memory
from .recall import recall, recall_detailed, related_to
from .maintenance import daily_decay, cleanup_isolated_nodes, get_stats

__version__ = "0.1.0"
__all__ = [
    "MemoryGraph",
    "get_graph",
    "close_graph",
    "store_memory",
    "store_event",
    "analyze_memory",
    "recall",
    "recall_detailed",
    "related_to",
    "daily_decay",
    "cleanup_isolated_nodes",
    "get_stats",
]
