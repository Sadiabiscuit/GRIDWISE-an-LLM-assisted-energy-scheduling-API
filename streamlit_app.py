
import json
import streamlit as st

from app.guardrails import validate_interpretations
from app.llm import interpret_notes
from app.optimizer import optimize, replay_validate
from app.models import OptimizeRequest

st.set_page_config(
    page_title="GridWise Energy Optimizer",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ GridWise Energy Optimizer")
st.write("LLM-powered energy optimization dashboard")

@st.cache_data
def load_demo():
    with open("example_request.json", "r") as f:
        return json.load(f)

data = load_demo()

st.sidebar.header("Scenario Settings")

scenario_id = st.sidebar.text_input(
    "Scenario ID",
    value=data["scenario_id"]
)

notes_text = st.text_area(
    "Operator Notes",
    value="\n".join(data["operator_notes"]),
    height=150
)

if st.button("🚀 Run Optimization", type="primary"):
    try:
        request_data = data.copy()
        request_data["scenario_id"] = scenario_id
        request_data["operator_notes"] = [
            n.strip() for n in notes_text.splitlines() if n.strip()
        ]

        req = OptimizeRequest(**request_data)

        raw = interpret_notes(
            req.operator_notes,
            req.battery.capacity_kwh
        )

        directives = validate_interpretations(
            raw,
            len(req.operator_notes),
            req.battery
        )

        plan = optimize(
            req.hours,
            req.battery,
            directives
        )

        replay_validate(
            req.hours,
            req.battery,
            directives,
            plan
        )

        total_grid = sum(
            p["grid_kwh"] for p in plan
        )

        total_cost = sum(
            p["grid_kwh"] * h.tariff_bdt_per_kwh
            for p, h in zip(plan, req.hours)
        )

        peak_grid = max(
            p["grid_kwh"] for p in plan
        )

        st.success("Optimization completed successfully!")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Total Grid Energy",
            f"{total_grid:.2f} kWh"
        )

        col2.metric(
            "Total Cost",
            f"৳{total_cost:,.2f}"
        )

        col3.metric(
            "Peak Grid Import",
            f"{peak_grid:.2f} kWh"
        )

        st.subheader("Hourly Energy Schedule")

        st.dataframe(
            plan,
            use_container_width=True
        )

        st.subheader("Directive Interpretation")

        st.json([
            d.model_dump() if hasattr(d, "model_dump")
            else d
            for d in directives
        ])

    except Exception as e:
        st.error(f"Optimization failed: {e}")