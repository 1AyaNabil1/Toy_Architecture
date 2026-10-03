"""ZIP validation and ZIP -> TAR conversion."""

from __future__ import annotations

import hashlib
import io
import stat
import struct
import tarfile
import zipfile

import pytest
from helpers import read_tar, read_tar_file

from app.errors import BundleError
from app.zip_handler import DEFAULT_LIMITS, ZipLimits, open_bundle


def _zipinfo(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3  # Unix, so external_attr carries a file mode
    info.external_attr = mode << 16
    return info


def _bundle_files(path, limits=DEFAULT_LIMITS) -> list[str]:
    with open_bundle(path, limits) as bundle:
        return bundle.files


# --- what goes into the image ------------------------------------------------


def test_nested_directories_are_preserved(make_zip):
    # Regression: the original builder copied every file by basename, so these
    # two __init__.py files collided in /app and the package layout was lost.
    path = make_zip(
        [
            ("inference.py", "print('hi')"),
            ("utils/__init__.py", "# utils"),
            ("config/__init__.py", "# config"),
        ]
    )
    assert _bundle_files(path) == ["inference.py", "utils/__init__.py", "config/__init__.py"]


def test_single_top_level_folder_is_stripped(make_zip):
    path = make_zip(
        [
            ("sample_model/", ""),
            ("sample_model/inference.py", "x"),
            ("sample_model/weights/model.bin", b"\x00\x01"),
        ]
    )
    assert _bundle_files(path) == ["inference.py", "weights/model.bin"]


def test_top_level_folder_kept_when_files_sit_beside_it(make_zip):
    path = make_zip([("inference.py", "x"), ("weights/model.bin", b"\x00")])
    assert _bundle_files(path) == ["inference.py", "weights/model.bin"]


def test_hidden_and_os_junk_entries_are_skipped(make_zip):
    path = make_zip(
        [
            ("model/inference.py", "x"),
            ("model/.env", "API_KEY=do-not-ship"),
            ("model/.git/config", "[core]"),
            ("model/.DS_Store", b"\x00"),
            ("__MACOSX/model/._inference.py", b"\x00"),
            ("model/__pycache__/inference.cpython-312.pyc", b"\x00"),
        ]
    )
    with open_bundle(path) as bundle:
        assert bundle.files == ["inference.py"]
        assert len(bundle.skipped) == 5
        assert "model/.env" in bundle.skipped


def test_sha256_and_sizes_are_reported(make_zip):
    path = make_zip([("a.txt", "abc"), ("b.txt", "de")])
    with open_bundle(path) as bundle:
        assert bundle.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert bundle.total_bytes == 5
        assert bundle.has_file("a.txt")
        assert not bundle.has_file("requirements.txt")


def test_sample_model_bundle_is_valid(sample_zip):
    with open_bundle(sample_zip) as bundle:
        assert sorted(bundle.files) == ["inference.py", "model.json", "requirements.txt"]


# --- path traversal (zip-slip) and other unsafe entries ----------------------


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "model/../../evil.py",
        "/etc/cron.d/evil",
        "C:/Windows/evil.py",
        "..\\evil.py",
        "model\\..\\..\\evil.py",
    ],
)
def test_path_traversal_is_rejected(make_zip, name):
    path = make_zip([("inference.py", "x"), (name, "pwned")])
    with pytest.raises(BundleError, match="Unsafe ZIP entry"):
        open_bundle(path)


def test_traversal_is_rejected_even_inside_skipped_folders(make_zip):
    path = make_zip([("inference.py", "x"), ("__MACOSX/../../evil.py", "pwned")])
    with pytest.raises(BundleError, match="Unsafe ZIP entry"):
        open_bundle(path)


def test_symlink_entry_is_rejected(make_zip):
    link = _zipinfo("model/link", stat.S_IFLNK | 0o777)
    path = make_zip([("model/inference.py", "x"), (link, "/etc/passwd")])
    with pytest.raises(BundleError, match="symbolic links"):
        open_bundle(path)


def test_encrypted_entry_is_rejected(make_zip):
    path = make_zip([("inference.py", "x")], compression=zipfile.ZIP_STORED)
    data = bytearray(path.read_bytes())
    # Set the "encrypted" flag bit in the local and central directory headers.
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.index(signature) + flag_offset
        (flags,) = struct.unpack_from("<H", data, at)
        struct.pack_into("<H", data, at, flags | 0x1)
    path.write_bytes(bytes(data))
    with pytest.raises(BundleError, match="encrypted"):
        open_bundle(path)


def test_duplicate_paths_are_rejected(make_zip):
    path = make_zip([("inference.py", "safe"), ("./inference.py", "evil")])
    with pytest.raises(BundleError, match="more than once"):
        open_bundle(path)


def test_file_and_directory_clash_is_rejected(make_zip):
    path = make_zip([("weights", "file"), ("weights/model.bin", b"\x00")])
    with pytest.raises(BundleError, match="both a file and a directory"):
        open_bundle(path)


# --- zip bombs and limits ----------------------------------------------------


def test_highly_compressed_entry_is_rejected(make_zip):
    # 8 MiB of zeros deflates to a few KiB: a classic zip-bomb shape.
    path = make_zip([("model.bin", bytes(8 * 1024 * 1024))])
    assert path.stat().st_size < 100_000
    with pytest.raises(BundleError, match="compression ratio"):
        open_bundle(path)


def test_total_uncompressed_size_limit(make_zip):
    path = make_zip([("a.bin", b"a" * 600), ("b.bin", b"b" * 600)])
    with pytest.raises(BundleError, match="expands to more than 1000 bytes"):
        open_bundle(path, ZipLimits(max_total_bytes=1000))
    assert _bundle_files(path, ZipLimits(max_total_bytes=1200)) == ["a.bin", "b.bin"]


def test_entry_count_limit(make_zip):
    path = make_zip([(f"f{i}.txt", "x") for i in range(4)])
    with pytest.raises(BundleError, match="4 entries; the limit is 3"):
        open_bundle(path, ZipLimits(max_entries=3))


# --- unusable input ----------------------------------------------------------


def test_missing_file(tmp_path):
    with pytest.raises(BundleError, match="not found"):
        open_bundle(tmp_path / "nope.zip")


def test_not_a_zip(tmp_path):
    path = tmp_path / "model.zip"
    path.write_text("definitely not a zip")
    with pytest.raises(BundleError, match="not a valid ZIP"):
        open_bundle(path)


def test_zip_with_only_junk_is_rejected(make_zip):
    path = make_zip([("__MACOSX/._x", b"\x00"), (".DS_Store", b"\x00"), ("empty_dir/", "")])
    with pytest.raises(BundleError, match="no usable files"):
        open_bundle(path)


# --- TAR stream sent to Docker -----------------------------------------------


def test_write_tar_contents_and_ownership(make_zip):
    path = make_zip(
        [
            ("pkg/inference.py", "print('hi')\n"),
            ("pkg/utils/helpers.py", "X = 1\n"),
            ("pkg/weights/model.bin", bytes(range(256)) * 4),
        ]
    )
    buffer = io.BytesIO()
    with open_bundle(path) as bundle:
        bundle.write_tar(buffer)
    data = buffer.getvalue()

    members = read_tar(data)
    assert list(members) == [
        "utils",
        "weights",
        "inference.py",
        "utils/helpers.py",
        "weights/model.bin",
    ]
    assert members["utils"].isdir() and members["utils"].mode == 0o755
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "root" for m in members.values())
    assert not any(name.startswith(("/", "..")) or "/../" in name for name in members)
    assert read_tar_file(data, "inference.py") == b"print('hi')\n"
    assert read_tar_file(data, "weights/model.bin") == bytes(range(256)) * 4


def test_write_tar_modes_keep_only_the_executable_bit(make_zip):
    path = make_zip(
        [
            (_zipinfo("run.sh", stat.S_IFREG | 0o755), "#!/bin/sh\n"),
            (_zipinfo("setuid", stat.S_IFREG | 0o4777), "x"),
            (_zipinfo("data.json", stat.S_IFREG | 0o666), "{}"),
            ("plain.txt", "no unix mode recorded"),
        ]
    )
    buffer = io.BytesIO()
    with open_bundle(path) as bundle:
        bundle.write_tar(buffer)
    modes = {name: member.mode for name, member in read_tar(buffer.getvalue()).items()}
    assert modes == {"run.sh": 0o755, "setuid": 0o755, "data.json": 0o644, "plain.txt": 0o644}


def test_write_tar_detects_corrupted_entry(make_zip):
    payload = b"model-weights-" * 100
    path = make_zip([("model.bin", payload)], compression=zipfile.ZIP_STORED)
    data = path.read_bytes()
    at = data.index(payload) + 10
    path.write_bytes(data[:at] + b"X" + data[at + 1 :])  # flip one byte, CRC no longer matches
    with open_bundle(path) as bundle, pytest.raises(BundleError, match="Corrupt ZIP entry"):
        bundle.write_tar(io.BytesIO())


def test_write_tar_streams_large_files(make_zip):
    # Larger than one copy chunk, stored uncompressed so the ratio check is not involved.
    payload = bytes(range(256)) * 12_000  # ~3 MiB
    path = make_zip([("big.bin", payload)], compression=zipfile.ZIP_STORED)
    buffer = io.BytesIO()
    with open_bundle(path) as bundle:
        bundle.write_tar(buffer)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as tar:
        assert tar.extractfile("big.bin").read() == payload
