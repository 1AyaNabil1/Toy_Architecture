"""
Core Docker image builder.

Builds an image from a model ZIP through the Docker Engine API, without a
Dockerfile:

1. validate the ZIP (see :mod:`app.zip_handler`) before touching Docker;
2. start a container from the base image (it only runs ``sleep infinity``);
3. stream the validated files into the working directory as a TAR archive;
4. ``pip install -r requirements.txt`` inside the container, if present;
5. commit the container as the new image, setting WORKDIR, CMD and
   provenance labels;
6. remove the build container, whether the build succeeded or not.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import docker
import docker.errors
import requests

from app import __version__
from app.docker_client import get_client
from app.errors import BuildError
from app.zip_handler import DEFAULT_LIMITS, Bundle, ZipLimits, open_bundle

logger = logging.getLogger(__name__)

DEFAULT_BASE_IMAGE = "python:3.12-slim"
DEFAULT_WORKDIR = "/app"
PULL_POLICIES = ("missing", "always", "never")
INFERENCE_SCRIPT = "inference.py"
REQUIREMENTS_FILE = "requirements.txt"
LABEL_PREFIX = "mini-model-builder"

# Image reference grammar, simplified from github.com/distribution/reference:
# an optional registry host (with port), then lowercase path components.
_DOMAIN_COMPONENT = r"(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])"
_DOMAIN = rf"{_DOMAIN_COMPONENT}(?:\.{_DOMAIN_COMPONENT})*(?::[0-9]+)?"
_PATH_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_REPOSITORY_RE = re.compile(rf"^(?:{_DOMAIN}/)?{_PATH_COMPONENT}(?:/{_PATH_COMPONENT})*$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_WORKDIR_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")

# Errors raised by docker-py: API errors, or the connection to the engine dropping.
_DOCKER_ERRORS = (docker.errors.DockerException, requests.exceptions.RequestException)

# pip settings passed as environment variables, which older pips ignore
# instead of failing on an unknown command-line flag.
_PIP_ENV = {
    "PIP_NO_CACHE_DIR": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_ROOT_USER_ACTION": "ignore",
}


def parse_image_tag(image_tag: str) -> tuple[str, str]:
    """Split ``repo[:tag]`` into ``(repo, tag)``; the tag defaults to ``latest``.

    Handles registry hosts with ports (``localhost:5000/model:v1``) and
    rejects references Docker would refuse, before any build work is done.
    """
    reference = image_tag.strip()
    if "@" in reference:
        raise BuildError(f"Invalid image tag {image_tag!r}: digests cannot be used, use repo:tag")
    repository, sep, tag = reference.rpartition(":")
    if not sep or "/" in tag:
        # No colon, or the only colon belongs to a registry port.
        repository, tag = reference, "latest"
    if len(repository) > 255 or not _REPOSITORY_RE.match(repository):
        raise BuildError(
            f"Invalid image name {repository!r}: use lowercase letters, digits and "
            "separators (. _ -), optionally prefixed by a registry host"
        )
    if not _TAG_RE.match(tag):
        raise BuildError(f"Invalid image tag {tag!r}")
    return repository, tag


def validate_workdir(workdir: str) -> str:
    """Return the normalised absolute working directory or raise BuildError."""
    if not _WORKDIR_RE.match(workdir):
        raise BuildError(
            f"Invalid working directory {workdir!r}: use an absolute path made of "
            "letters, digits, '.', '_', '-' and '/'"
        )
    normalised = posixpath.normpath(workdir)
    if normalised == "/" or ".." in workdir.split("/"):
        raise BuildError(f"Invalid working directory {workdir!r}: must not be '/' or contain '..'")
    return normalised


def _dockerfile_quote(value: str) -> str:
    """Quote a value for a Dockerfile instruction that expands variables."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return '"' + " ".join(escaped.splitlines()) + '"'


@dataclass(frozen=True)
class BuildPlan:
    """Everything the builder will do, derived from the ZIP and the options.

    ``cmd`` is None when the image should keep the base image's command.
    """

    repository: str
    tag: str
    base_image: str
    workdir: str
    cmd: list[str] | None
    install_requirements: bool
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def image(self) -> str:
        return f"{self.repository}:{self.tag}"

    def commit_changes(self, base_cmd: Sequence[str] | None = None) -> list[str]:
        """Dockerfile instructions applied when the container is committed.

        The build container runs ``sleep infinity``, so CMD must always be
        set explicitly; otherwise the image would inherit that command.
        """
        cmd = self.cmd if self.cmd is not None else list(base_cmd or ["python"])
        changes = [f"WORKDIR {self.workdir}", f"CMD {json.dumps(cmd)}"]
        changes += [
            f"LABEL {key}={_dockerfile_quote(value)}" for key, value in sorted(self.labels.items())
        ]
        return changes


def plan_build(
    bundle: Bundle,
    image_tag: str,
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    workdir: str = DEFAULT_WORKDIR,
    cmd: Sequence[str] | None = None,
) -> BuildPlan:
    """Work out what to build from a validated bundle. Does not use Docker."""
    repository, tag = parse_image_tag(image_tag)
    if cmd is not None:
        cmd = list(cmd)
        if not cmd:
            raise BuildError("The image command must not be empty")
    elif bundle.has_file(INFERENCE_SCRIPT):
        cmd = ["python", INFERENCE_SCRIPT]
    labels = {
        f"{LABEL_PREFIX}.version": __version__,
        f"{LABEL_PREFIX}.source.name": bundle.source.name,
        f"{LABEL_PREFIX}.source.sha256": bundle.sha256,
        "org.opencontainers.image.base.name": base_image,
    }
    return BuildPlan(
        repository=repository,
        tag=tag,
        base_image=base_image,
        workdir=validate_workdir(workdir),
        cmd=cmd,
        install_requirements=bundle.has_file(REQUIREMENTS_FILE),
        labels=labels,
    )


def ensure_base_image(client: docker.DockerClient, name: str, pull: str = "missing"):
    """Return the base image, pulling it according to the pull policy."""
    if pull not in PULL_POLICIES:
        raise BuildError(f"Unknown pull policy {pull!r}; choose one of {', '.join(PULL_POLICIES)}")
    if pull != "always":
        try:
            return client.images.get(name)
        except docker.errors.ImageNotFound:
            if pull == "never":
                raise BuildError(
                    f"Base image {name!r} is not available locally and --pull=never was given"
                ) from None
    logger.info("Pulling base image %s", name)
    try:
        return client.images.pull(name)
    except docker.errors.APIError as exc:
        raise BuildError(f"Could not pull base image {name!r}: {exc}") from exc


def _output_tail(output: bytes | None, lines: int = 20) -> str:
    text = (output or b"").decode("utf-8", errors="replace").rstrip()
    return "\n".join(text.splitlines()[-lines:])


def _exec(container, cmd: list[str], action: str, **kwargs) -> None:
    exit_code, output = container.exec_run(cmd, **kwargs)
    if exit_code != 0:
        raise BuildError(f"Failed to {action} (exit code {exit_code}):\n{_output_tail(output)}")


def _remove_container(container) -> None:
    try:
        # force=True kills `sleep infinity` immediately; a plain stop() would
        # wait out Docker's 10 s grace period because sleep ignores SIGTERM.
        container.remove(force=True)
    except _DOCKER_ERRORS as exc:
        logger.warning("Could not remove build container %s: %s", container.short_id, exc)


def build_image_from_zip(
    zip_path: str | Path,
    image_tag: str,
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    workdir: str = DEFAULT_WORKDIR,
    cmd: Sequence[str] | None = None,
    pull: str = "missing",
    limits: ZipLimits = DEFAULT_LIMITS,
    client: docker.DockerClient | None = None,
) -> str:
    """Build a Docker image from a model ZIP and return the new image ID.

    Args:
        zip_path: Path to the ZIP containing the model files.
        image_tag: Tag for the resulting image, e.g. ``my-model:v1``.
        base_image: Image the model is installed into.
        workdir: Directory in the image that receives the ZIP contents.
        cmd: Default command of the image. When omitted it is
            ``python inference.py`` if the ZIP has one, else the base image's.
        pull: ``missing`` (pull only if absent), ``always`` or ``never``.
        limits: Size limits applied to the ZIP.
        client: Docker client to use; defaults to one built from the environment.

    Raises:
        BuildError: with a user-facing message, if any step fails.
    """
    with open_bundle(zip_path, limits) as bundle:
        plan = plan_build(bundle, image_tag, base_image=base_image, workdir=workdir, cmd=cmd)
        logger.info(
            "Validated %s: %d files, %d bytes uncompressed, sha256 %s",
            bundle.source.name,
            len(bundle.entries),
            bundle.total_bytes,
            bundle.sha256,
        )

        client = client or get_client()
        try:
            return _build(client, bundle, plan, pull)
        except _DOCKER_ERRORS as exc:
            raise BuildError(f"Docker error while building {plan.image}: {exc}") from exc


def _build(client: docker.DockerClient, bundle: Bundle, plan: BuildPlan, pull: str) -> str:
    base = ensure_base_image(client, plan.base_image, pull)
    base_config = base.attrs.get("Config") or {}
    if base_config.get("Entrypoint"):
        raise BuildError(
            f"Base image {plan.base_image!r} defines an ENTRYPOINT, which is not supported; "
            f"use an image without one, such as {DEFAULT_BASE_IMAGE}"
        )

    logger.info("Starting build container from %s", plan.base_image)
    container = client.containers.run(plan.base_image, command=["sleep", "infinity"], detach=True)
    try:
        _exec(container, ["mkdir", "-p", plan.workdir], f"create {plan.workdir}")

        logger.info("Copying %d files into %s", len(bundle.entries), plan.workdir)
        with tempfile.TemporaryFile() as tar_file:
            bundle.write_tar(tar_file)
            tar_file.seek(0)
            if not container.put_archive(plan.workdir, tar_file):
                raise BuildError(f"Docker refused to copy the files into {plan.workdir}")

        if plan.install_requirements:
            logger.info("Installing %s", REQUIREMENTS_FILE)
            _exec(
                container,
                ["python", "-m", "pip", "install", "-r", REQUIREMENTS_FILE],
                f"install {REQUIREMENTS_FILE}",
                workdir=plan.workdir,
                environment=_PIP_ENV,
            )

        logger.info("Committing image %s", plan.image)
        image = container.commit(
            repository=plan.repository,
            tag=plan.tag,
            message=f"Built by mini-model-builder from {bundle.source.name}",
            changes=plan.commit_changes(base_config.get("Cmd")),
        )
        return image.id
    finally:
        _remove_container(container)
