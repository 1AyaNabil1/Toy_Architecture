"""Connection to the Docker Engine."""

import docker
import docker.errors
import requests

from app.errors import BuildError


def get_client() -> docker.DockerClient:
    """Return a client for the Docker Engine configured by the environment.

    Honours the usual DOCKER_HOST / DOCKER_TLS_VERIFY / DOCKER_CERT_PATH
    variables. Raises BuildError with a readable message if the engine is
    unreachable, instead of a long connection traceback.
    """
    try:
        client = docker.from_env()
        client.ping()
    except (docker.errors.DockerException, requests.exceptions.RequestException) as exc:
        raise BuildError(
            "Cannot connect to the Docker Engine. Is Docker running, and is DOCKER_HOST "
            f"(if set) correct?\nDetails: {exc}"
        ) from exc
    return client
