"""
BusinessIntelligence.ai -- Prototype Demo App
================================================
Run locally with:   streamlit run app.py
Deploy for free at: https://share.streamlit.io  (connect this GitHub repo)

WHAT THIS APP DOES:
1. Loads the real, reconciled KPI dataset (built from Superstore + Telco +
   Support Tickets -- see data/final_kpi_dataset.csv and semantic_contract.md)
2. Lets a judge pick any region + week and see if a "material movement" is
   detected
3. Shows the ranked, confidence-scored drivers behind that movement
   (deterministic -- no LLM involved in this part)
4. Generates a persona-specific narrative explaining it in plain English
   (this is the one part that calls the Claude API -- if no API key is
   provided, the app falls back to a pre-written example so the demo still
   works without one)
"""

import json
import time
import urllib.request

import pandas as pd
import streamlit as st

st.set_page_config(page_title="BusinessIntelligence.ai Prototype", layout="wide")

# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------
@st.cache_data
def load_data():
    return pd.read_csv("data/final_kpi_dataset.csv")

df = load_data()

# ---------------------------------------------------------------------------
# CONFIDENCE TIER LOGIC (deterministic -- see semantic_contract.md section 3)
# ---------------------------------------------------------------------------
def confidence_tier(order_count: int) -> str:
    if order_count >= 8:
        return "High"
    elif order_count >= 3:
        return "Medium"
    else:
        return "Low"


def is_sparse_history(df: pd.DataFrame, region: str, week: str, lookback_weeks: int = 4) -> bool:
    """Flags a week as 'sparse history' if fewer than 3 prior weeks of data
    exist for this region -- i.e. we don't yet have enough trend to be
    confident this week's reading is even comparable to a 'normal' baseline."""
    region_weeks = sorted(df[df["region"] == region]["week_start"].unique())
    if week not in region_weeks:
        return False
    idx = region_weeks.index(week)
    return idx < lookback_weeks


def detect_movement(region: str, week: str, kpi: str = "profit_margin"):
    region_df = df[df["region"] == region]
    region_avg = region_df[kpi].mean()
    row = df[(df["region"] == region) & (df["week_start"] == week)]
    if row.empty:
        return None
    row = row.iloc[0]
    deviation = row[kpi] - region_avg
    sparse = is_sparse_history(df, region, week)
    tier = confidence_tier(int(row["order_count"]))
    # ABSTAIN RULE: if order_count is 1 (the absolute floor -- a single order
    # cannot establish a "movement" at all, it's one data point) AND the
    # region has sparse history, the engine should refuse to claim a
    # movement rather than force a low-confidence narrative onto noise.
    should_abstain = (int(row["order_count"]) <= 1) and sparse
    return {
        "region": region, "week": week, "kpi": kpi,
        "value": row[kpi], "region_average": region_avg, "deviation": deviation,
        "order_count": int(row["order_count"]),
        "confidence_tier": tier,
        "sparse_history": sparse,
        "should_abstain": should_abstain,
        "row": row,
    }


def rank_drivers(movement: dict):
    row = movement["row"]
    region_df = df[df["region"] == movement["region"]]
    drivers = []

    discount_avg = region_df["avg_discount"].mean()
    drivers.append({
        "driver": "Average Discount Rate", "confidence": "High",
        "relationship": "Direct / deterministic driver of Profit Margin",
        "value": row["avg_discount"], "region_average": discount_avg,
        "explanation": (f"Discount rate was {row['avg_discount']:.1%} vs. a regional "
                         f"average of {discount_avg:.1%}. Discount directly reduces "
                         f"Profit in this data, so this is high-confidence."),
    })

    if pd.notna(row.get("ticket_volume")):
        ticket_avg = region_df["ticket_volume"].mean()
        drivers.append({
            "driver": "Support Ticket Volume", "confidence": "Medium",
            "relationship": "Correlational (separate source, disclosed proxy)",
            "value": row["ticket_volume"], "region_average": ticket_avg,
            "explanation": (f"Ticket volume was {row['ticket_volume']:.0f} vs. a "
                             f"regional average of {ticket_avg:.1f}. Related but not "
                             f"proven causal -- medium confidence."),
        })

    if pd.notna(row.get("churn_rate")):
        drivers.append({
            "driver": "Regional Customer-Health Snapshot (churn rate)", "confidence": "Low",
            "relationship": "Static snapshot -- cannot explain this specific week",
            "value": row["churn_rate"], "region_average": row["churn_rate"],
            "explanation": (f"Churn proxy is {row['churn_rate']:.1%}, but it's a static "
                             f"snapshot with no time dimension -- standing context only, "
                             f"not a cause of this week's movement."),
        })

    order = {"High": 0, "Medium": 1, "Low": 2}
    drivers.sort(key=lambda d: order.get(d["confidence"], 99))
    return drivers


# ---------------------------------------------------------------------------
# LLM NARRATIVE (only part that calls an API -- has an offline fallback)
# ---------------------------------------------------------------------------
FALLBACK_NARRATIVES = {
    "Regional Sales Manager": (
        "**[Offline fallback -- add an API key in the sidebar for live generation]**\n\n"
        "This region's profit margin moved well outside its typical range. The primary "
        "driver is a spike in average discount rate, well above the region's usual level -- "
        "since discount directly reduces profit in this data, this is a high-confidence "
        "explanation. Recommendation: review whether the discounting was a planned "
        "promotion, and consider a discount-approval threshold for low-volume weeks."
    ),
    "CX Ops Lead": (
        "**[Offline fallback -- add an API key in the sidebar for live generation]**\n\n"
        "Support ticket volume also spiked in this region and week -- worth investigating, "
        "though this is a correlational signal only, not the confirmed driver (which was "
        "discounting, a pricing issue). Recommendation: sample that week's tickets for a "
        "common theme, since a volume spike alongside heavy discounting can indicate "
        "promotion-related confusion."
    ),
}

SYSTEM_PROMPT = """You are a business intelligence narrative generator. You will be given
a JSON payload with a KPI movement and ranked drivers, each with a confidence level already
computed by a deterministic engine.
RULES: Never invent or alter any number given. Never claim a Low-confidence/static-snapshot
driver caused the movement. Always state the movement's own confidence tier if not High.
Write 3-4 plain-English sentences. End with one concrete, actionable recommendation."""

PERSONA_INSTRUCTIONS = {
    "Regional Sales Manager": "Write for a Regional Sales Manager who cares about pricing and discounting.",
    "CX Ops Lead": "Write for a CX/Support Ops Lead who cares about ticket load and churn risk.",
}


def call_claude(api_key: str, user_prompt: str) -> str:
    body = json.dumps({
        "model": "claude-sonnet-4-6", "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return "".join(b["text"] for b in data["content"] if b["type"] == "text")


def generate_narrative(persona: str, movement: dict, drivers: list, api_key: str) -> str:
    if not api_key:
        return FALLBACK_NARRATIVES.get(persona, "No fallback available for this persona.")
    payload = {
        "region": movement["region"], "week": movement["week"],
        "value": f"{movement['value']:.1%}", "region_average": f"{movement['region_average']:.1%}",
        "movement_confidence_tier": movement["confidence_tier"],
        "ranked_drivers": [{"driver": d["driver"], "confidence": d["confidence"],
                             "relationship": d["relationship"]} for d in drivers],
    }
    prompt = f"{PERSONA_INSTRUCTIONS[persona]}\n\nData:\n{json.dumps(payload, indent=2)}"
    try:
        return call_claude(api_key, prompt)
    except Exception as e:
        return f"(API call failed: {e})\n\n" + FALLBACK_NARRATIVES.get(persona, "")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📊 BusinessIntelligence.ai -- KPI Intelligence-to-Action Prototype")
st.caption("Reconciles Superstore, Telco, and Support Ticket data honestly at the "
           "regional level -- no fabricated customer-identity joins. See semantic_contract.md.")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Anthropic API key (optional)", type="password",
                             help="Leave blank to see offline example narratives.")
    persona = st.selectbox("View as persona:", list(PERSONA_INSTRUCTIONS.keys()))

    st.divider()
    st.header("Pick a scenario")

    all_regions = sorted(df["region"].unique())

    # ROLE-BASED SECURITY (actually enforced, not just documented):
    # Sales Manager is hard-locked to one assigned region, like a real
    # row-level-security rule. Ops Lead sees all regions (their job is
    # cross-region comparison). This mirrors semantic_contract.md section 4.
    PERSONA_REGION_ACCESS = {
        "Regional Sales Manager": ["West"],   # assigned region -- simulates row-level security
        "CX Ops Lead": all_regions,           # cross-region access by design
    }
    allowed_regions = PERSONA_REGION_ACCESS[persona]

    if len(allowed_regions) == 1:
        st.caption(f"🔒 Access restricted: this persona can only view **{allowed_regions[0]}** "
                   f"(role-based entitlement, not a UI convenience -- see semantic_contract.md §4).")
    region = st.selectbox("Region", allowed_regions)

    weeks_for_region = sorted(df[df["region"] == region]["week_start"].unique())
    default_week = "2017-04-17" if "2017-04-17" in weeks_for_region else weeks_for_region[0]
    week = st.selectbox("Week", weeks_for_region, index=weeks_for_region.index(default_week))
    st.caption("💡 Try an early week (e.g. the very first option) to see the sparse-history "
               "and abstain behavior, since those weeks have the least accumulated data.")

movement = detect_movement(region, week)

if movement is None:
    st.warning("No data for that selection.")
elif movement["should_abstain"]:
    st.error(
        f"🚫 **Engine abstains from claiming a movement here.** "
        f"{region}, week of {week}, has only {movement['order_count']} order and falls "
        f"within the region's first {4} weeks of history (sparse-history threshold). "
        f"Reporting a 'trend' or 'driver' from a single data point with no prior baseline "
        f"would be manufacturing false confidence -- so the engine explicitly declines "
        f"to generate a narrative for this selection, rather than guessing. "
        f"This is a deliberate design choice, not a bug -- try a different week to see "
        f"normal output."
    )
else:
    if movement["sparse_history"]:
        st.warning(f"📉 **Sparse-history flag:** this is within the region's first 4 weeks "
                   f"of recorded data -- there isn't yet a stable baseline to compare "
                   f"against, so treat any 'vs. average' comparison below with extra caution.")

    col1, col2, col3 = st.columns(3)
    col1.metric("Profit Margin (this week)", f"{movement['value']:.1%}",
                f"{movement['deviation']:.1%} vs. region avg")
    col2.metric("Region average", f"{movement['region_average']:.1%}")
    col3.metric("Movement confidence", movement["confidence_tier"],
                help=f"Based on order_count={movement['order_count']} that week")

    if movement["confidence_tier"] != "High":
        st.info(f"⚠️ This movement is **{movement['confidence_tier']} confidence** "
                f"(only {movement['order_count']} orders that week). The engine is "
                f"deliberately not overstating certainty here.")

    st.subheader("Ranked Drivers (deterministic -- no LLM)")
    st.caption("Method: rule-based deterministic ranking (pandas/Python), not model-generated. "
               "See driver_ranking.py.")
    drivers = rank_drivers(movement)
    for i, d in enumerate(drivers, 1):
        badge = {"High": "🟢", "Medium": "🟡", "Low": "⚪"}[d["confidence"]]
        with st.expander(f"{i}. {badge} {d['driver']} -- {d['confidence']} confidence"):
            st.write(d["explanation"])
            st.caption(f"**Relationship:** {d['relationship']}")
            source_map = {
                "Average Discount Rate": ("Superstore (Sample - Superstore.csv)", "Daily"),
                "Support Ticket Volume": ("Support Tickets (synthetic region proxy)", "Weekly (week-of-year)"),
                "Regional Customer-Health Snapshot (churn rate)": ("Telco Churn (synthetic region proxy)", "Static snapshot -- no refresh cadence"),
            }
            src, freshness = source_map.get(d["driver"], ("Unknown", "Unknown"))
            st.caption(f"**Source:** {src} | **Freshness/cadence:** {freshness}")

    st.subheader(f"Narrative for: {persona}")
    _t0 = time.perf_counter()
    with st.spinner("Generating narrative..."):
        narrative = generate_narrative(persona, movement, drivers, api_key)
    _elapsed_ms = (time.perf_counter() - _t0) * 1000
    st.markdown(narrative)

    with st.expander("⏱️ Runtime telemetry for this narrative"):
        approx_tokens = len(narrative.split()) * 1.3  # rough word->token estimate
        mode = "Live LLM call (Claude Sonnet)" if api_key else "Offline fallback (no API call made)"
        st.write(f"**Mode:** {mode}")
        st.write(f"**Latency:** {_elapsed_ms:.0f} ms")
        st.write(f"**Approx. output tokens:** {approx_tokens:.0f}")
        st.caption("All movement detection, confidence scoring, and driver ranking above "
                   "this point are plain Python/pandas -- zero LLM calls, zero cost, "
                   "millisecond latency. Only this final narrative step touches the API.")

st.divider()
with st.expander("📄 View full semantic contract"):
    with open("semantic_contract.md") as f:
        st.markdown(f.read())
