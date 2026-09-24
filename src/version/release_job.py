"""`version.release` — the Dashboard's Release job: release the latest
committed integration branch (`dev`) in one go, but only once the chosen
conditions hold.

1. Conditions, checked before anything is branched:
   - a minimum delta (lines changed since the last production tag) and a
     minimum number of patch bumps since the last release, combined with
     "and" or "or";
   - optionally, all tests pass / all requirements pass (one test run
     serves both) / all policies pass.
2. `release start`, a CHANGELOG.md entry on the release branch, `release finish`.
3. If the project's app is running, relaunch it on the branch it runs; for
   little-workshop itself, redeploy every one of its enabled pillars.

Other pillars are reached through `ReleaseServices` (see
`version/release_services.py` for the real one), so this module stays pure
git plus decisions, and its tests can fake every other pillar.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from version.commands.release import cmd_release_finish, cmd_release_start
from version.common import get_integration_branch, get_production_branch
from version.configstore import load_release_finish_state
from version.gitops import checkout, commit_file, current_branch, get_commit_sha, is_ancestor, is_dirty, list_tags, run, write_file_in_repo
from version.stages import GitflowVersion

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's own empty tree: "since the beginning"
CHANGELOG_FILE = "CHANGELOG.md"
CHANGELOG_MAX_ENTRIES = 200
# Subjects that are bookkeeping, not changes a reader of the changelog cares about.
_CHANGELOG_SKIP = re.compile(r"^(chore\b|chore\(|Back-merge |Merge branch )", re.IGNORECASE)
_SHORTSTAT = re.compile(r"(\d+) (insertion|deletion)")
# Test verdicts that mean "did not pass". A skip is a test declaring itself not
# applicable, never a failure.
_FAILING_VERDICTS = ("failed", "error")
TEST_RUN_TIMEOUT_SECONDS = 1920.0  # the run's own 1800s ceiling, plus worktree setup
POLICY_CHECK_TIMEOUT_SECONDS = 1920.0  # the sandbox's own 1800s ceiling, plus image start
# Their restart interrupts the redeploy machinery itself, so they go last
# (the same order the Dashboard's Pillars page uses).
SELF_AFFECTING_PILLARS = ("deployment", "orchestration")


class ReleaseServices(Protocol):
    def create_job(self, template: dict) -> str: ...
    def wait_for_job(self, job_id: str, timeout: float) -> dict: ...
    def test_suites(self, project_id: str) -> list[dict]: ...
    def requirements_report(self, project_id: str) -> dict: ...
    def policies(self) -> list[dict]: ...
    def deployment_status(self, project_id: str) -> dict | None: ...
    def pillars(self) -> dict[str, dict]: ...


class ReleaseRefused(Exception):
    """A condition did not hold, or the checkout can't be released from: nothing was changed."""

    def __init__(self, message: str, checks: list[dict] | None = None) -> None:
        super().__init__(message)
        self.checks = checks or []


class ReleaseFailed(Exception):
    """Something went wrong after the release branch was started."""


@dataclass(frozen=True)
class Conditions:
    min_delta: int = 0
    min_patch_bumps: int = 0
    combine: str = "and"  # "and" | "or": how the two minimums combine
    all_tests: bool = False
    all_requirements: bool = False
    all_policies: bool = False

    @classmethod
    def from_params(cls, params: dict) -> "Conditions":
        def count(name: str) -> int:
            value = params.get(name, 0) or 0
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ReleaseRefused(f"{name} must be a whole number, got {value!r}") from None
            if number < 0:
                raise ReleaseRefused(f"{name} cannot be negative")
            return number

        combine = str(params.get("combine", "and")).lower()
        if combine not in ("and", "or"):
            raise ReleaseRefused(f"combine must be 'and' or 'or', got {combine!r}")
        return cls(
            min_delta=count("min_delta"), min_patch_bumps=count("min_patch_bumps"), combine=combine,
            all_tests=bool(params.get("all_tests")), all_requirements=bool(params.get("all_requirements")),
            all_policies=bool(params.get("all_policies")),
        )


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


# -- git facts ---------------------------------------------------------------


def last_production_tag(repo: Path, production_branch: str) -> str | None:
    """The newest stable `vX.Y.Z` tag on the production branch's history."""
    best: tuple[GitflowVersion, str] | None = None
    for tag in list_tags(repo, "v*"):
        try:
            ver = GitflowVersion.parse(tag[1:])
        except ValueError:
            continue
        if not ver.is_stable() or not is_ancestor(repo, tag, production_branch):
            continue
        key = (ver.major, ver.minor, ver.patch)
        if best is None or key > (best[0].major, best[0].minor, best[0].patch):
            best = (ver, tag)
    return best[1] if best else None


def lines_changed(repo: Path, base: str | None, ref: str) -> int:
    """Lines added plus lines removed between `base` (the beginning, if None) and `ref`."""
    out = run(["git", "diff", "--shortstat", base or EMPTY_TREE, ref], repo).stdout
    return sum(int(n) for n, _ in _SHORTSTAT.findall(out))


def _version_at(repo: Path, ref: str) -> GitflowVersion:
    shown = run(["git", "show", f"{ref}:VERSION"], repo, check=False)
    if shown.returncode != 0:
        return GitflowVersion(0, 1, 0)  # read_version's own default for a repo without one
    return GitflowVersion.parse(shown.stdout.strip())


def patch_bumps(repo: Path, integration_ref: str, production_ref: str, released: bool) -> tuple[int, str]:
    """How many patch bumps the integration branch carries since the last release.

    A release leaves production at X.(Y+1).0 and starts the integration branch's
    next patch line at X.(Y+1).1 (see `cmd_release_finish`); every finished
    feature/improvement bumps the patch after that. So the count is the
    integration patch minus the patch its line started at: production's patch
    plus one after a release, production's own patch before any release. A
    line that has moved past production's major.minor started at .0."""
    integ = _version_at(repo, integration_ref)
    prod = _version_at(repo, production_ref)
    if (integ.major, integ.minor) != (prod.major, prod.minor):
        start = 0
    else:
        start = prod.patch + 1 if released else prod.patch
    return max(0, integ.patch - start), f"{integration_ref} is at {integ}, its patch line started at .{start}"


def merge_check(repo: Path, integration_ref: str, production_branch: str) -> Check:
    """Would the release merge into production go through? The release branch is
    the integration branch plus a VERSION commit, so a dry run of merging the
    integration branch into production answers it without touching anything."""
    probe = run(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", production_branch, integration_ref],
                repo, check=False)
    if probe.returncode == 0:
        return Check(f"merges cleanly into {production_branch}", True, f"{integration_ref} merges into {production_branch} without conflicts")
    conflicted = [line for line in probe.stdout.splitlines()[1:] if line.strip()]
    return Check(f"merges cleanly into {production_branch}", False,
                 f"merging {integration_ref} into {production_branch} would conflict in {', '.join(conflicted) or 'some files'}; "
                 f"{production_branch} has changes {integration_ref} lacks — merge {production_branch} into {integration_ref} first")


def delta_checks(repo: Path, conditions: Conditions, integration_ref: str, production_branch: str) -> list[Check]:
    tag = last_production_tag(repo, production_branch)
    since = f"since {tag}" if tag else "since the first commit (no release yet)"
    changed = lines_changed(repo, tag, integration_ref)
    bumps, bumps_detail = patch_bumps(repo, integration_ref, production_branch, released=tag is not None)
    delta = Check("minimum delta", changed >= conditions.min_delta,
                  f"{changed} lines changed {since}; at least {conditions.min_delta} required")
    patch = Check("minimum patch bumps", bumps >= conditions.min_patch_bumps,
                  f"{bumps} patch bumps ({bumps_detail}); at least {conditions.min_patch_bumps} required")
    passed = (delta.passed and patch.passed) if conditions.combine == "and" else (delta.passed or patch.passed)
    combined = Check(f"delta {conditions.combine} patch bumps", passed,
                     f"{'both' if conditions.combine == 'and' else 'either'} of the two minimums must hold")
    return [delta, patch, combined]


# -- conditions that ask other pillars ---------------------------------------


def suite_checks(services: ReleaseServices, project_id: str, commit: str, conditions: Conditions) -> list[Check]:
    """One run of every test suite serves both test conditions."""
    names = [n for n, on in (("all tests pass", conditions.all_tests), ("all requirements pass", conditions.all_requirements)) if on]
    if not names:
        return []
    suites = services.test_suites(project_id)
    if not suites:
        return [Check(name, False, "the project has no test suites to run") for name in names]
    job = services.wait_for_job(services.create_job({
        "type": "requirements.run_tests", "target_pillar": "requirements", "project_id": project_id,
        "params": {"commitish": commit, "suite_ids": [s["id"] for s in suites]}, "queue_key": None,
    }), TEST_RUN_TIMEOUT_SECONDS)
    if job.get("status") != "succeeded":
        reason = f"the test run did not complete ({job.get('status')}): {job.get('error') or 'no error given'}"
        return [Check(name, False, reason) for name in names]

    checks = []
    if conditions.all_tests:
        summary = (job.get("result") or {}).get("suites", [])
        ran = sum(s.get("ran", 0) for s in summary)
        failing = {v: sum(s.get("verdicts", {}).get(v, 0) for s in summary) for v in _FAILING_VERDICTS}
        bad = sum(failing.values())
        checks.append(Check("all tests pass", bad == 0 and ran > 0,
                            f"{ran} tests ran at {commit[:12]}; " + (", ".join(f"{n} {v}" for v, n in failing.items() if n) or "none failed")))
    if conditions.all_requirements:
        rows = services.requirements_report(project_id).get("requirements", [])
        published = [r for r in rows if r.get("status") == "published"]  # drafts are not validated yet: ignored
        not_done = [f"{r['key']} ({r.get('verdict')})" for r in published if not r.get("done")]
        checks.append(Check("all requirements pass", not not_done,
                            f"{len(published) - len(not_done)} of {len(published)} published requirements pass"
                            + (f"; not passing: {', '.join(not_done[:20])}" + (" …" if len(not_done) > 20 else "") if not_done else "")))
    return checks


def policy_check(services: ReleaseServices, project_id: str) -> Check:
    """Run every enabled policy associated with the project against its checkout
    (now on the integration branch). A failing check blocks unless the project
    has gating switched off for that policy (recorded, never blocking)."""
    selected = []
    for policy in services.policies():
        if not policy.get("enabled", True):
            continue
        for sel in policy.get("projects", []):
            if sel.get("project_id") == project_id:
                selected.append((policy, bool(sel.get("gating_enabled", True))))
    if not selected:
        return Check("all policies pass", True, "no enabled policy applies to this project")
    job_ids = [
        (policy, gating, services.create_job({
            "type": "policy.check_project", "target_pillar": "policy", "project_id": project_id,
            "params": {"policy_id": policy["id"], "gating": gating}, "queue_key": None,
        }))
        for policy, gating in selected
    ]
    failing = []
    for policy, gating, job_id in job_ids:
        job = services.wait_for_job(job_id, POLICY_CHECK_TIMEOUT_SECONDS)
        if job.get("status") != "succeeded":
            failing.append(f"{policy['topic']} (check did not complete: {job.get('error') or job.get('status')})")
        elif (job.get("result") or {}).get("outcome") == "fail" and gating:
            failing.append(policy["topic"])
    return Check("all policies pass", not failing,
                 f"{len(selected) - len(failing)} of {len(selected)} policies pass" + (f"; failing: {', '.join(failing)}" if failing else ""))


# -- changelog ---------------------------------------------------------------


def changelog_entries(repo: Path, since_tag: str | None, ref: str) -> list[str]:
    span = f"{since_tag}..{ref}" if since_tag else ref
    log = run(["git", "log", "--first-parent", "--format=%h%x09%s", span], repo).stdout
    entries = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\t")
        if subject and not _CHANGELOG_SKIP.match(subject):
            entries.append(f"- {subject} ({sha})")
    return entries


def write_changelog(repo: Path, version: str, entries: list[str], *, today: date | None = None) -> Path:
    shown = entries[:CHANGELOG_MAX_ENTRIES]
    if len(entries) > len(shown):
        shown.append(f"- …and {len(entries) - len(shown)} more")
    section = f"## {version} — {(today or date.today()).isoformat()}\n\n" + ("\n".join(shown) if shown else "- No changes recorded.") + "\n"
    path = repo / CHANGELOG_FILE
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "# Changelog\n\n"
    body = existing[len(header):] if existing.startswith(header) else existing
    return write_file_in_repo(repo, CHANGELOG_FILE, header + section + ("\n" + body if body.strip() else ""))


# -- relaunch ----------------------------------------------------------------


def is_little_workshop(repo: Path) -> bool:
    own = os.environ.get("LITTLE_WORKSHOP_REPO_DIR")
    return bool(own) and Path(own).resolve() == repo.resolve()


def relaunch(services: ReleaseServices, project_id: str, repo: Path, integration_branch: str, release_job_id: str) -> dict:
    """Queue the relaunch as Jobs that wait for this release Job to succeed, so
    they only start once it is done — and a pillar restarting can never cut
    this Job off mid-way."""
    if is_little_workshop(repo):
        commit = get_commit_sha(repo, integration_branch)
        chosen = sorted(
            (name for name, info in services.pillars().items() if info.get("status") != "disabled"),
            key=lambda name: (name in SELF_AFFECTING_PILLARS, SELF_AFFECTING_PILLARS.index(name) if name in SELF_AFFECTING_PILLARS else 0, name),
        )
        job_ids, previous = [], release_job_id
        for name in chosen:  # one after another: never two pillars restarting at once
            previous = services.create_job({
                "type": "deployment.redeploy_pillar", "target_pillar": "deployment", "project_id": name,
                "params": {"pillar": name, "commit": commit}, "queue_key": name, "depends_on": [previous],
            })
            job_ids.append(previous)
        return {"action": "redeploy_pillars", "pillars": chosen, "commit": commit, "job_ids": job_ids}

    status = services.deployment_status(project_id)
    if status is None or not status.get("listening"):
        return {"action": "none", "reason": "the app is not running"}
    if status.get("external"):
        return {"action": "none", "reason": "the app is managed outside Deployment"}
    branch = status.get("branch") or integration_branch
    job_id = services.create_job({
        "type": "deployment.deploy", "target_pillar": "deployment", "project_id": project_id,
        "params": {"branch": branch}, "queue_key": project_id, "depends_on": [release_job_id],
    })
    return {"action": "relaunch", "branch": branch, "job_ids": [job_id]}


# -- the job -----------------------------------------------------------------


def run_release(repo: Path, project_id: str, params: dict, services: ReleaseServices, *, job_id: str) -> dict:
    conditions = Conditions.from_params(params)
    integ = get_integration_branch(repo)
    prod = get_production_branch(repo)
    if load_release_finish_state(repo):
        raise ReleaseRefused("a release finish is already in progress in this checkout; continue or abort it first")
    if is_dirty(repo):
        raise ReleaseRefused("the project checkout has uncommitted changes; commit or stash them first")

    original = current_branch(repo)
    if original != integ:
        checkout(repo, integ)
    try:
        commit = get_commit_sha(repo, integ)
        checks = [merge_check(repo, integ, prod), *delta_checks(repo, conditions, integ, prod)]
        if all(c.passed for c in checks):  # the costly checks only once the cheap ones hold
            checks += suite_checks(services, project_id, commit, conditions)
            if conditions.all_policies:
                checks.append(policy_check(services, project_id))
        failed = [c for c in checks if not c.passed]
        if failed:
            raise ReleaseRefused("release conditions not met: " + "; ".join(f"{c.name} — {c.detail}" for c in failed),
                                 [asdict(c) for c in checks])
    except BaseException:
        if original != integ and current_branch(repo) != original:
            checkout(repo, original)
        raise

    base_tag = last_production_tag(repo, prod)
    started = cmd_release_start(repo)
    if not started.success:
        if original != integ:
            checkout(repo, original)
        raise ReleaseFailed(f"release start failed: {started.message}")
    version = started.data["version"]

    changelog = write_changelog(repo, version, changelog_entries(repo, base_tag, integ))
    commit_file(repo, changelog, f"docs: changelog for {version}")

    finished = cmd_release_finish(repo)
    if not finished.success:
        # Mid-release (e.g. a merge conflict): leave the checkout where it is, so
        # `version release finish --continue`/`--abort` can pick it up.
        raise ReleaseFailed(f"release finish did not complete: {finished.message}")

    if original != integ and current_branch(repo) != original:
        checkout(repo, original)
    return {
        **finished.data,
        "version": version,
        "checks": [asdict(c) for c in checks],
        "changelog": CHANGELOG_FILE,
        "relaunch": relaunch(services, project_id, repo, integ, job_id),
    }
