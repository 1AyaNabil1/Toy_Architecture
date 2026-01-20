"""
Prediction module for sklearn model.
Provides load_model() and predict() functions for model inference.
"""

import joblib
import numpy as np
from pathlib import Path

# Global model cache
_model = None


def load_model(model_path: str = None):
    """
    Load the sklearn model from disk.
    
    Args:
        model_path: Path to the model file. Defaults to model.joblib in the same directory.
    
    Returns:
        The loaded sklearn model.
    """
    global _model
    
    if model_path is None:
        model_path = Path(__file__).parent / "model.joblib"
    
    _model = joblib.load(model_path)
    return _model


def predict(model, inputs: list) -> dict:
    # Use the passed model parameter instead of global
    if model is None:
        raise ValueError("Model parameter is required")
    
    # Handle list of dicts (standard format)
    if inputs and isinstance(inputs[0], dict):
        # Extract feature names from first row
        feature_names = list(inputs[0].keys())
        # Convert to 2D array maintaining feature order
        X = np.array([[row[feat] for feat in feature_names] for row in inputs])
    else:
        # Handle legacy list/array format
        X = np.array(inputs)
        if X.ndim == 1:
            X = X.reshape(1, -1)
    
    # Make predictions using the passed model
    predictions = model.predict(X).tolist()
    
    result = {"predictions": predictions}
    
    # Add probabilities if available (for classifiers)
    if hasattr(model, "predict_proba"):
        try:
            probabilities = model.predict_proba(X).tolist()
            result["probabilities"] = probabilities
        except Exception:
            pass  # Some models may not support predict_proba
    
    return result


if __name__ == "__main__":
    # Example usage
    model = load_model()
    print(f"Model loaded: {type(model).__name__}")
    
    # Example prediction (adjust features based on your model)
    sample_features = [[1.0, 2.0, 3.0, 4.0]]
    result = predict(model, sample_features)
    print(f"Prediction result: {result}")
