"""The Dashboard's routes, one module per area; each `build_router(ctx)` returns its APIRouter."""

from dashboard.routes import (
    assets, backlog, data, docs, incidents, jobs, policies, projects, requirements, schedule, statistics, system, tasks, usage,
)

# Included in this order, which keeps the order in which overlapping paths are matched.
AREAS = (system, data, statistics, usage, incidents, requirements, projects, docs, assets, backlog, tasks, jobs, schedule, policies)
