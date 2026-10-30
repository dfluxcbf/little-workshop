"""Every Job type's declared checklist, one module per pillar (`STEPS`: job type -> steps).

A type that is not listed has the single step `default_steps` gives it. The
pillar that runs a type reports these steps by key (`common.job_steps`), so a
list here and the code that reports it change together.
"""

from __future__ import annotations

from common.job_steps import StepSpec, default_steps
from common.step_specs import agent, build, deployment, policy, registry, requirements, version

STEPS: dict[str, tuple[StepSpec, ...]] = {}
for _module in (agent, build, deployment, policy, registry, requirements, version):
    STEPS.update(_module.STEPS)


def steps_for(job_type: str) -> tuple[StepSpec, ...]:
    return STEPS.get(job_type) or default_steps(job_type)
