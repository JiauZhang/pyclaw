"""Tool registry factory."""

import os
from pathlib import Path
from typing import Optional

from .base import ToolRegistry
from .builtin import (
    BashTool,
    DateTimeTool,
    ExecTool,
    PythonTool,
    ReadFileTool,
    WriteFileTool,
    WeatherTool,
)


def create_default_tool_registry(
    workspace_dir: Optional[str] = None,
    enable_exec: bool = True,
    enable_file_ops: bool = True,
    enable_python: bool = True,
    exec_timeout: int = 60,
    exec_allowed_commands: Optional[list] = None
) -> ToolRegistry:
    """
    Create a default tool registry with common tools.

    Args:
        workspace_dir: Base directory for file operations (security sandbox)
        enable_exec: Whether to enable exec/bash tools
        enable_file_ops: Whether to enable file read/write tools
        enable_python: Whether to enable Python execution tool
        exec_timeout: Default timeout for exec commands
        exec_allowed_commands: List of allowed commands (None = all allowed)

    Returns:
        Configured ToolRegistry
    """
    registry = ToolRegistry()

    # Always available: basic info tools
    registry.register(DateTimeTool())
    registry.register(WeatherTool())

    # File operations (with workspace sandbox)
    if enable_file_ops:
        base_dir = workspace_dir or os.getcwd()
        registry.register(ReadFileTool(base_dir=base_dir))
        registry.register(WriteFileTool(base_dir=base_dir))

    # Python execution
    if enable_python:
        registry.register(PythonTool())

    # Exec/Bash tools (with security restrictions)
    if enable_exec:
        registry.register(ExecTool(
            allowed_commands=exec_allowed_commands,
            timeout=exec_timeout,
            workdir=workspace_dir
        ))
        registry.register(BashTool(
            allowed_commands=exec_allowed_commands,
            timeout=exec_timeout,
            workdir=workspace_dir
        ))

    return registry
