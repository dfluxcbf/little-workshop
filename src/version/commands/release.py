"""`release` command implementations — ported from `little-versions`'
`commands/release.py`: release-candidate cut/list/promote, and the
production+dev-backmerge finish sequence."""

from __future__ import annotations

from pathlib import Path

from version.common import CommandResult, get_integration_branch, get_production_branch, require_branch_exists, require_clean_tree, run_version_hooks
from version.configstore import clear_release_finish_state, get_promoted_rc, load_release_finish_state, save_release_finish_state, set_promoted_rc
from version.gitops import (
    DirtyWorkingTreeError, MergeConflictError, branch_exists, checkout, commit_file, commit_if_dirty,
    create_and_checkout, create_annotated_tag, current_branch, get_tag_commit, is_ancestor,
    is_dirty, is_merge_in_progress, list_tags, merge_abort, merge_commit, merge_no_ff, run, tag_exists,
)
from version.stages import GitflowVersion, bump_minor, bump_patch, normalize_to_stable
from version.workflows import checkout_existing_branch, clear_untracked_state_file, delete_branch_if_configured, merge_conflict_result, read_version, require_checked_out_branch, success_with_hook_warnings, write_version


def _commit_lvx_and_version(repo_root: Path, ver: GitflowVersion) -> None:
    vfile = write_version(repo_root, ver)
    version_str = str(vfile.relative_to(repo_root))
    run(["git", "add", version_str], repo_root)
    lvx_dir = repo_root / ".lvx"
    if lvx_dir.exists():
        run(["git", "add", ".lvx/"], repo_root)
    staged = run(["git", "diff", "--cached", "--quiet"], repo_root, check=False)
    if staged.returncode != 0:
        run(["git", "commit", "-m", f"chore: set version to {ver}"], repo_root)


def cmd_release_start(repo_root: Path, force: bool = False) -> CommandResult:
    integ = get_integration_branch(repo_root)
    invalid = require_checked_out_branch(repo_root, integ)
    if invalid is not None:
        return invalid

    lvx_dir = repo_root / ".lvx"
    if lvx_dir.exists():
        run(["git", "add", ".lvx/"], repo_root, check=False)

    if not force and is_dirty(repo_root):
        raise DirtyWorkingTreeError("Working tree is dirty. Commit or stash changes first, or use --force.")

    current_ver = read_version(repo_root)
    ver = normalize_to_stable(current_ver)
    version = str(ver)

    branch_name = f"release/{version}"
    if branch_exists(repo_root, branch_name):
        return CommandResult(success=False, message=f"Branch {branch_name!r} already exists.")

    create_and_checkout(repo_root, branch_name, integ)
    _commit_lvx_and_version(repo_root, ver)

    return CommandResult(
        success=True, message=f"Created {branch_name!r} from {integ!r}. Version set to {version}.",
        data={"branch": branch_name, "version": version},
    )


def cmd_release_candidate_cut(repo_root: Path, number: int | None = None, force: bool = False) -> CommandResult:
    require_clean_tree(repo_root, force)

    cur = current_branch(repo_root)
    if not cur.startswith("release/"):
        return CommandResult(success=False, message=f"Must run from a release/* branch. Current: {cur!r}")

    base_version = cur[len("release/"):]
    try:
        base_ver = GitflowVersion.parse(base_version)
    except ValueError as exc:
        return CommandResult(success=False, message=str(exc))

    if number is None:
        existing = list_tags(repo_root, f"v{base_version}-rc.*")
        number = len(existing) + 1

    rc_ver = GitflowVersion(base_ver.major, base_ver.minor, base_ver.patch, "rc", number)

    vfile = write_version(repo_root, rc_ver)
    commit_file(repo_root, vfile, f"chore: release candidate {rc_ver}")

    tag = rc_ver.rc_tag()
    create_annotated_tag(repo_root, tag, f"Release candidate {tag}")

    return CommandResult(success=True, message=f"Cut release candidate: {tag}", data={"tag": tag, "version": str(rc_ver)})


def cmd_release_candidate_list(repo_root: Path) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith("release/"):
        return CommandResult(success=False, message=f"Must run from a release/* branch. Current: {cur!r}")

    base_version = cur[len("release/"):]
    tags = sorted(list_tags(repo_root, f"v{base_version}-rc.*"))

    return CommandResult(success=True, message=f"Release candidates for {cur!r}:", data={"tags": tags})


def cmd_release_candidate_promote(repo_root: Path, rc_number: int) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith("release/"):
        return CommandResult(success=False, message=f"Must run from a release/* branch. Current: {cur!r}")

    base_version = cur[len("release/"):]
    try:
        base_ver = GitflowVersion.parse(base_version)
    except ValueError as exc:
        return CommandResult(success=False, message=str(exc))

    rc_ver = GitflowVersion(base_ver.major, base_ver.minor, base_ver.patch, "rc", rc_number)
    tag = rc_ver.rc_tag()

    if not list_tags(repo_root, tag):
        return CommandResult(success=False, message=f"RC tag {tag!r} does not exist for release line {cur!r}.")

    set_promoted_rc(repo_root, cur, rc_number)
    commit_if_dirty(repo_root, f"chore(version): promote RC {rc_number} for {cur}")

    return CommandResult(success=True, message=f"Promoted RC {rc_number} ({tag}) as the release target.", data={"rc_number": rc_number, "tag": tag})


def _release_finish_post_prod_merge(
    repo_root: Path, release_branch: str, stable_ver: GitflowVersion, prod: str, integ: str, hook_errors: list
) -> CommandResult:
    production_tag = stable_ver.production_tag()
    if not tag_exists(repo_root, production_tag):
        create_annotated_tag(repo_root, production_tag, f"Release {stable_ver}")

    bumped_ver = bump_minor(stable_ver)
    write_version(repo_root, bumped_ver)
    hook_errors.extend(run_version_hooks(repo_root, str(bumped_ver)))
    commit_if_dirty(repo_root, f"chore: bump version to {bumped_ver}")

    save_release_finish_state(
        repo_root, release_branch=release_branch, stable_version=str(stable_ver), production_branch=prod,
        integration_branch=integ, merge_message=f"Back-merge release {stable_ver} into {integ}", phase="dev_backmerge",
    )
    commit_if_dirty(repo_root, "chore(version): advance release finish state to dev_backmerge")

    checkout(repo_root, integ)
    try:
        # Back-merge production, not the release branch: production now also holds
        # the minor bump and finish-state commits made after the release merge.
        # Leaving those off the integration branch made every *next* release
        # conflict on VERSION, .lvx/state.json and whatever version_hooks write
        # (2026-09-24, found building the Release job).
        merge_no_ff(repo_root, prod, f"Back-merge release {stable_ver} into {integ}")
    except MergeConflictError as exc:
        return merge_conflict_result(exc, continue_cmd="version release finish --continue", abort_cmd="version release finish --abort")

    next_ver = bump_patch(bumped_ver)
    write_version(repo_root, next_ver)
    hook_errors.extend(run_version_hooks(repo_root, str(next_ver)))
    commit_if_dirty(repo_root, f"chore: establish next patch line {next_ver}")

    delete_branch_if_configured(repo_root, release_branch)
    clear_release_finish_state(repo_root)
    commit_if_dirty(repo_root, "chore(version): clear release finish state")

    return success_with_hook_warnings(
        message=(
            f"Released {stable_ver} (production tag {production_tag}). Minor bumped to {bumped_ver} on {prod}. "
            f"Next patch line established at {next_ver} on {integ}."
        ),
        hook_errors=hook_errors,
        data={
            "stable_version": str(stable_ver), "production_version": str(bumped_ver), "next_version": str(next_ver),
            "tag": production_tag, "production_branch": prod, "integration_branch": integ,
        },
    )


def _release_finish_post_dev_backmerge(
    repo_root: Path, release_branch: str, stable_ver: GitflowVersion, prod: str, integ: str
) -> CommandResult:
    production_tag = stable_ver.production_tag()
    bumped_ver = bump_minor(stable_ver)
    next_ver = bump_patch(bumped_ver)
    write_version(repo_root, next_ver)
    hook_errors = run_version_hooks(repo_root, str(next_ver))
    commit_if_dirty(repo_root, f"chore: establish next patch line {next_ver}")

    delete_branch_if_configured(repo_root, release_branch)
    clear_release_finish_state(repo_root)
    commit_if_dirty(repo_root, "chore(version): clear release finish state")

    return success_with_hook_warnings(
        message=(
            f"Released {stable_ver} (production tag {production_tag}). Minor bumped to {bumped_ver} on {prod}. "
            f"Next patch line established at {next_ver} on {integ}."
        ),
        hook_errors=hook_errors,
        data={
            "stable_version": str(stable_ver), "production_version": str(bumped_ver), "next_version": str(next_ver),
            "tag": production_tag, "production_branch": prod, "integration_branch": integ,
        },
    )


def cmd_release_finish(repo_root: Path, force: bool = False, action: str | None = None) -> CommandResult:
    if action == "abort":
        finish_state = load_release_finish_state(repo_root)
        if not finish_state and not is_merge_in_progress(repo_root):
            return CommandResult(success=False, message="No release finish in progress to abort.")
        if is_merge_in_progress(repo_root):
            merge_abort(repo_root)
        release_branch = (finish_state or {}).get("release_branch", "release branch")
        clear_untracked_state_file(repo_root)
        checkout_existing_branch(repo_root, release_branch)
        clear_release_finish_state(repo_root)
        commit_if_dirty(repo_root, "chore(version): abort release finish")
        return CommandResult(success=True, message=f"Aborted release finish for {release_branch!r}. Repository restored.")

    if action == "continue":
        finish_state = load_release_finish_state(repo_root)
        if not finish_state and not is_merge_in_progress(repo_root):
            return CommandResult(success=False, message="No release finish in progress. Run 'version release finish' first.")
        if not finish_state:
            return CommandResult(
                success=False,
                message="A merge is in progress but no release finish state was found. Resolve manually or run 'git merge --abort'.",
            )

        release_branch = finish_state["release_branch"]
        stable_ver = GitflowVersion.parse(finish_state["stable_version"])
        prod = finish_state["production_branch"]
        integ = finish_state["integration_branch"]
        prod_merge_message = f"rel ({stable_ver}): merge {release_branch}"
        backmerge_message = f"Back-merge release {stable_ver} into {integ}"

        cur = current_branch(repo_root)

        if cur == prod:
            if is_merge_in_progress(repo_root):
                merge_commit(repo_root, prod_merge_message)
            return _release_finish_post_prod_merge(repo_root, release_branch, stable_ver, prod, integ, hook_errors=[])

        if cur == integ:
            if is_merge_in_progress(repo_root):
                merge_commit(repo_root, backmerge_message)
            return _release_finish_post_dev_backmerge(repo_root, release_branch, stable_ver, prod, integ)

        return CommandResult(success=False, message=f"Cannot resume release finish from {cur!r}. Switch to {prod!r} or {integ!r} and retry.")

    # --- Normal start path ---
    require_clean_tree(repo_root, force)

    cur = current_branch(repo_root)
    if not cur.startswith("release/"):
        return CommandResult(success=False, message=f"Must run from a release/* branch. Current: {cur!r}")

    base_version = cur[len("release/"):]
    try:
        base_ver = GitflowVersion.parse(base_version)
        stable_ver = normalize_to_stable(base_ver)
    except ValueError as exc:
        return CommandResult(success=False, message=str(exc))

    integ = get_integration_branch(repo_root)
    prod = get_production_branch(repo_root)
    require_branch_exists(repo_root, integ)
    require_branch_exists(repo_root, prod)

    promoted_rc = get_promoted_rc(repo_root, cur)
    if promoted_rc is not None:
        rc_ver = GitflowVersion(stable_ver.major, stable_ver.minor, stable_ver.patch, "rc", promoted_rc)
        rc_tag = rc_ver.rc_tag()
        # Precondition-checked, refuse rather than crash: a promoted RC number
        # with no matching tag (state tampered with, or a cut that never
        # happened) must fail cleanly, not raise from deep inside gitops.
        if not tag_exists(repo_root, rc_tag):
            return CommandResult(success=False, message=f"Promoted RC {rc_tag} does not exist. Cannot finish.")
        rc_commit = get_tag_commit(repo_root, rc_tag)
        if not is_ancestor(repo_root, rc_commit, "HEAD"):
            return CommandResult(success=False, message=f"Promoted RC {rc_tag} is not reachable from HEAD. Cannot finish.")

    current_file_ver = read_version(repo_root)
    if not current_file_ver.is_stable():
        vfile = write_version(repo_root, stable_ver)
        commit_file(repo_root, vfile, f"chore: finalize release version {stable_ver}")

    release_branch = cur
    merge_message = f"rel ({stable_ver}): merge {release_branch}"

    save_release_finish_state(
        repo_root, release_branch=release_branch, stable_version=str(stable_ver), production_branch=prod,
        integration_branch=integ, merge_message=merge_message, phase="prod_merge",
    )
    commit_if_dirty(repo_root, "chore(version): begin release finish")

    checkout(repo_root, prod)
    try:
        merge_no_ff(repo_root, release_branch, merge_message)
    except MergeConflictError as exc:
        return merge_conflict_result(exc, continue_cmd="version release finish --continue", abort_cmd="version release finish --abort")

    return _release_finish_post_prod_merge(repo_root, release_branch, stable_ver, prod, integ, hook_errors=[])


def cmd_release_status(repo_root: Path) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith("release/"):
        return CommandResult(success=False, message=f"Not on a release/* branch. Current: {cur!r}")
    base_version = cur[len("release/"):]
    promoted = get_promoted_rc(repo_root, cur)
    tags = sorted(list_tags(repo_root, f"v{base_version}-rc.*"))
    return CommandResult(
        success=True, message=f"Release branch: {cur!r}",
        data={"branch": cur, "version": base_version, "rc_tags": tags, "promoted_rc": promoted},
    )
