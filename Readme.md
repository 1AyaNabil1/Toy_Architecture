# Mini Model Builder

Build Docker images from ZIP files using Docker Engine API without Dockerfile.

## Architecture

The build system exposes a programmatic CLI interface that converts ZIP artifacts into OCI images using the Docker Engine API.

## Project Structure

```
mini-model-builder/
├── build.py                 ← CLI ENTRY POINT
├── app/
│   ├── docker_client.py
│   ├── zip_handler.py
│   ├── builder.py           ← CORE BUILD LOGIC
│   └── __init__.py
├── .gitignore
└── README.md
```

**Note:** Model ZIP files are inputs, not project assets. Store them externally (e.g., `~/model_uploads/`).

## Usage

### Build an Image

```bash
# ZIP files should be stored outside the repository
python build.py \
  --zip ~/model_uploads/sample_model.zip \
  --tag my-model:v1
```

### Validate the Image

```bash
# Check Python version
docker run --rm my-model:v1 python --version

# List files in /app
docker run --rm my-model:v1 ls -lh /app

# Run inference (if your model has inference.py)
docker run --rm my-model:v1 python /app/inference.py
```

## How It Works

1. **Unzip**: Extract model files from ZIP to temporary directory
2. **Create Container**: Start a base Python container
3. **Copy Files**: Transfer files from ZIP into container using TAR streams
4. **Execute Commands**: Install dependencies (if requirements.txt exists)
5. **Commit Image**: Save container state as Docker image
6. **Cleanup**: Remove temporary files and container

## Requirements

- Docker Engine running locally
- Python 3.10+
- `docker` Python package

## Example ZIP Contents

Your model ZIP should contain:

- `inference.py` - Model inference script
- `model.joblib` - Trained model artifact
- `requirements.txt` - Python dependencies (will be auto-installed during build)