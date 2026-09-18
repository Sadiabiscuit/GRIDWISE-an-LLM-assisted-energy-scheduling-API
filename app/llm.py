import json
import os
from typing import Any

import requests


SYSTEM_PROMPT = r"""You are the operator-note interpreter for the GridWise smart-campus energy challenge.
Your ONLY job is to convert each natural-language operator note into exactly one supported structured directive.
Do not optimize the schedule. Do not invent facts. Do not alter demand, solar, tariff, or battery parameters.

Supported directive types and exact shapes:
1. solar_reduction: {"hours":[...],"factor":number}, where factor is the usable fraction remaining.
2. minimum_battery_reserve: {"hours":[...],"minimum_energy_kwh":number}
3. no_charge_window: {"hours":[...]}
4. no_discharge_window: {"hours":[...]}
5. max_grid_window: {"hours":[...],"max_grid_kwh":number}
6. no_op: null, with applies=false.

Time convention: start is inclusive, end is exclusive. Thus 1 PM to 3 PM means [13,14].
Hours must be unique ascending integers 0..23.
For an applicable directive, applies=true. Only no_op uses applies=false.
If a note is irrelevant to the 24-hour energy schedule, use no_op. Never infer an unsupported directive.

Return JSON only, with this exact top-level shape:
{"interpretations":[
  {"note_index":0,"applies":true,"directive_type":"...","structured_adjustment":{...},"explanation":"short reason"}
]}
"""


def _mock_interpret(notes: list[str], battery_capacity_kwh: float | None = None) -> dict[str, Any]:
    """Local-only testing fallback. Never use this mode for judging."""
    import re

    out = []
    for i, note in enumerate(notes):
        s = note.lower()
        def hours_from_range(text: str):
            token = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"
            m = re.search(rf"(?:from|between)\s+({token})\s*(?:to|and|until|-)\s+({token})", text)
            if not m:
                m = re.search(rf"({token})\s*(?:to|and|until|-)\s*({token})", text)
            if not m:
                return None
            def to_hour(raw: str, default_ampm: str | None = None):
                raw = raw.strip().lower()
                if raw == "noon": return 12
                if raw == "midnight": return 0
                mm = re.match(r"(\d{1,2})(?::\d{2})?\s*(am|pm)?", raw)
                h = int(mm.group(1)); ap = mm.group(2) or default_ampm
                if ap == "pm" and h != 12: h += 12
                if ap == "am" and h == 12: h = 0
                return h
            a_raw, b_raw = m.group(1), m.group(2)
            ap = re.search(r"(am|pm)", b_raw.lower())
            default_ap = ap.group(1) if ap else (re.search(r"(am|pm)", a_raw.lower()).group(1) if re.search(r"(am|pm)", a_raw.lower()) else None)
            a, b = to_hour(a_raw, default_ap), to_hour(b_raw, default_ap)
            return list(range(a, b))
        hours = hours_from_range(s)
        if "solar" in s or "pv" in s or "panel" in s and ("output" in s or "production" in s or "wash" in s or "inspection" in s):
            factor = None
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
            if m:
                pct = float(m.group(1))
                if "reduction" in s or "drop" in s:
                    factor = round(1 - pct / 100, 6)
                else:
                    factor = round(pct / 100, 6)
            elif "half" in s or "one-half" in s or "one half" in s:
                factor = 0.5
            elif "one-fifth" in s or "one fifth" in s:
                factor = 0.2
            if factor is not None and hours is not None:
                out.append({"note_index": i, "applies": True, "directive_type": "solar_reduction", "structured_adjustment": {"hours": hours, "factor": factor}, "explanation": "Solar availability is reduced in the stated window."})
                continue
        if "do not charge" in s or "don't charge" in s or ("charger" in s or "charging" in s) and ("unavailable" in s or "prohibited" in s or "disabled" in s or "isolated" in s):
            out.append({"note_index": i, "applies": True, "directive_type": "no_charge_window", "structured_adjustment": {"hours": hours or []}, "explanation": "Battery charging is unavailable in the stated window."})
            continue
        if "do not discharge" in s or "don't discharge" in s or "must not discharge" in s or "discharge" in s and ("unavailable" in s or "prohibited" in s):
            out.append({"note_index": i, "applies": True, "directive_type": "no_discharge_window", "structured_adjustment": {"hours": hours or []}, "explanation": "Battery discharging is unavailable in the stated window."})
            continue
        m = re.search(r"(?:at least|minimum|reserve(?: of)?)\s+(\d+(?:\.\d+)?)\s*kwh", s)
        if m and hours is not None:
            out.append({"note_index": i, "applies": True, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": hours, "minimum_energy_kwh": float(m.group(1))}, "explanation": "A minimum battery reserve is required in the stated window."})
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of|of the)?\s*(?:the )?battery capacity", s)
        if m and hours is not None and battery_capacity_kwh is not None:
            reserve_value = battery_capacity_kwh * float(m.group(1)) / 100.0
            out.append({"note_index": i, "applies": True, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": hours, "minimum_energy_kwh": reserve_value}, "explanation": "A percentage of battery capacity is reserved in the stated window."})
            continue
        if m and hours is not None:
            out.append({"note_index": i, "applies": True, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": hours, "minimum_energy_kwh": float(m.group(1))}, "explanation": "A minimum battery reserve is required in the stated window."})
            continue
        m = re.search(r"(?:cap|limit|maximum|max(?:imum)?|not exceed|at or below)\D*(\d+(?:\.\d+)?)\s*kwh", s)
        if ("grid" in s or "import" in s or "transformer" in s or "substation" in s) and m and hours is not None:
            out.append({"note_index": i, "applies": True, "directive_type": "max_grid_window", "structured_adjustment": {"hours": hours, "max_grid_kwh": float(m.group(1))}, "explanation": "Grid import is capped in the stated window."})
            continue
        out.append({"note_index": i, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "This note does not affect the energy schedule."})
    return {"interpretations": out}


def interpret_notes(notes: list[str], battery_capacity_kwh: float | None = None) -> dict[str, Any]:
    if os.getenv("MOCK_LLM", "false").lower() == "true":
        return _mock_interpret(notes, battery_capacity_kwh)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        # Keep local development usable while preserving a strict deployment mode.
        # Set REQUIRE_LLM=true in judging/production environments.
        if os.getenv("REQUIRE_LLM", "false").lower() != "true":
            return _mock_interpret(notes, battery_capacity_kwh)
        raise RuntimeError("LLM provider is not configured; set OPENAI_API_KEY or disable REQUIRE_LLM for local mode")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_prompt = "Interpret these operator notes independently. Return exactly one interpretation for each note, preserving note_index order. The scenario battery capacity is provided so percentage reserve notes can be converted to kWh.\n\n" + json.dumps({"operator_notes": notes, "battery_capacity_kwh": battery_capacity_kwh}, ensure_ascii=False)

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError("LLM provider request failed")
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
