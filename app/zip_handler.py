"""
Validation of user-supplied model ZIPs and conversion to a TAR stream.

The ZIP is treated as untrusted input. Nothing in it is extracted to the host
file system or executed on the host: every entry is validated up front, then
streamed straight from the ZIP into a TAR archive that Docker unpacks inside
the build container.

Checks applied before a build starts:

* path traversal ("zip-slip"): absolute paths, Windows drive letters, ``..``
  components and symlink entries are rejected;
* zip bombs: limits on the number of entries, the total uncompressed size and
  the per-entry compression ratio. While streaming, the bytes actually read
  must match the declared size and CRC of each entry;
* ambiguity: duplicate paths and file/directory clashes are rejected;
* encrypted entries are rejected.

Hidden files and folders (``.env``, ``.git/``, ``.DS_Store``...), ``__MACOSX/``
and ``__pycache__/`` are skipped so they never end up in the image. If every
file sits under one top-level folder (the usual result of zipping a folder),
that folder is stripped so its contents land directly in the working directory.
"""

from __future__ import annotations

import hashlib
import re
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO

from app.errors import BundleError

MiB = 1024 * 1024


@dataclass(frozen=True)
class ZipLimits:
    """Upper bounds a ZIP must respect before anything is built from it."""

    max_entries: int = 10_000
    max_total_bytes: int = 2048 * MiB
    max_compression_ratio: float = 200.0
    # Entries smaller than this skip the ratio check: a tiny, highly
    # compressible file (a config full of spaces, say) cannot be a meaningful bomb.
    ratio_check_min_bytes: int = 1 * MiB


DEFAULT_LIMITS = ZipLimits()

_SKIPPED_NAMES = frozenset({"__MACOSX", "__pycache__"})
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ENCRYPTED_FLAG = 0x1
_COPY_CHUNK = 1 * MiB


@dataclass(frozen=True)
class BundleEntry:
    """A validated file from the ZIP and the path it will have in the image."""

    info: zipfile.ZipInfo
    path: PurePosixPath

    @property
    def size(self) -> int:
        return self.info.file_size

    @property
    def mode(self) -> int:
        """0o755 if the ZIP marks the file executable, else 0o644.

        Only the executable bit is carried over: setuid/setgid/world-writable
        bits from the ZIP are never applied.
        """
        unix_mode = (self.info.external_attr >> 16) & 0o777
        return 0o755 if unix_mode & 0o111 else 0o644


def _relative_path(name: str) -> PurePosixPath:
    """Return the entry name as a safe relative path, or raise BundleError."""
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or _DRIVE_RE.match(normalised):
        raise BundleError(f"Unsafe ZIP entry {name!r}: absolute paths are not allowed")
    parts = [part for part in normalised.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise BundleError(f"Unsafe ZIP entry {name!r}: '..' path components are not allowed")
    return PurePosixPath(*parts)


def _is_skipped(path: PurePosixPath) -> bool:
    return any(part.startswith(".") or part in _SKIPPED_NAMES for part in path.parts)


def _strip_common_root(
    files: list[tuple[zipfile.ZipInfo, PurePosixPath]],
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    roots = {path.parts[0] for _, path in files}
    if len(roots) == 1 and all(len(path.parts) > 1 for _, path in files):
        return [(info, PurePosixPath(*path.parts[1:])) for info, path in files]
    return files


def _check_collisions(files: list[tuple[zipfile.ZipInfo, PurePosixPath]]) -> None:
    seen: set[PurePosixPath] = set()
    for info, path in files:
        if path in seen:
            raise BundleError(
                f"Ambiguous ZIP: {path} appears more than once (entry {info.filename!r})"
            )
        seen.add(path)
    directories = {parent for path in seen for parent in path.parents if parent.parts}
    clashes = sorted(str(path) for path in seen & directories)
    if clashes:
        raise BundleError(f"Ambiguous ZIP: {clashes[0]} is both a file and a directory")


def validate_entries(
    zf: zipfile.ZipFile, limits: ZipLimits = DEFAULT_LIMITS
) -> tuple[list[BundleEntry], list[str]]:
    """Validate every entry of ``zf``.

    Returns ``(entries, skipped)``: the files to put in the image, in ZIP order,
    and the names of the entries that were deliberately left out.
    Raises BundleError on the first unsafe or over-limit entry.
    """
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise BundleError(f"ZIP has {len(infos)} entries; the limit is {limits.max_entries}")

    files: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    skipped: list[str] = []
    total_bytes = 0
    for info in infos:
        name = info.filename
        path = _relative_path(name)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise BundleError(f"Unsafe ZIP entry {name!r}: symbolic links are not allowed")
        if name.replace("\\", "/").endswith("/"):
            continue  # directory entry; directories are recreated from file paths
        if not path.parts:
            raise BundleError(f"Invalid ZIP entry {name!r}: empty path")
        if _is_skipped(path):
            skipped.append(name)
            continue
        if info.flag_bits & _ENCRYPTED_FLAG:
            raise BundleError(f"ZIP entry {name!r} is encrypted; encrypted ZIPs are not supported")

        total_bytes += info.file_size
        if total_bytes > limits.max_total_bytes:
            raise BundleError(
                f"ZIP expands to more than {limits.max_total_bytes} bytes "
                f"(limit reached at entry {name!r})"
            )
        if (
            info.file_size >= limits.ratio_check_min_bytes
            and info.file_size > limits.max_compression_ratio * max(info.compress_size, 1)
        ):
            ratio = info.file_size / max(info.compress_size, 1)
            raise BundleError(
                f"ZIP entry {name!r} has a suspicious compression ratio "
                f"({ratio:.0f}:1, limit {limits.max_compression_ratio:.0f}:1)"
            )
        files.append((info, path))

    if not files:
        raise BundleError("ZIP contains no usable files")
    files = _strip_common_root(files)
    _check_collisions(files)
    return [BundleEntry(info, path) for info, path in files], skipped


class _EntryReader:
    """File-like wrapper that turns ZIP read failures into BundleError.

    ``tarfile.addfile`` reads exactly ``entry.size`` bytes from it. zipfile
    verifies the CRC when the last declared byte is read, so a corrupt or
    tampered entry fails here instead of producing a silently broken image.
    """

    def __init__(self, src: IO[bytes], entry: BundleEntry) -> None:
        self._src = src
        self._entry = entry
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        try:
            data = self._src.read(size)
        except Exception as exc:  # BadZipFile, EOFError, zlib.error, ...
            raise BundleError(f"Corrupt ZIP entry {self._entry.info.filename!r}: {exc}") from exc
        if not data and size != 0 and self.bytes_read < self._entry.size:
            raise BundleError(
                f"Corrupt ZIP entry {self._entry.info.filename!r}: "
                f"ended after {self.bytes_read} of {self._entry.size} declared bytes"
            )
        self.bytes_read += len(data)
        return data


def _zip_mtime(info: zipfile.ZipInfo) -> int:
    try:
        return int(datetime(*info.date_time).timestamp())
    except (ValueError, OverflowError, OSError):
        return 0


class Bundle:
    """A validated model ZIP, ready to be streamed into a container.

    Use :func:`open_bundle` to create one, ideally as a context manager.
    """

    def __init__(
        self,
        source: Path,
        sha256: str,
        handle: BinaryIO,
        zf: zipfile.ZipFile,
        entries: list[BundleEntry],
        skipped: list[str],
    ) -> None:
        self.source = source
        self.sha256 = sha256
        self.entries = entries
        self.skipped = skipped
        self._handle = handle
        self._zip = zf

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def files(self) -> list[str]:
        return [str(entry.path) for entry in self.entries]

    def has_file(self, path: str) -> bool:
        target = PurePosixPath(path)
        return any(entry.path == target for entry in self.entries)

    def directories(self) -> list[PurePosixPath]:
        """Every directory implied by the file paths, parents first."""
        dirs = {parent for entry in self.entries for parent in entry.path.parents if parent.parts}
        return sorted(dirs, key=lambda d: (len(d.parts), str(d)))

    def write_tar(self, fileobj: IO[bytes]) -> None:
        """Write the bundle to ``fileobj`` as an uncompressed TAR archive.

        Members are owned by root, use the modes from :attr:`BundleEntry.mode`
        and are streamed from the ZIP in chunks, so large model files are never
        held in memory.
        """
        with tarfile.open(fileobj=fileobj, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for directory in self.directories():
                info = tarfile.TarInfo(str(directory))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(_owned_by_root(info))
            for entry in self.entries:
                info = tarfile.TarInfo(str(entry.path))
                info.size = entry.size
                info.mode = entry.mode
                info.mtime = _zip_mtime(entry.info)
                with self._zip.open(entry.info) as src:
                    reader = _EntryReader(src, entry)
                    tar.addfile(_owned_by_root(info), reader)
                    # Reading past the declared size must yield nothing.
                    if reader.read(1):
                        raise BundleError(
                            f"Corrupt ZIP entry {entry.info.filename!r}: "
                            "more data than its declared size"
                        )

    def close(self) -> None:
        self._zip.close()
        self._handle.close()

    def __enter__(self) -> Bundle:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _owned_by_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def open_bundle(zip_path: str | Path, limits: ZipLimits = DEFAULT_LIMITS) -> Bundle:
    """Open and validate a model ZIP. Raises BundleError if it is unusable."""
    source = Path(zip_path).expanduser()
    if not source.is_file():
        raise BundleError(f"ZIP file not found: {source}")

    handle = source.open("rb")
    try:
        # Hash and read the same open file, so the recorded hash always
        # describes the bytes that were built.
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(chunk)
        handle.seek(0)
        try:
            zf = zipfile.ZipFile(handle)
        except zipfile.BadZipFile as exc:
            raise BundleError(f"{source} is not a valid ZIP file: {exc}") from exc
        try:
            entries, skipped = validate_entries(zf, limits)
        except BaseException:
            zf.close()
            raise
    except BaseException:
        handle.close()
        raise
    return Bundle(source, digest.hexdigest(), handle, zf, entries, skipped)
