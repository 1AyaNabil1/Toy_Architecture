"""Test helpers: ZIP/TAR utilities and an in-memory stand-in for the Docker client."""

from __future__ import annotations

import io
import tarfile
import warnings
import zipfile
from pathlib import Path

import docker.errors

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_MODEL_DIR = REPO_ROOT / "examples" / "sample_model"


def write_zip(path: Path, entries, compression=zipfile.ZIP_DEFLATED) -> Path:
    """Write ``entries`` (pairs of name-or-ZipInfo and bytes/str) to a ZIP at ``path``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns about duplicate names
        with zipfile.ZipFile(path, "w", compression) as zf:
            for name, data in entries:
                if isinstance(name, zipfile.ZipInfo):
                    name.compress_type = compression
                zf.writestr(name, data)
    return path


def read_tar(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {member.name: member for member in tar.getmembers()}


def read_tar_file(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return tar.extractfile(name).read()


class FakeImage:
    def __init__(self, image_id="sha256:base", config=None):
        self.id = image_id
        self.attrs = {"Config": config if config is not None else {"Cmd": ["python3"]}}


class FakeContainer:
    """Records what the builder does to its build container."""

    short_id = "c0ffee"

    def __init__(self, exec_results=None, put_ok=True, fail_on=None):
        self.exec_results = exec_results or {}
        self.put_ok = put_ok
        self.fail_on = fail_on
        self.execs: list[tuple[list[str], dict]] = []
        self.archives: dict[str, bytes] = {}
        self.committed: dict | None = None
        self.removed_with: dict | None = None

    def exec_run(self, cmd, **kwargs):
        self.execs.append((cmd, kwargs))
        if self.fail_on == "exec":
            raise docker.errors.APIError("engine went away")
        for prefix, result in self.exec_results.items():
            if " ".join(cmd).startswith(prefix):
                return result
        return 0, b""

    def put_archive(self, path, data):
        self.archives[path] = data.read() if hasattr(data, "read") else bytes(data)
        return self.put_ok

    def commit(self, **kwargs):
        self.committed = kwargs
        return FakeImage("sha256:built")

    def remove(self, **kwargs):
        self.removed_with = kwargs


class FakeImages:
    def __init__(self, local):
        self.local = dict(local)
        self.pulled: list[str] = []

    def get(self, name):
        if name in self.local:
            return self.local[name]
        raise docker.errors.ImageNotFound(f"No such image: {name}")

    def pull(self, name):
        self.pulled.append(name)
        image = self.local.setdefault(name, FakeImage())
        return image


class FakeContainers:
    def __init__(self, container):
        self.container = container
        self.runs: list[tuple[str, dict]] = []

    def run(self, image, **kwargs):
        self.runs.append((image, kwargs))
        return self.container


class FakeClient:
    def __init__(self, local_images=None, container=None):
        self.images = FakeImages(local_images or {})
        self.container = container or FakeContainer()
        self.containers = FakeContainers(self.container)
