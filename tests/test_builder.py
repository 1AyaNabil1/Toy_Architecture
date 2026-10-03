"""Build planning and the Docker build flow, against an in-memory fake client."""

from __future__ import annotations

import pytest
from helpers import FakeClient, FakeContainer, FakeImage, read_tar, read_tar_file

from app.builder import (
    DEFAULT_BASE_IMAGE,
    BuildPlan,
    build_image_from_zip,
    parse_image_tag,
    plan_build,
    validate_workdir,
)
from app.errors import BuildError, BundleError
from app.zip_handler import open_bundle

# --- image tags and working directory ----------------------------------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("my-model:v1", ("my-model", "v1")),
        ("my-model", ("my-model", "latest")),
        ("team/my_model:1.0.2", ("team/my_model", "1.0.2")),
        ("ghcr.io/aya/model:2026-10", ("ghcr.io/aya/model", "2026-10")),
        # Regression: the original `image_tag.split(':')` crashed on registry ports.
        ("localhost:5000/team/model:v1", ("localhost:5000/team/model", "v1")),
        ("localhost:5000/model", ("localhost:5000/model", "latest")),
    ],
)
def test_parse_image_tag(reference, expected):
    assert parse_image_tag(reference) == expected


@pytest.mark.parametrize(
    "reference",
    ["", "My-Model:v1", "my model:v1", "model:", "model:-bad", "model@sha256:abc", "a//b:v1"],
)
def test_parse_image_tag_rejects_invalid_references(reference):
    with pytest.raises(BuildError):
        parse_image_tag(reference)


@pytest.mark.parametrize(
    ("workdir", "expected"),
    [("/app", "/app"), ("/opt/model/", "/opt/model"), ("/srv//model", "/srv/model")],
)
def test_validate_workdir(workdir, expected):
    assert validate_workdir(workdir) == expected


@pytest.mark.parametrize("workdir", ["app", "/", "/app/../etc", "/app; rm -rf /", "$HOME/app", ""])
def test_validate_workdir_rejects_unsafe_values(workdir):
    with pytest.raises(BuildError):
        validate_workdir(workdir)


# --- generated image configuration -------------------------------------------


def test_plan_for_sample_model(sample_zip):
    with open_bundle(sample_zip) as bundle:
        plan = plan_build(bundle, "mmb-sample:v1")
    assert plan.image == "mmb-sample:v1"
    assert plan.base_image == DEFAULT_BASE_IMAGE
    assert plan.install_requirements is True
    assert plan.commit_changes()[:2] == ["WORKDIR /app", 'CMD ["python", "inference.py"]']
    assert plan.labels["mini-model-builder.source.sha256"] == bundle.sha256
    assert plan.labels["mini-model-builder.source.name"] == "sample_model.zip"
    assert plan.labels["org.opencontainers.image.base.name"] == DEFAULT_BASE_IMAGE


def test_plan_custom_cmd_and_workdir(make_zip):
    path = make_zip([("serve.py", "x")])
    with open_bundle(path) as bundle:
        plan = plan_build(
            bundle, "m:v1", workdir="/opt/model", cmd=["python", "serve.py", "--port", "80"]
        )
    assert plan.install_requirements is False
    assert plan.commit_changes()[:2] == [
        "WORKDIR /opt/model",
        'CMD ["python", "serve.py", "--port", "80"]',
    ]


def test_plan_without_inference_script_keeps_base_command(make_zip):
    path = make_zip([("model.onnx", b"\x00")])
    with open_bundle(path) as bundle:
        plan = plan_build(bundle, "m:v1")
    assert plan.cmd is None
    # The build container runs `sleep infinity`; CMD must be reset to the base
    # image's command, never left as that.
    assert 'CMD ["python3"]' in plan.commit_changes(base_cmd=["python3"])


def test_plan_rejects_empty_cmd(make_zip):
    path = make_zip([("inference.py", "x")])
    with open_bundle(path) as bundle, pytest.raises(BuildError, match="must not be empty"):
        plan_build(bundle, "m:v1", cmd=[])


def test_label_values_are_escaped():
    plan = BuildPlan(
        repository="m",
        tag="v1",
        base_image="python:3.12-slim",
        workdir="/app",
        cmd=["python"],
        install_requirements=False,
        labels={"mini-model-builder.source.name": 'odd "name" $HOME\\x\nnext.zip'},
    )
    assert plan.commit_changes()[-1] == (
        'LABEL mini-model-builder.source.name="odd \\"name\\" \\$HOME\\\\x next.zip"'
    )


# --- the build flow against a fake Docker client -----------------------------


def _client_with_base(**container_kwargs) -> FakeClient:
    return FakeClient(
        local_images={DEFAULT_BASE_IMAGE: FakeImage(config={"Cmd": ["python3"]})},
        container=FakeContainer(**container_kwargs),
    )


def test_build_happy_path(sample_zip):
    client = _client_with_base()
    image_id = build_image_from_zip(sample_zip, "mmb-sample:v1", client=client)
    container = client.container

    assert image_id == "sha256:built"
    assert client.images.pulled == []  # base image already present, pull=missing
    assert client.containers.runs == [
        (DEFAULT_BASE_IMAGE, {"command": ["sleep", "infinity"], "detach": True})
    ]

    tar_data = container.archives["/app"]
    assert set(read_tar(tar_data)) == {"inference.py", "model.json", "requirements.txt"}
    assert b"def predict" in read_tar_file(tar_data, "inference.py")

    commands = [cmd for cmd, _ in container.execs]
    assert commands == [
        ["mkdir", "-p", "/app"],
        ["python", "-m", "pip", "install", "-r", "requirements.txt"],
    ]
    pip_kwargs = container.execs[1][1]
    assert pip_kwargs["workdir"] == "/app"
    assert pip_kwargs["environment"]["PIP_NO_CACHE_DIR"] == "1"

    assert container.committed["repository"] == "mmb-sample"
    assert container.committed["tag"] == "v1"
    changes = container.committed["changes"]
    assert changes[:2] == ["WORKDIR /app", 'CMD ["python", "inference.py"]']
    assert any(c.startswith('LABEL mini-model-builder.source.sha256="') for c in changes)
    assert container.removed_with == {"force": True}


def test_build_without_requirements_skips_pip(make_zip):
    path = make_zip([("model.onnx", b"\x00")])
    client = _client_with_base()
    build_image_from_zip(path, "m:v1", client=client)
    assert [cmd for cmd, _ in client.container.execs] == [["mkdir", "-p", "/app"]]
    assert 'CMD ["python3"]' in client.container.committed["changes"]


def test_failed_pip_install_fails_the_build_and_cleans_up(sample_zip):
    # Regression: the original builder ignored pip's exit code and committed
    # an image without its dependencies.
    client = _client_with_base(
        exec_results={"python -m pip": (1, b"Collecting numpy\nERROR: No matching distribution\n")}
    )
    with pytest.raises(BuildError, match="exit code 1") as excinfo:
        build_image_from_zip(sample_zip, "m:v1", client=client)
    assert "No matching distribution" in str(excinfo.value)
    assert client.container.committed is None
    assert client.container.removed_with == {"force": True}


def test_refused_copy_fails_the_build(sample_zip):
    client = _client_with_base(put_ok=False)
    with pytest.raises(BuildError, match="refused to copy"):
        build_image_from_zip(sample_zip, "m:v1", client=client)
    assert client.container.removed_with == {"force": True}


def test_docker_api_error_becomes_build_error(sample_zip):
    client = _client_with_base(fail_on="exec")
    with pytest.raises(BuildError, match="Docker error while building m:v1"):
        build_image_from_zip(sample_zip, "m:v1", client=client)
    assert client.container.removed_with == {"force": True}


def test_missing_base_image_is_pulled(sample_zip):
    client = FakeClient()
    build_image_from_zip(sample_zip, "m:v1", client=client)
    assert client.images.pulled == [DEFAULT_BASE_IMAGE]


def test_pull_always_pulls_even_if_present(sample_zip):
    client = _client_with_base()
    build_image_from_zip(sample_zip, "m:v1", client=client, pull="always")
    assert client.images.pulled == [DEFAULT_BASE_IMAGE]


def test_pull_never_fails_when_base_image_missing(sample_zip):
    client = FakeClient()
    with pytest.raises(BuildError, match="not available locally"):
        build_image_from_zip(sample_zip, "m:v1", client=client, pull="never")
    assert client.containers.runs == []


def test_base_image_with_entrypoint_is_rejected_before_starting(sample_zip):
    client = FakeClient(
        local_images={"custom:1": FakeImage(config={"Entrypoint": ["/entry.sh"], "Cmd": None})}
    )
    with pytest.raises(BuildError, match="ENTRYPOINT"):
        build_image_from_zip(sample_zip, "m:v1", base_image="custom:1", client=client)
    assert client.containers.runs == []


class _UnusableClient:
    def __getattr__(self, name):
        raise AssertionError("Docker must not be touched for an invalid request")


@pytest.mark.parametrize(
    ("entries", "tag", "error"),
    [
        ([("../evil.py", "x")], "m:v1", BundleError),
        ([("inference.py", "x")], "Not A Tag", BuildError),
    ],
)
def test_invalid_input_fails_before_docker_is_used(make_zip, entries, tag, error):
    path = make_zip(entries)
    with pytest.raises(error):
        build_image_from_zip(path, tag, client=_UnusableClient())
