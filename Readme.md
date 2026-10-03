# Mini Model Builder

[![tests](https://github.com/1AyaNabil1/Toy_Architecture/actions/workflows/test.yml/badge.svg)](https://github.com/1AyaNabil1/Toy_Architecture/actions/workflows/test.yml)

Turns a ZIP of model files into a runnable Docker image through the Docker Engine API, with no Dockerfile.

You give it a ZIP holding your model, an `inference.py` and an optional `requirements.txt`. You get back a tagged image whose default command runs your model. The ZIP is treated as untrusted input: it is checked for path traversal, zip bombs, symlinks and other unsafe entries before Docker is touched.

## How it works

```
model.zip ──validate──▶ start container ──TAR stream──▶ /app ──pip install──▶ commit ──▶ my-model:v1
            (host only)  (python:3.12-slim,                   (inside the      (WORKDIR, CMD,
                          sleep infinity)                      container)       labels)
```

1. **Validate** the ZIP on the host (`app/zip_handler.py`). Nothing is extracted to disk and nothing from the ZIP runs on the host.
2. **Start a build container** from the base image. Its only process is `sleep infinity`.
3. **Copy the files** into the working directory (`/app` by default) as one TAR stream, read straight from the ZIP. Large model files are streamed in chunks instead of being loaded into memory.
4. **Install dependencies** with `python -m pip install -r requirements.txt` inside the container, if the ZIP has one. If pip fails, the build fails.
5. **Commit** the container as the new image. The commit sets `WORKDIR`, `CMD` and labels that record which ZIP the image came from (its SHA-256).
6. **Remove the build container**, whether the build worked or not.

## Quick start

Requirements: Python 3.10+ and a running Docker Engine (Docker Desktop, or `dockerd` on Linux).

```bash
git clone https://github.com/1AyaNabil1/Toy_Architecture.git
cd Toy_Architecture
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Zip the example model bundle (stdlib only, works on any OS)
python -m zipfile -c sample_model.zip examples/sample_model

# See what would be built. This step doesn't need Docker.
python build.py --zip sample_model.zip --tag sample-model:v1 --dry-run

# Build the image, then run it
python build.py --zip sample_model.zip --tag sample-model:v1
docker run --rm sample-model:v1
# {"predictions": [10.0]}
docker run --rm sample-model:v1 python inference.py '[[2, 3, 4, 5]]'
# {"predictions": [20.0]}
```

The example in `examples/sample_model/` is a small linear model saved as JSON. `examples/create_model.py` shows how it was trained with scikit-learn.

## What goes in the ZIP

```
my_model/              ← an optional single top-level folder is stripped
├── inference.py       ← if present, it becomes the image's default command
├── requirements.txt   ← if present, installed with pip during the build
├── model.json         ← your model files, in any format
└── utils/helpers.py   ← subfolders are kept as they are
```

- Files land in the working directory (`/app`) with their folder structure intact. If everything sits under one top-level folder, as happens when you zip a folder, that folder is dropped. So `my_model/inference.py` becomes `/app/inference.py`.
- `requirements.txt` and `inference.py` are only detected at the root of the bundle.
- Hidden files and folders (`.env`, `.git/`, `.DS_Store` …), `__MACOSX/` and `__pycache__/` are skipped, so secrets and junk don't get baked into the image. `--dry-run` lists anything that was skipped.
- The image's default command is `--cmd` if you pass it. If not, it is `python inference.py` when that file exists, or else the base image's own command.

## Configuration

```
python build.py --zip PATH --tag NAME[:TAG] [options]
```

| Option | Default | Description |
| --- | --- | --- |
| `--zip` | required | Model ZIP to build from. |
| `--tag` | required | Image name and tag, e.g. `my-model:v1` or `localhost:5000/team/model:v1`. Checked before the build starts. |
| `--base-image` | `python:3.12-slim` | Image to install the model into. It must have `python`, `pip`, `sleep` and `mkdir`, and no `ENTRYPOINT`. |
| `--workdir` | `/app` | Absolute directory in the image that receives the ZIP contents. |
| `--cmd` | see above | Default command, e.g. `"python serve.py --port 8080"`. It is split like a shell command and stored in exec form. |
| `--pull` | `missing` | `missing` pulls the base image only if it isn't local, `always` pulls every time, `never` works offline. |
| `--max-size-mb` | `2048` | Reject ZIPs that expand to more than this many MiB. |
| `--dry-run` | off | Validate the ZIP and print the files, install step and commit changes without contacting Docker. |

The Docker connection comes from the usual environment variables (`DOCKER_HOST`, `DOCKER_TLS_VERIFY`, `DOCKER_CERT_PATH`). The exit status is `0` on success, `1` for a build or validation error and `2` for invalid arguments.

You can also call the builder from Python:

```python
from app.builder import build_image_from_zip

image_id = build_image_from_zip("sample_model.zip", "sample-model:v1", pull="never")
```

## Security notes

**Checked on every build, before Docker is used:**

- **Path traversal (zip-slip).** Entries with absolute paths, drive letters or `..` components are rejected, and so are entries that use backslashes to hide them. Symlink entries are rejected too. Files are never extracted on the host; they go from the ZIP into a TAR stream that Docker unpacks inside the container.
- **Zip bombs.** The builder enforces limits on the number of entries (10,000), the total uncompressed size (`--max-size-mb`) and the compression ratio of any entry over 1 MiB (200:1). While streaming, each entry must produce exactly its declared size and pass its CRC check.
- **Ambiguous archives.** The builder rejects duplicate paths (including `a.py` next to `./a.py`), paths that are both a file and a folder, and encrypted entries.
- **File modes.** Only the executable bit is carried over from the ZIP. Files become `0644` or `0755` and are owned by root, so setuid, setgid and world-writable bits are dropped.

**Not protected. Treat a model ZIP as code:**

- `inference.py` is arbitrary code. It runs every time someone starts the image.
- `pip install -r requirements.txt` runs with network access inside the build container. A requirements file can point at any index or URL, and installing packages can run their build scripts.
- **Pickle-based model formats** (`.pkl`, `joblib`, most PyTorch `.pt`/`.pth` files) execute code when they are loaded. The builder never loads model files itself; it only copies bytes. But any image built from an untrusted pickle is unsafe to run. Prefer formats that are pure data, such as safetensors, ONNX or JSON. The example model uses JSON for this reason.
- Only build ZIPs from sources you trust, or run the resulting images in a sandbox.

## Limitations

- **Runs as root.** The image runs as root, like the base image. No non-root user is created.
- **No compilers.** The default base image (`python:3.12-slim`) has no compilers. Requirements that need to compile C extensions will fail to install unless you pick a fuller `--base-image`.
- **No ENTRYPOINT support.** Base images that define an `ENTRYPOINT` are rejected, because the build container has to run `sleep infinity`.
- **Not reproducible.** Images are built with `docker commit`, so each build gives a new image ID even for the same ZIP. The ZIP's SHA-256 label shows which input an image came from. It doesn't make builds reproducible.
- **No HTTP server.** The builder packages and runs whatever `inference.py` (or `--cmd`) does. If you want an HTTP API, put the server in your bundle.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest
```

The unit tests run offline and don't need Docker. They cover ZIP validation (traversal, symlinks, bombs, size limits, corrupt entries), the TAR stream sent to Docker, image-tag and working-directory validation, the generated commit changes, and the build flow against an in-memory fake Docker client.

CI (`.github/workflows/test.yml`) runs ruff and pytest on Python 3.10, 3.12 and 3.13. It then runs an end-to-end job on the runner's real Docker Engine. That job builds the sample model, runs the image and checks its output and SHA-256 label. It also checks that no build container is left behind and that a ZIP with path traversal is rejected.

## Project structure

```
├── build.py                  CLI entry point
├── app/
│   ├── builder.py            build plan and Docker build flow
│   ├── zip_handler.py        ZIP validation and ZIP → TAR streaming
│   ├── docker_client.py      Docker Engine connection with a readable error
│   └── errors.py             BuildError / BundleError
├── examples/
│   ├── sample_model/         example bundle: inference.py, model.json, requirements.txt
│   └── create_model.py       trains the example model (needs scikit-learn)
├── tests/                    pytest suite (offline)
└── .github/workflows/        CI: lint, tests, end-to-end Docker build
```
