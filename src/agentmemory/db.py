"""
brainctl shared database layer — re-exports from _impl.

Import from here for shared utilities:
    from agentmemory.db import get_db, json_out, rows_to_list
"""
from agentmemory._impl import (
    # Constants
    DB_PATH, BLOBS_DIR, BACKUPS_DIR, VERSION,
    VALID_MEMORY_CATEGORIES, VALID_EVENT_TYPES, VALID_ENTITY_TYPES,
    VALID_TASK_STATUSES, VALID_PRIORITIES,
    RECENCY_LAMBDA, REFLEXION_BOOST,
    VEC_DYLIB, OLLAMA_EMBED_URL, EMBED_MODEL, EMBED_DIMENSIONS,
    # DB helpers
    get_db, log_access, _estimate_tokens,
    json_out, row_to_dict, rows_to_list,
    # Time/recency helpers
    _now_ts, _sanitize_fts_query, _scope_lambda,
    _days_since, _temporal_weight, _is_reflexion, _age_str,
    # Vector helpers
    _find_vec_dylib, _try_get_db_with_vec, _get_db_with_vec,
    _embed_query_safe, _embed_query,
    _try_vec_delete_memories,
    # Search helpers
    _rrf_fuse, _graph_expand, _mmr_rerank,
    _source_weight_cache, _get_source_weight,
)
