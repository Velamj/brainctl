"""Command module: entity"""
from agentmemory._impl import (
    cmd_entity_create, cmd_entity_get, cmd_entity_search, cmd_entity_list,
    cmd_entity_update, cmd_entity_observe, cmd_entity_relate, cmd_entity_delete,
    cmd_entity_autolink, _fts5_entity_match, _AUTOLINK_MIN_NAME_LENGTH,
    VALID_ENTITY_TYPES,
)

__all__ = [
    'cmd_entity_create', 'cmd_entity_get', 'cmd_entity_search', 'cmd_entity_list',
    'cmd_entity_update', 'cmd_entity_observe', 'cmd_entity_relate', 'cmd_entity_delete',
    'cmd_entity_autolink', '_fts5_entity_match', '_AUTOLINK_MIN_NAME_LENGTH',
    'VALID_ENTITY_TYPES',
]
