"""Re-exports the shared registry-resolution helper — moved to
`common/registry_client.py` once Deployment & Servers (M4) needed the
identical capability. Kept here so existing imports/tests don't churn.
"""

from __future__ import annotations

from common.registry_client import ProjectResolutionError, resolve_project_path

__all__ = ["ProjectResolutionError", "resolve_project_path"]
