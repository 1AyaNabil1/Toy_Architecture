"""Command-line interface (build.py)."""

from __future__ import annotations

import pytest

import build
from app.errors import BuildError


def test_dry_run_prints_plan_without_docker(sample_zip, capsys):
    exit_code = build.main(["--zip", str(sample_zip), "--tag", "mmb-sample:v1", "--dry-run"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "/app/inference.py" in out
    assert "Install:    pip install -r requirements.txt" in out
    assert 'CMD ["python", "inference.py"]' in out
    assert "LABEL mini-model-builder.source.sha256=" in out


def test_dry_run_without_inference_script_shows_base_command(make_zip, capsys):
    path = make_zip([("model.onnx", b"\x00")])
    assert build.main(["--zip", str(path), "--tag", "m:v1", "--dry-run"]) == 0
    assert "CMD <the base image's command>" in capsys.readouterr().out


def test_unsafe_zip_exits_with_error(make_zip, capsys):
    path = make_zip([("../../evil.py", "x")])
    assert build.main(["--zip", str(path), "--tag", "m:v1", "--dry-run"]) == 1
    assert "error: Unsafe ZIP entry" in capsys.readouterr().err


def test_size_limit_flag(make_zip, capsys):
    path = make_zip([("model.bin", b"x" * (2 * 1024 * 1024))], name="big.zip")
    assert build.main(["--zip", str(path), "--tag", "m:v1", "--dry-run", "--max-size-mb", "1"]) == 1
    assert "expands to more than" in capsys.readouterr().err


def test_build_passes_options_through(sample_zip, monkeypatch, capsys):
    seen = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return "sha256:abc"

    monkeypatch.setattr(build, "build_image_from_zip", fake_build)
    exit_code = build.main(
        [
            "--zip",
            str(sample_zip),
            "--tag",
            "m:v1",
            "--base-image",
            "python:3.11-slim",
            "--workdir",
            "/srv/model",
            "--cmd",
            "python serve.py --port 8080",
            "--pull",
            "never",
        ]
    )
    assert exit_code == 0
    assert seen["base_image"] == "python:3.11-slim"
    assert seen["workdir"] == "/srv/model"
    assert seen["cmd"] == ["python", "serve.py", "--port", "8080"]
    assert seen["pull"] == "never"
    assert "Image ID: sha256:abc" in capsys.readouterr().out


def test_build_error_exits_with_status_1(sample_zip, monkeypatch, capsys):
    def failing_build(**kwargs):
        raise BuildError("Cannot connect to the Docker Engine.")

    monkeypatch.setattr(build, "build_image_from_zip", failing_build)
    assert build.main(["--zip", str(sample_zip), "--tag", "m:v1"]) == 1
    assert "error: Cannot connect to the Docker Engine." in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ['python "unterminated', ""])
def test_invalid_cmd_is_a_usage_error(sample_zip, cmd):
    with pytest.raises(SystemExit) as excinfo:
        build.main(["--zip", str(sample_zip), "--tag", "m:v1", "--cmd", cmd])
    assert excinfo.value.code == 2
