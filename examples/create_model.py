#!/usr/bin/env python3
"""
Trains the small linear model used by examples/sample_model and saves it
as plain JSON (coefficients + intercept).

JSON is used instead of joblib/pickle on purpose: loading a pickle runs
arbitrary code, so a model file in that format is only as trustworthy as
whoever produced it. A JSON file can only ever be data.

Needs scikit-learn and numpy (not required by the builder itself):
    pip install scikit-learn numpy
    python examples/create_model.py
"""

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LinearRegression

OUTPUT = Path(__file__).parent / "sample_model" / "model.json"

# Create some dummy training data
X_train = np.array([[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]])
y_train = np.array([10, 20, 30, 40])

# Train a simple linear regression model
model = LinearRegression()
model.fit(X_train, y_train)

print("Model trained successfully!")
print(f"Model type: {type(model).__name__}")
print(f"Model coefficients: {model.coef_}")
print(f"Model intercept: {model.intercept_}")

# Save the model as JSON, rounded to 10 decimals to drop floating-point noise
# from the least-squares solve (2.5000000000000018 -> 2.5).
weights = {
    "coef": [round(float(c), 10) for c in model.coef_],
    "intercept": round(float(model.intercept_), 10),
}
OUTPUT.write_text(json.dumps(weights, indent=2) + "\n")
print(f"\nModel saved to {OUTPUT}")

# Test the model
test_input = [[1.0, 2.0, 3.0, 4.0]]
prediction = model.predict(test_input)
print(f"\nTest prediction for {test_input}: {prediction}")
