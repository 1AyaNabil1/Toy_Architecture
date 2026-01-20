#!/usr/bin/env python3
"""
Creates a simple trained model for testing inference.py
Run this from your activated virtual environment: python3 create_model.py
"""
import joblib
from sklearn.linear_model import LinearRegression
import numpy as np

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

# Save the model
joblib.dump(model, 'model.joblib')
print("\n✅ Model saved to model.joblib")

# Test the model
test_input = [[1.0, 2.0, 3.0, 4.0]]
prediction = model.predict(test_input)
print(f"\n🧪 Test prediction for {test_input}: {prediction}")
