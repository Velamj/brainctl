"""Command module: epoch"""
from agentmemory._impl import cmd_epoch_detect, cmd_epoch_create, cmd_epoch_list, detect_epoch_boundaries, suggest_epoch_ranges

__all__ = ['cmd_epoch_detect', 'cmd_epoch_create', 'cmd_epoch_list', 'detect_epoch_boundaries', 'suggest_epoch_ranges']
