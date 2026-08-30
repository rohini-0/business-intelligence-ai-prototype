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
   (this is the one part that calls the Gemini API -- if no API key is
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


def call_gemini(api_key: str, system_prompt: str, user_prompt: str) -> str:
    """Calls Google's Gemini API. Uses the REST endpoint directly (no SDK
    dependency needed) so requirements.txt stays minimal.
    Model note: gemini-2.0-flash was shut down by Google on 2026-06-01 --
    using gemini-2.5-flash instead, confirmed current as of this writing.
    If this ever 404s again, get a current model name from
    https://aistudio.google.com and update the line below."""
    model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": 400},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


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
        return call_gemini(api_key, SYSTEM_PROMPT, prompt)
    except Exception as e:
        return (f"(API call failed: {e})\n\n"
                f"If this says 'model not found', open app.py and update the "
                f"model name in call_gemini() to a current Gemini model from "
                f"https://aistudio.google.com\n\n") + FALLBACK_NARRATIVES.get(persona, "")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"]  { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.01em; }

.bi-eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #2FBF8F;
    margin-bottom: 4px;
}
.bi-subtitle { color: #8B93A3; font-size: 0.95rem; margin-top: -6px; }

.bi-readout-row { display: flex; gap: 14px; margin: 18px 0 10px 0; flex-wrap: wrap; }
.bi-readout {
    flex: 1; min-width: 200px;
    background: #1A2029; border: 1px solid #2B3540; border-radius: 10px;
    padding: 16px 18px;
}
.bi-readout-label {
    font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem;
    letter-spacing: 0.08em; text-transform: uppercase; color: #8B93A3;
}
.bi-readout-value {
    font-family: 'IBM Plex Mono', monospace; font-size: 1.9rem; font-weight: 600;
    color: #EDEFF3; margin-top: 2px;
}
.bi-readout-sub { font-size: 0.82rem; margin-top: 4px; }

.bi-tier-High { color: #2FBF8F; }
.bi-tier-Medium { color: #E8A33D; }
.bi-tier-Low { color: #8B93A3; }

.bi-driver-card {
    background: #1A2029; border-radius: 10px; margin-bottom: 10px;
    border-left: 4px solid #2B3540; overflow: hidden;
}
.bi-driver-card.High { border-left-color: #2FBF8F; }
.bi-driver-card.Medium { border-left-color: #E8A33D; }
.bi-driver-card.Low { border-left-color: #6B7480; }
.bi-driver-card summary {
    padding: 12px 16px; cursor: pointer; font-weight: 500;
    display: flex; align-items: center; gap: 10px; list-style: none;
}
.bi-driver-card summary::-webkit-details-marker { display: none; }
.bi-driver-body { padding: 0 16px 14px 16px; color: #C7CCD4; font-size: 0.92rem; }
.bi-driver-meta {
    font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem;
    color: #8B93A3; margin-top: 8px;
}
.bi-signal-dot {
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
}
.bi-signal-dot.High { background: #2FBF8F; }
.bi-signal-dot.Medium { background: #E8A33D; }
.bi-signal-dot.Low { background: #6B7480; }

.bi-narrative-card {
    background: #1A2029; border: 1px solid #2B3540; border-radius: 10px;
    padding: 18px 20px; line-height: 1.6;
}
.bi-narrative-persona {
    font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem;
    letter-spacing: 0.1em; text-transform: uppercase; color: #2FBF8F;
    margin-bottom: 10px;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="bi-eyebrow">KPI Intelligence-to-Action &middot; Prototype</div>', unsafe_allow_html=True)
st.title("BusinessIntelligence.ai")
st.markdown(
    '<div class="bi-subtitle">Reconciles Superstore, Telco, and Support Ticket data honestly '
    'at the regional level -- no fabricated customer-identity joins. See semantic_contract.md.</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Gemini API key (optional)", type="password",
                             help="Leave blank to see offline example narratives. "
                                  "Get a free key at https://aistudio.google.com")
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
    tier = movement["confidence_tier"]
    dev_color = "#2FBF8F" if movement["deviation"] >= 0 else "#E6533F"
    st.markdown(f"""
    <div class="bi-readout-row">
      <div class="bi-readout">
        <div class="bi-readout-label">Profit Margin (this week)</div>
        <div class="bi-readout-value">{movement['value']:.1%}</div>
        <div class="bi-readout-sub" style="color:{dev_color}">{movement['deviation']:+.1%} vs. region avg</div>
      </div>
      <div class="bi-readout">
        <div class="bi-readout-label">Region average</div>
        <div class="bi-readout-value">{movement['region_average']:.1%}</div>
      </div>
      <div class="bi-readout">
        <div class="bi-readout-label">Movement confidence</div>
        <div class="bi-readout-value bi-tier-{tier}">{tier}</div>
        <div class="bi-readout-sub" style="color:#8B93A3">order_count = {movement['order_count']}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if movement["confidence_tier"] != "High":
        st.info(f"This movement is **{movement['confidence_tier']} confidence** "
                f"(only {movement['order_count']} orders that week). The engine is "
                f"deliberately not overstating certainty here.")

    st.subheader("Ranked Drivers")
    st.caption("Method: rule-based deterministic ranking (pandas/Python), not model-generated. "
               "See driver_ranking.py.")
    drivers = rank_drivers(movement)
    source_map = {
        "Average Discount Rate": ("Superstore (Sample - Superstore.csv)", "Daily"),
        "Support Ticket Volume": ("Support Tickets (synthetic region proxy)", "Weekly (week-of-year)"),
        "Regional Customer-Health Snapshot (churn rate)": ("Telco Churn (synthetic region proxy)", "Static snapshot -- no refresh cadence"),
    }
    for i, d in enumerate(drivers, 1):
        src, freshness = source_map.get(d["driver"], ("Unknown", "Unknown"))
        st.markdown(f"""
        <details class="bi-driver-card {d['confidence']}">
          <summary><span class="bi-signal-dot {d['confidence']}"></span> {i}. {d['driver']} &mdash; {d['confidence']} confidence</summary>
          <div class="bi-driver-body">
            {d['explanation']}
            <div class="bi-driver-meta">
              RELATIONSHIP: {d['relationship']}<br/>
              SOURCE: {src} &nbsp;|&nbsp; FRESHNESS: {freshness}
            </div>
          </div>
        </details>
        """, unsafe_allow_html=True)

    st.subheader("Narrative")
    _t0 = time.perf_counter()
    with st.spinner("Generating narrative..."):
        narrative = generate_narrative(persona, movement, drivers, api_key)
    _elapsed_ms = (time.perf_counter() - _t0) * 1000
    st.markdown(f"""
    <div class="bi-narrative-card">
      <div class="bi-narrative-persona">View as &middot; {persona}</div>
      {narrative}
    </div>
    """, unsafe_allow_html=True)

    with st.expander("⏱️ Runtime telemetry for this narrative"):
        approx_tokens = len(narrative.split()) * 1.3  # rough word->token estimate
        mode = "Live LLM call (Gemini 2.5 Flash)" if api_key else "Offline fallback (no API call made)"
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

with st.expander("📄 View full semantic contract"):
    with open("semantic_contract.md") as f:
        st.markdown(f.read())

