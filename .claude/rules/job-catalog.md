---
paths:
  - "src/dashboard/job_catalog.py"
  - "src/dashboard/app.py"
---
# User-triggered jobs

A job a user can start from the Dashboard is a typed `JobSpec` entry in `src/dashboard/job_catalog.py`: project,
then job, then typed arguments. Never a free-form job, and never one whose content Claude drafts.
