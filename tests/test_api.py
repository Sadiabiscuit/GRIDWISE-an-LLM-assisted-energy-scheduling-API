import json
from pathlib import Path

import os
os.environ["MOCK_LLM"] = "true"

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_public_samples_smoke():
    p = Path(__file__).resolve().parents[1] / "public_samples.json"
    data = json.loads(p.read_text())
    for case in data["cases"]:
        r = client.post("/optimize-energy", json=case["input"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scenario_id"] == case["input"]["scenario_id"]
        assert len(body["directive_interpretation"]) == len(case["input"]["operator_notes"])
        assert len(body["hourly_plan"]) == 24
