from __future__ import annotations

import logging
import math
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

from .guardrails import validate_interpretations
from .llm import interpret_notes
from .models import OptimizeRequest, OptimizeResponse
from .optimizer import optimize, replay_validate

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM Energy Optimizer", version="1.1.0")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    index_file = Path(__file__).resolve().parent / "static" / "index.html"
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok"}


def _validate_request(req: OptimizeRequest) -> None:
    if [h.hour for h in req.hours] != list(range(24)):
        raise HTTPException(status_code=422, detail="hours must contain exactly 0 through 23 in order")
    b = req.battery
    vals = [b.capacity_kwh, b.initial_energy_kwh, b.minimum_energy_kwh, b.max_charge_kwh_per_hour, b.max_discharge_kwh_per_hour]
    if any(not math.isfinite(float(v)) or float(v) < 0 for v in vals):
        raise HTTPException(status_code=422, detail="battery values must be finite and non-negative")
    if b.initial_energy_kwh > b.capacity_kwh or b.minimum_energy_kwh > b.capacity_kwh or b.initial_energy_kwh < b.minimum_energy_kwh:
        raise HTTPException(status_code=422, detail="invalid battery bounds")
    for h in req.hours:
        if any(not math.isfinite(float(v)) or float(v) < 0 for v in [h.demand_kwh, h.solar_kwh, h.tariff_bdt_per_kwh]):
            raise HTTPException(status_code=422, detail="hourly numeric values must be finite and non-negative")


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(req: OptimizeRequest):
    _validate_request(req)
    try:
        raw = interpret_notes(req.operator_notes, req.battery.capacity_kwh)
        directives = validate_interpretations(raw, len(req.operator_notes), req.battery)
        plan = optimize(req.hours, req.battery, directives)
        replay_validate(req.hours, req.battery, directives, plan)

        total_grid = sum(p["grid_kwh"] for p in plan)
        total_cost = sum(p["grid_kwh"] * h.tariff_bdt_per_kwh for p, h in zip(plan, req.hours))
        peak_grid = max(p["grid_kwh"] for p in plan)
        active = [d.directive_type for d in directives if d.applies]
        summary = "Prioritizes available solar, shifts battery energy toward lower-cost grid hours, and enforces all applicable operator directives."
        if active:
            summary += " Active directives: " + ", ".join(active) + "."
        else:
            summary += " No operator directive changes the base schedule."

        return OptimizeResponse(
            scenario_id=req.scenario_id,
            directive_interpretation=directives,
            hourly_plan=plan,
            total_grid_kwh=round(total_grid, 4),
            total_cost_bdt=round(total_cost, 4),
            peak_grid_kwh=round(peak_grid, 4),
            plan_summary=summary,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("Validation failure: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Controlled service failure: %s", exc)
        message = str(exc)
        if "LLM provider" in message or "OPENAI_API_KEY" in message:
            raise HTTPException(status_code=503, detail=message)
        raise HTTPException(status_code=503, detail="No feasible optimization plan was found. Check battery limits, reserves, grid caps, and operator notes.")
    except Exception:
        logger.exception("Unexpected internal error")
        raise HTTPException(status_code=500, detail="Internal optimization error")


@app.exception_handler(Exception)
async def generic_exception_handler(_, exc: Exception):
    logger.exception("Unhandled request error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
