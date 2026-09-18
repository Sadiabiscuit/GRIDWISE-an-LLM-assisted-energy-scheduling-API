import math
from typing import Any

from .models import BatteryInput, DirectiveInterpretation


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _valid_hours(hours: Any) -> bool:
    return (
        isinstance(hours, list)
        and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in hours)
        and len(hours) == len(set(hours))
        and hours == sorted(hours)
    )


def validate_interpretations(
    raw: Any,
    note_count: int,
    battery: BatteryInput,
) -> list[DirectiveInterpretation]:
    """Strictly validate the LLM output before it can affect optimization."""
    if not isinstance(raw, dict) or not isinstance(raw.get("interpretations"), list):
        raise ValueError("LLM output must contain an interpretations array")

    items = raw["interpretations"]
    if len(items) != note_count:
        raise ValueError("LLM must return exactly one interpretation per note")

    seen: set[int] = set()

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each interpretation must be an object")
        try:
            parsed = DirectiveInterpretation.model_validate(item)
        except Exception as exc:
            raise ValueError("Malformed directive interpretation") from exc

        if parsed.note_index not in range(note_count) or parsed.note_index in seen:
            raise ValueError("Invalid or duplicate note_index")
        seen.add(parsed.note_index)

        if not parsed.explanation.strip():
            raise ValueError("Explanation must not be empty")

        adj = parsed.structured_adjustment
        if parsed.directive_type == "no_op":
            if parsed.applies is not False or adj is not None:
                raise ValueError("no_op must use applies=false and null adjustment")
        else:
            if parsed.applies is not True or not isinstance(adj, dict):
                raise ValueError("Applicable directives must use applies=true and an adjustment")
            hours = adj.get("hours")
            if not _valid_hours(hours):
                raise ValueError("hours must be ascending unique integers from 0 through 23")

            if parsed.directive_type == "solar_reduction":
                if set(adj) != {"hours", "factor"} or not _finite_number(adj.get("factor")):
                    raise ValueError("Invalid solar_reduction adjustment")
                if not 0 <= float(adj["factor"]) <= 1:
                    raise ValueError("solar factor must be between 0 and 1")

            elif parsed.directive_type == "minimum_battery_reserve":
                if set(adj) != {"hours", "minimum_energy_kwh"} or not _finite_number(adj.get("minimum_energy_kwh")):
                    raise ValueError("Invalid minimum_battery_reserve adjustment")
                reserve = float(adj["minimum_energy_kwh"])
                if reserve < 0 or reserve > battery.capacity_kwh:
                    raise ValueError("Reserve must be within battery capacity")

            elif parsed.directive_type in {"no_charge_window", "no_discharge_window"}:
                if set(adj) != {"hours"}:
                    raise ValueError("Invalid battery window adjustment")

            elif parsed.directive_type == "max_grid_window":
                if set(adj) != {"hours", "max_grid_kwh"} or not _finite_number(adj.get("max_grid_kwh")):
                    raise ValueError("Invalid max_grid_window adjustment")
                if float(adj["max_grid_kwh"]) < 0:
                    raise ValueError("Grid cap must be non-negative")

    if seen != set(range(note_count)):
        raise ValueError("Missing note interpretation")

    return sorted([DirectiveInterpretation.model_validate(x) for x in items], key=lambda x: x.note_index)
