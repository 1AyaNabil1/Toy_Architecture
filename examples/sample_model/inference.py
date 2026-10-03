"""
Sample model bundle for mini-model-builder.

Loads a linear model from model.json (plain data, so loading it cannot run
code, unlike pickle/joblib) and prints predictions as JSON.

    python inference.py                     # predicts for [[1, 2, 3, 4]]
    python inference.py '[[2, 3, 4, 5]]'    # predicts for your own rows
"""

import json
import sys
from pathlib import Path

import numpy as np

MODEL_PATH = Path(__file__).with_name("model.json")


def load_model(model_path: Path = MODEL_PATH) -> tuple[np.ndarray, float]:
    """Load the coefficients and intercept saved by examples/create_model.py."""
    data = json.loads(Path(model_path).read_text())
    return np.asarray(data["coef"], dtype=float), float(data["intercept"])


def predict(model: tuple[np.ndarray, float], inputs: list) -> dict:
    coef, intercept = model
    X = np.asarray(inputs, dtype=float)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    if X.ndim != 2 or X.shape[1] != coef.shape[0]:
        raise ValueError(f"Expected rows of {coef.shape[0]} features, got shape {X.shape}")
    return {"predictions": (X @ coef + intercept).tolist()}


def main(argv: list[str]) -> int:
    inputs = json.loads(argv[1]) if len(argv) > 1 else [[1.0, 2.0, 3.0, 4.0]]
    print(json.dumps(predict(load_model(), inputs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
