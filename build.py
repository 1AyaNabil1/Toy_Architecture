"""
CLI entry point for building Docker images from model ZIP files.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import sys

from app.builder import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_WORKDIR,
    PULL_POLICIES,
    REQUIREMENTS_FILE,
    build_image_from_zip,
    plan_build,
)
from app.errors import BuildError
from app.zip_handler import MiB, ZipLimits, open_bundle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Docker image from a model ZIP, without a Dockerfile."
    )
    parser.add_argument("--zip", required=True, help="Path to the model ZIP file")
    parser.add_argument("--tag", required=True, help="Image tag, e.g. my-model:v1")
    parser.add_argument(
        "--base-image",
        default=DEFAULT_BASE_IMAGE,
        help=f"Base image to install the model into (default: {DEFAULT_BASE_IMAGE})",
    )
    parser.add_argument(
        "--workdir",
        default=DEFAULT_WORKDIR,
        help=f"Directory in the image that receives the ZIP contents (default: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "--cmd",
        help='Default command of the image, e.g. "python serve.py". '
        "Default: python inference.py if the ZIP has one, else the base image's command",
    )
    parser.add_argument(
        "--pull",
        choices=PULL_POLICIES,
        default="missing",
        help="When to pull the base image (default: missing)",
    )
    parser.add_argument(
        "--max-size-mb",
        type=int,
        default=ZipLimits().max_total_bytes // MiB,
        help="Reject ZIPs that expand to more than this many MiB (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the ZIP and print the build plan without contacting Docker",
    )
    args = parser.parse_args(argv)
    if args.max_size_mb <= 0:
        parser.error("--max-size-mb must be positive")
    if args.cmd is not None:
        try:
            args.cmd = shlex.split(args.cmd)
        except ValueError as exc:
            parser.error(f"--cmd: {exc}")
        if not args.cmd:
            parser.error("--cmd must not be empty")
    return args


def dry_run(args: argparse.Namespace, limits: ZipLimits) -> None:
    with open_bundle(args.zip, limits) as bundle:
        plan = plan_build(
            bundle, args.tag, base_image=args.base_image, workdir=args.workdir, cmd=args.cmd
        )
        print(f"ZIP:        {bundle.source} (sha256 {bundle.sha256})")
        print(f"Files:      {len(bundle.entries)} ({bundle.total_bytes} bytes uncompressed)")
        for entry in bundle.entries:
            print(f"  {plan.workdir}/{entry.path}  ({entry.size} bytes)")
        if bundle.skipped:
            print(f"Skipped:    {', '.join(bundle.skipped)}")
        print(f"Base image: {plan.base_image}")
        print(f"Image:      {plan.image}")
        install = f"pip install -r {REQUIREMENTS_FILE}" if plan.install_requirements else "none"
        print(f"Install:    {install}")
        print("Commit changes:")
        for change in plan.commit_changes():
            if plan.cmd is None and change.startswith("CMD "):
                change = "CMD <the base image's command>"
            print(f"  {change}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    limits = ZipLimits(max_total_bytes=args.max_size_mb * MiB)

    try:
        if args.dry_run:
            dry_run(args, limits)
            return 0
        image_id = build_image_from_zip(
            zip_path=args.zip,
            image_tag=args.tag,
            base_image=args.base_image,
            workdir=args.workdir,
            cmd=args.cmd,
            pull=args.pull,
            limits=limits,
        )
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130

    print("BUILD COMPLETE")
    print("Image ID:", image_id)
    print("Tag:", args.tag)
    print(f"Run it with: docker run --rm {args.tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
