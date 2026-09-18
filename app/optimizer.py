from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from .models import BatteryInput, DirectiveInterpretation, HourInput


def build_constraints(hours: list[HourInput], battery: BatteryInput, directives: list[DirectiveInterpretation]):
    n = 24
    # Variables per hour: grid, solar, charge, discharge, energy_after, charge_binary, discharge_binary
    G, S, C, D, E, YC, YD = range(7)
    nv = n * 7
    idx = lambda typ, h: typ * n + h

    lower = np.zeros(nv)
    upper = np.full(nv, np.inf)
    c = np.zeros(nv)
    for h, row in enumerate(hours):
        c[idx(G,h)] = row.tariff_bdt_per_kwh
        upper[idx(S,h)] = 0.0  # set below after solar adjustments
        upper[idx(C,h)] = battery.max_charge_kwh_per_hour
        upper[idx(D,h)] = battery.max_discharge_kwh_per_hour
        lower[idx(E,h)] = battery.minimum_energy_kwh
        upper[idx(E,h)] = battery.capacity_kwh
        upper[idx(YC,h)] = 1
        upper[idx(YD,h)] = 1

    solar_factor = np.ones(n)
    reserve = np.full(n, battery.minimum_energy_kwh, dtype=float)
    no_charge = set()
    no_discharge = set()
    grid_cap = np.full(n, np.inf)

    for d in directives:
        if not d.applies or d.directive_type == "no_op":
            continue
        adj = d.structured_adjustment or {}
        hs = adj["hours"]
        if d.directive_type == "solar_reduction":
            factor = float(adj["factor"])
            for h in hs:
                solar_factor[h] *= factor
        elif d.directive_type == "minimum_battery_reserve":
            value = float(adj["minimum_energy_kwh"])
            for h in hs:
                reserve[h] = max(reserve[h], value)
        elif d.directive_type == "no_charge_window":
            no_charge.update(hs)
        elif d.directive_type == "no_discharge_window":
            no_discharge.update(hs)
        elif d.directive_type == "max_grid_window":
            value = float(adj["max_grid_kwh"])
            for h in hs:
                grid_cap[h] = min(grid_cap[h], value)

    for h, row in enumerate(hours):
        upper[idx(S,h)] = max(0.0, row.solar_kwh * solar_factor[h])
        upper[idx(G,h)] = grid_cap[h]
        if h in no_charge:
            upper[idx(C,h)] = 0
            upper[idx(YC,h)] = 0
        if h in no_discharge:
            upper[idx(D,h)] = 0
            upper[idx(YD,h)] = 0
        lower[idx(E,h)] = reserve[h]

    # Build equality/inequality rows.
    rows = []
    lb = []
    ub = []

    # Energy balance: grid + solar + discharge - charge = demand.
    for h, row in enumerate(hours):
        a = {}
        a[idx(G,h)] = 1
        a[idx(S,h)] = 1
        a[idx(D,h)] = 1
        a[idx(C,h)] = -1
        rows.append(a); lb.append(row.demand_kwh); ub.append(row.demand_kwh)

    # Battery transition.
    for h in range(n):
        a = {idx(E,h): 1, idx(C,h): -1, idx(D,h): 1}
        if h == 0:
            rows.append(a); lb.append(battery.initial_energy_kwh); ub.append(battery.initial_energy_kwh)
        else:
            a[idx(E,h-1)] = -1
            rows.append(a); lb.append(0); ub.append(0)

    # Charge/discharge cannot happen simultaneously.
    for h in range(n):
        a = {idx(YC,h): 1, idx(YD,h): 1}
        rows.append(a); lb.append(-np.inf); ub.append(1)
        a = {idx(C,h): 1, idx(YC,h): -battery.max_charge_kwh_per_hour}
        rows.append(a); lb.append(-np.inf); ub.append(0)
        a = {idx(D,h): 1, idx(YD,h): -battery.max_discharge_kwh_per_hour}
        rows.append(a); lb.append(-np.inf); ub.append(0)

    # End-of-day neutrality.
    rows.append({idx(E,23): 1}); lb.append(battery.initial_energy_kwh); ub.append(battery.initial_energy_kwh)

    A = lil_matrix((len(rows), nv), dtype=float)
    for r, row in enumerate(rows):
        for j, value in row.items():
            A[r, j] = value

    integrality = np.zeros(nv)
    integrality[idx(YC,0):idx(YC,0)+n] = 1
    integrality[idx(YD,0):idx(YD,0)+n] = 1

    return c, Bounds(lower, upper), LinearConstraint(A.tocsr(), np.array(lb), np.array(ub)), integrality


def optimize(hours: list[HourInput], battery: BatteryInput, directives: list[DirectiveInterpretation]):
    c, bounds, constraints, integrality = build_constraints(hours, battery, directives)
    result = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": 12, "mip_rel_gap": 1e-8},
    )
    if not result.success or result.x is None:
        raise RuntimeError("No feasible optimization plan was found")

    x = result.x
    n = 24
    G, S, C, D, E = range(5)
    get = lambda typ, h: float(x[typ*n+h])
    plan = []
    for h in range(n):
        charge = max(0.0, get(C,h))
        discharge = max(0.0, get(D,h))
        if charge > 1e-6:
            action = "charge"; batt = charge
        elif discharge > 1e-6:
            action = "discharge"; batt = discharge
        else:
            action = "idle"; batt = 0.0
        plan.append({
            "hour": h,
            "grid_kwh": max(0.0, get(G,h)),
            "solar_used_kwh": max(0.0, get(S,h)),
            "battery_action": action,
            "battery_kwh": batt,
            "battery_energy_after_kwh": get(E,h),
        })
    return plan


def replay_validate(hours: list[HourInput], battery: BatteryInput, directives: list[DirectiveInterpretation], plan: list[dict]) -> None:
    if len(plan) != 24 or [p["hour"] for p in plan] != list(range(24)):
        raise ValueError("Optimizer returned invalid hour sequence")

    solar_factor = [1.0] * 24
    reserve = [battery.minimum_energy_kwh] * 24
    no_charge, no_discharge = set(), set()
    grid_cap = [math.inf] * 24
    for d in directives:
        if not d.applies or d.directive_type == "no_op":
            continue
        adj = d.structured_adjustment
        if d.directive_type == "solar_reduction":
            for h in adj["hours"]: solar_factor[h] *= float(adj["factor"])
        elif d.directive_type == "minimum_battery_reserve":
            for h in adj["hours"]: reserve[h] = max(reserve[h], float(adj["minimum_energy_kwh"]))
        elif d.directive_type == "no_charge_window": no_charge.update(adj["hours"])
        elif d.directive_type == "no_discharge_window": no_discharge.update(adj["hours"])
        elif d.directive_type == "max_grid_window":
            for h in adj["hours"]: grid_cap[h] = min(grid_cap[h], float(adj["max_grid_kwh"]))

    e_before = battery.initial_energy_kwh
    for h, p in enumerate(plan):
        g, s, b = p["grid_kwh"], p["solar_used_kwh"], p["battery_kwh"]
        if min(g, s, b) < -0.01:
            raise ValueError("Negative schedule value")
        if s > hours[h].solar_kwh * solar_factor[h] + 0.01:
            raise ValueError("Solar usage exceeds effective solar")
        if g > grid_cap[h] + 0.01:
            raise ValueError("Grid cap violated")
        if p["battery_action"] == "charge":
            if h in no_charge or b > battery.max_charge_kwh_per_hour + 0.01:
                raise ValueError("Charge directive/rate violated")
            e_after = e_before + b
        elif p["battery_action"] == "discharge":
            if h in no_discharge or b > battery.max_discharge_kwh_per_hour + 0.01:
                raise ValueError("Discharge directive/rate violated")
            e_after = e_before - b
        elif p["battery_action"] == "idle":
            if b > 0.01:
                raise ValueError("Idle action must have zero battery_kwh")
            e_after = e_before
        else:
            raise ValueError("Invalid battery action")
        if e_after < reserve[h] - 0.01 or e_after > battery.capacity_kwh + 0.01:
            raise ValueError("Battery bound/reserve violated")
        if abs(g + s + (b if p["battery_action"] == "discharge" else 0) - hours[h].demand_kwh - (b if p["battery_action"] == "charge" else 0)) > 0.01:
            raise ValueError("Energy balance violated")
        if abs(p["battery_energy_after_kwh"] - e_after) > 0.01:
            raise ValueError("Battery state mismatch")
        e_before = e_after
    if abs(e_before - battery.initial_energy_kwh) > 0.01:
        raise ValueError("End-of-day battery neutrality violated")
