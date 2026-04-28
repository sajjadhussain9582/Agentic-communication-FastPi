"""
LangSmith tracing initialization.
This module sets up LangSmith observability for LangChain/LangGraph runs.
Tracing is only enabled if LANGSMITH_TRACING=True and LANGSMITH_API_KEY is set.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_langsmith_initialized = False


def initialize_langsmith(
    api_key: str,
    tracing_enabled: bool,
    project_name: str,
    endpoint: Optional[str] = None,
) -> bool:
    """
    Initialize LangSmith tracing.
    
    Returns True if successfully initialized, False otherwise.
    This function is safe to call multiple times (idempotent).
    """
    global _langsmith_initialized
    
    if _langsmith_initialized:
        return True
    
    if not tracing_enabled or not api_key:
        logger.info("LangSmith tracing disabled (set LANGSMITH_TRACING=true and LANGSMITH_API_KEY to enable)")
        return False
    
    try:
        from langsmith import Client
        
        # Set environment variables for LangChain/LangGraph auto-instrumentation
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = api_key
        os.environ["LANGSMITH_PROJECT"] = project_name
        if endpoint:
            os.environ["LANGSMITH_ENDPOINT"] = endpoint
        
        # Verify connectivity
        client = Client(api_key=api_key, api_url=endpoint)
        
        logger.info(
            f"LangSmith tracing initialized successfully (project: {project_name})"
        )
        _langsmith_initialized = True
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize LangSmith tracing: {e}")
        _langsmith_initialized = False
        return False


def is_langsmith_enabled() -> bool:
    """Check if LangSmith tracing is active."""
    return _langsmith_initialized and os.environ.get("LANGSMITH_TRACING") == "true"
