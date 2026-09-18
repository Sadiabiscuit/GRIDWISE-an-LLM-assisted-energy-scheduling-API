"""Run all organizer public samples against the local service logic."""
import json
import os
from pathlib import Path

os.environ.setdefault("MOCK_LLM", "true")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
data = json.loads((Path(__file__).parent / "public_samples.json").read_text())
for case in data["cases"]:
    response = client.post("/optimize-energy", json=case["input"])
    response.raise_for_status()
    body = response.json()
    expected = case["expected_output"]
    print(f"{case['input']['scenario_id']}: cost={body['total_cost_bdt']} (reference {expected['total_cost_bdt']})")
print("All public samples completed successfully.")
