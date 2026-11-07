"""`lws` commands of Build & Packages."""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def _build_job(args: argparse.Namespace, job_type: str, params: dict, *, timeout: float = 30.0) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type=job_type, target_pillar="build", project_id=args.project_id,
            params=params, queue_key=None, depends_on=[], created_by="lws", timeout=timeout,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_build_run(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return _build_job(args, "build.run", {
        "commitish": args.commitish, "command": command, "from_fork": args.from_fork,
    }, timeout=1800.0)


def cmd_build_run_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.build_list_runs(paths, args.project_id))
    return 0


def cmd_build_run_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.build_get_run(paths, args.run_id)
    _print(result)
    return 0 if result.get("build_run") is not None else 1


def cmd_build_publish_pypi(args: argparse.Namespace) -> int:
    content_base64 = base64.b64encode(Path(args.file).read_bytes()).decode()
    params = {"build_run_id": args.build_run_id, "filename": Path(args.file).name, "content_base64": content_base64}
    for key in ("name", "version"):
        if getattr(args, key):
            params[key] = getattr(args, key)
    return _build_job(args, "build.publish_pypi", params)


def cmd_build_publish_vsix(args: argparse.Namespace) -> int:
    content_base64 = base64.b64encode(Path(args.file).read_bytes()).decode()
    return _build_job(args, "build.publish_vsix", {
        "build_run_id": args.build_run_id, "name": args.name, "version": args.version,
        "filename": Path(args.file).name, "content_base64": content_base64,
    })


def cmd_build_package_delete(args: argparse.Namespace) -> int:
    return _build_job(args, "build.delete_package", {"package_id": args.package_id})


def cmd_build_package_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.build_list_packages(paths, args.project_id, args.kind))
    return 0


# M16 little-pypi parity: the PEP 503 index is served over Build &
# Packages' own Pillar Interface, a Unix domain socket
# (docs/architecture.md's PI transport: "never a network port") — these
# three commands assume a real HTTP reverse proxy already sits in front
# of it at --host:--port, the same deployment-time-concern split
# docs/pillars/dashboard.md's own browser-exposure section already
# documents. `little-pypi`'s own equivalents make the identical
# assumption (they just run their own TCP server directly); nothing
# here builds that proxy.


def cmd_build_print_pip_config(args: argparse.Namespace) -> int:
    print(_pip_config_text(args.host, args.port))
    return 0


def cmd_build_write_pip_config(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_pip_config_text(args.host, args.port) + "\n", encoding="utf-8")
    _print({"written": str(target), "index_url": f"http://{args.host}:{args.port}/simple"})
    return 0


def _pip_config_text(host: str, port: int) -> str:
    return "\n".join([
        "[global]",
        f"index-url = http://{host}:{port}/simple",
        "extra-index-url = https://pypi.org/simple",
        f"trusted-host = {host}",
        "disable-pip-version-check = true",
    ])


def cmd_build_pip(args: argparse.Namespace) -> int:
    pip_args = list(args.pip_install_args)
    if pip_args and pip_args[0] == "--":
        pip_args = pip_args[1:]
    index_url = f"http://{args.host}:{args.port}/simple"
    command = [
        sys.executable, "-m", "pip", "install", args.package,
        "--extra-index-url", index_url, "--trusted-host", args.host, *pip_args,
    ]
    completed = subprocess.run(command, check=False)
    return completed.returncode


def cmd_build_print_vsix_urls(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    listing = client.build_list_packages(paths, args.project_id, "vsix")
    base_url = args.base_url.rstrip("/") if args.base_url else f"http://{args.host}:{args.port}"
    urls = [f"{base_url}/vsix/{pkg['name']}/{pkg['version']}" for pkg in listing["packages"]]
    _print({"count": len(urls), "urls": urls})
    return 0


def register(subparsers) -> None:
    build = subparsers.add_parser("build", help="Build & Packages commands")
    build_sub = build.add_subparsers(dest="build_command", required=True)

    build_run = build_sub.add_parser("run", help="Run a build/test command sandboxed in a fresh worktree")
    build_run.add_argument("--project-id", required=True)
    build_run.add_argument("--commitish", default="HEAD")
    build_run.add_argument("--from-fork", action="store_true", help="Mark this run as untrusted (fork-safety gate: blocks publish)")
    build_run.add_argument("command", nargs=argparse.REMAINDER, help="The build/test command, e.g. -- python3 -m pytest")
    build_run.set_defaults(func=cmd_build_run)

    build_run_list = build_sub.add_parser("run-list", help="List build runs")
    build_run_list.add_argument("--project-id", default=None)
    build_run_list.set_defaults(func=cmd_build_run_list)

    build_run_status = build_sub.add_parser("run-status", help="Show one build run")
    build_run_status.add_argument("run_id")
    build_run_status.set_defaults(func=cmd_build_run_status)

    build_publish_pypi = build_sub.add_parser("publish-pypi", help="Publish a wheel/sdist to the internal PEP 503 index")
    build_publish_pypi.add_argument("--project-id", required=True)
    build_publish_pypi.add_argument("--build-run-id", required=True)
    build_publish_pypi.add_argument("file", help="The wheel/sdist; its name and version are read from its filename")
    build_publish_pypi.add_argument("--name", default=None, help="Override the name read from the filename")
    build_publish_pypi.add_argument("--version", default=None, help="Must match the version in the filename")
    build_publish_pypi.set_defaults(func=cmd_build_publish_pypi)

    build_publish_vsix = build_sub.add_parser("publish-vsix", help="Publish a .vsix extension")
    build_publish_vsix.add_argument("--project-id", required=True)
    build_publish_vsix.add_argument("--build-run-id", required=True)
    build_publish_vsix.add_argument("name")
    build_publish_vsix.add_argument("version")
    build_publish_vsix.add_argument("file")
    build_publish_vsix.set_defaults(func=cmd_build_publish_vsix)

    build_package_list = build_sub.add_parser("package-list", help="List published packages")
    build_package_list.add_argument("--project-id", default=None)
    build_package_list.add_argument("--kind", choices=["pypi", "vsix"], default=None)
    build_package_list.set_defaults(func=cmd_build_package_list)

    build_package_delete = build_sub.add_parser("package-delete", help="Delete a published package")
    build_package_delete.add_argument("--project-id", required=True)
    build_package_delete.add_argument("package_id")
    build_package_delete.set_defaults(func=cmd_build_package_delete)

    build_print_pip_config = build_sub.add_parser(
        "print-pip-config", help="Print a pip.conf snippet pointing at a reverse proxy in front of this pillar's PEP 503 index"
    )
    build_print_pip_config.add_argument("--host", default="127.0.0.1")
    build_print_pip_config.add_argument("--port", type=int, default=8080)
    build_print_pip_config.set_defaults(func=cmd_build_print_pip_config)

    build_write_pip_config = build_sub.add_parser("write-pip-config", help="Write the same pip.conf snippet to a file")
    build_write_pip_config.add_argument("--host", default="127.0.0.1")
    build_write_pip_config.add_argument("--port", type=int, default=8080)
    build_write_pip_config.add_argument("--path", default="~/.config/pip/pip.conf")
    build_write_pip_config.set_defaults(func=cmd_build_write_pip_config)

    build_pip = build_sub.add_parser("pip", help="Install a package using this pillar's index as an extra index")
    build_pip.add_argument("--host", default="127.0.0.1")
    build_pip.add_argument("--port", type=int, default=8080)
    build_pip.add_argument("package")
    build_pip.add_argument("pip_install_args", nargs=argparse.REMAINDER, help="Extra pip install args, e.g. -- --upgrade")
    build_pip.set_defaults(func=cmd_build_pip)

    build_print_vsix_urls = build_sub.add_parser("print-vsix-urls", help="Print direct install URLs for published .vsix files")
    build_print_vsix_urls.add_argument("--project-id", default=None)
    build_print_vsix_urls.add_argument("--host", default="127.0.0.1")
    build_print_vsix_urls.add_argument("--port", type=int, default=8080)
    build_print_vsix_urls.add_argument("--base-url", default=None, help="Overrides --host/--port with an explicit base URL")
    build_print_vsix_urls.set_defaults(func=cmd_build_print_vsix_urls)
