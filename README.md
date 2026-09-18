# GridWise LLM Energy Optimizer

A FastAPI service for the BUP CSE Fest 2026 Smart Campus Energy Optimization preliminary.

## Architecture

`operator_notes -> LLM interpretation -> deterministic guardrails -> MILP optimizer -> deterministic replay validation -> JSON`

The LLM is used directly to interpret every operator note into one of the six supported directive types. The returned structure is rejected unless it passes deterministic validation. The optimizer then enforces those directives while minimizing grid electricity cost.

## Supported directives

- `solar_reduction`: `{"hours":[...],"factor":number}`
- `minimum_battery_reserve`: `{"hours":[...],"minimum_energy_kwh":number}`
- `no_charge_window`: `{"hours":[...]}`
- `no_discharge_window`: `{"hours":[...]}`
- `max_grid_window`: `{"hours":[...],"max_grid_kwh":number}`
- `no_op`: `null`

Time windows use start-inclusive/end-exclusive whole hours. Example: 1 PM to 3 PM -> `[13,14]`.

## Technology

- Python 3.11
- FastAPI + Uvicorn
- OpenAI-compatible Chat Completions API for operator-note interpretation
- SciPy `milp` for mixed-integer linear optimization
- Pydantic for request/response validation

## Environment variables

Copy `.env.example` to `.env` locally (do not commit it) or export variables in your deployment platform:

- `OPENAI_API_KEY` — required for real LLM-backed operation
- `OPENAI_MODEL` — model identifier, default `gpt-4o-mini`
- `OPENAI_BASE_URL` — default `https://api.openai.com/v1`
- `MOCK_LLM` — `false` for judging; `true` only for local smoke tests without a model key
- `PORT` — service port, default `8000`

The mock interpreter exists only to make local API/optimizer testing possible without credentials. It must not be used for the submitted judging deployment because the challenge requires a generative language model in the operator-note interpretation path.

## Local quickstart

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# For a local smoke test without an LLM key only:
# Windows PowerShell: $env:MOCK_LLM="true"
# Linux/macOS: export MOCK_LLM=true

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

## Public sample test

The repository includes `public_samples.json`, copied from the organizer-provided public sample cases. With `MOCK_LLM=true`, run:

```bash
pytest -q
```

For real LLM operation, unset `MOCK_LLM`, configure `OPENAI_API_KEY`, and start the service again.

## Example request

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  --data @example_request.json
```

## Optimization model

For each hour, the model chooses grid import, solar used, battery charge/discharge, and battery state. It enforces:

- energy balance
- solar availability after directive adjustments
- battery capacity and base reserve
- additional battery reserves
- hourly charge/discharge limits
- no-charge and no-discharge windows
- grid caps
- final battery energy equal to initial energy
- mutually exclusive charge/discharge actions

Objective:

`minimize sum(grid_kwh[h] * tariff_bdt_per_kwh[h])`

After solving, the schedule is replayed independently by deterministic code. A response is not returned if replay validation fails.

## Docker

Build:

```bash
docker build -t gridwise-llm:local .
```

Run with an environment file containing the real key:

```bash
docker run --rm -p 8000:8000 --env-file .env gridwise-llm:local
```

Then:

```bash
curl http://localhost:8000/health
```

Do not bake API keys into the image.

## Deployment

Deploy the container to any public HTTP platform. The service binds to `0.0.0.0` and uses `$PORT` when provided. The judging base URL must expose exactly:

- `GET /health`
- `POST /optimize-energy`

The LLM provider must remain available during judging; keep credentials in the platform's secret/environment-variable store.

## Security

No API key is included in the repository. Provider errors are returned as controlled messages and raw stack traces/secrets are not included in API responses.

## Known limitations

- Hosted LLM availability, quota, and latency depend on the configured provider.
- The public sample cases are not the hidden judge set.
- `MOCK_LLM=true` is intentionally limited to local testing and is not a compliant judging configuration.

## Web dashboard

Open `http://localhost:8000/` after starting Uvicorn. The dashboard can generate a demo request, run the optimizer, and display the cost summary, directives, and hourly schedule.

For local use without an API key, the service uses its deterministic local interpreter unless `REQUIRE_LLM=true`. For challenge judging, configure `OPENAI_API_KEY` and set `REQUIRE_LLM=true`.


## app view

<img width="870" height="407" alt="image" src="https://github.com/user-attachments/assets/7dac02d0-149b-4b02-8a8e-b4c737cabb9c" />
<img width="845" height="380" alt="image" src="https://github.com/user-attachments/assets/c300a2a6-17e6-45b5-a17b-5e775e42fe05" />

