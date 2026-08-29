# KPI Semantic Contract — BusinessIntelligence.ai Prototype

This document defines every KPI used in the prototype: its formula, source, grain,
refresh cadence, confidence rules, and who is allowed to see it. This is the
"single source of truth" the engine reads from — it is not narrative, it is
the contract every other component (driver ranking, personas, confidence
scoring) is built against.

---

## 1. Data Sources

| Source | Real dimensions available | Native grain | Refresh cadence (assumed) | Role |
|---|---|---|---|---|
| **Superstore** | Region, Order Date, Customer ID, Category | Per order | Daily | Primary transactional system — ground truth for all core business KPIs |
| **Telco Customer Churn** | *(none — no region or date field in source)* | Per customer | N/A — static snapshot | Illustrative proxy for a customer-health signal a real company would source from a separate system |
| **Customer Support Tickets** | *(none — no region field in source)* | Per ticket | Weekly (via Date of Purchase, week-of-year) | Illustrative proxy for a service-quality signal from a separate ticketing system |

**Disclosure (read this before judging the joins):** Telco and Support Tickets
contain no real region field. Region is synthetically assigned per row,
weighted to match Superstore's real regional order-volume distribution (not
uniform random) — e.g. if Superstore shows 32% of orders in the West, 32% of
Telco/Ticket rows are assigned "West." **No individual-level identity is ever
claimed between sources.** Customer ID (Telco) and Ticket ID (Tickets) are
dropped at the moment of aggregation — only the region-level (and for
Tickets, region+week) aggregate crosses into the merged dataset. This is
disclosed here rather than hidden, per the brief's own framing that "reasonable
assumptions" are expected in place of proprietary real data.

---

## 2. KPI Definitions

### KPI 1 — Revenue
- **Formula:** `SUM(Sales)`
- **Source:** Superstore only
- **Grain:** Region × Week
- **Cadence:** Daily refresh
- **Join required:** None (single-source)

### KPI 2 — Profit Margin
- **Formula:** `SUM(Profit) / SUM(Sales)`
- **Source:** Superstore only
- **Grain:** Region × Week
- **Cadence:** Daily refresh
- **Join required:** None (single-source)
- **Known driver relationship:** Average Discount Rate is a direct mathematical
  input to Profit in this dataset — treated as a **high-confidence, deterministic
  driver** whenever Profit Margin moves, not an independently-discovered insight.

### KPI 3 — Repeat Purchase Rate
- **Formula:** `% of customers with COUNT(DISTINCT Order ID) > 1`
- **Source:** Superstore only
- **Grain:** Region (customer-level history rolled up)
- **Cadence:** Daily refresh
- **Join required:** None — this KPI needs no cross-source join at all, and is
  the prototype's clean single-source anchor.

### KPI 4 — Regional Customer-Health Snapshot *(contextual signal, not a standalone business KPI)*
- **Formula:** `AVG(tenure)`, `AVG(Churn = "Yes")`, `AVG(Contract = "Month-to-month")`
- **Source:** Telco, aggregated to region (synthetic region assignment, disclosed above)
- **Grain:** Region only — **static snapshot, not time-varying**, because the
  Telco source has no date field of any kind to reconcile against.
- **Cadence:** Treated as a slow-changing enrichment layer, refreshed
  independently of the weekly transactional feed (e.g. monthly, in a real
  deployment) — for this prototype it is a fixed regional value repeated
  across all weeks.
- **Explicit limitation:** Because this is a snapshot, it can inform a
  standing regional risk context (e.g. "West has structurally higher churn
  exposure") but **cannot** be used to explain a specific week's movement —
  the engine must not claim a snapshot signal moved *in response to* a
  specific week's KPI change.

### KPI 5 — Regional Support Load
- **Formula:** `COUNT(Ticket ID)` (volume), `% Ticket Priority IN (High, Critical)`, `% Ticket Status IN (Open, Pending Customer Response)`
- **Source:** Support Tickets, aggregated to region × week-of-year (synthetic
  region assignment, disclosed above)
- **Grain:** Region × Week-of-year
- **Cadence:** Weekly refresh
- **Calendar note:** Superstore (2014–2017) and Support Tickets (2020–2021)
  share no overlapping absolute calendar week. Reconciliation is therefore
  done on **ISO week-of-year** (i.e., "the 16th week of the retail calendar")
  rather than absolute date — a disclosed normalization, not a hidden one.
- **Data-quality note:** The raw "First Response Time" / "Time to Resolution"
  timestamp pair was tested and found to be unusable — 49.3% of tickets
  showed resolution logged *before* first response, indicating the two
  columns are independently randomized in this public dataset rather than a
  real event sequence. Resolution-duration metrics were therefore dropped
  entirely rather than reported as if trustworthy.

---

## 3. Confidence Tiers (applies to any KPI movement)

Based on the actual order-count distribution in this dataset (median = 5,
75th percentile = 8):

| Tier | Rule | Meaning |
|---|---|---|
| **High** | `order_count >= 8` | Enough transactions that a movement is unlikely to be noise |
| **Medium** | `3 <= order_count < 8` | Directionally meaningful, but flagged as based on a smaller sample |
| **Low** | `order_count < 3` | Engine must explicitly caveat or abstain — insufficient volume to trust the movement |

The same tiering logic applies independently to Support Ticket volume, using
its own distribution, since ticket count and order count are not on the same
scale.

---

## 4. Persona Access Rules

| Persona | KPIs visible | Regions visible | Narrative focus |
|---|---|---|---|
| **Regional Sales Manager** | Revenue, Profit Margin, Repeat Purchase Rate (own region only) | Their assigned region only | Pricing/discount levers, order volume trends |
| **CX / Support Ops Lead** | Regional Support Load, Regional Customer-Health Snapshot (all regions) | All regions (cross-region comparison is their job) | Staffing, ticket triage, churn-risk regions |

This asymmetry (one persona region-locked, one persona cross-region) is the
prototype's role-based security/entitlement demonstration. **This is enforced
in `app.py` (not just documented here)** — the Regional Sales Manager's region
selector is hard-locked to their assigned region; they cannot select another
region through the UI.

---

## 6. Abstain Rule (low-confidence / sparse-history)

The engine explicitly **refuses to generate a narrative** when both of the
following are true for a given region/week:
- `order_count <= 1` (a single order cannot establish a "movement" — it's one
  data point, not a trend)
- The week falls within that region's **first 4 recorded weeks** (sparse
  history — no stable baseline yet exists to compare against)

When only the sparse-history condition is true (but order count is 2-7), the
engine still generates output but **flags it with an explicit caution banner**
rather than silently treating it as equally reliable as a well-established week.

Real example from the data: **East region, week of 2013-12-30** — only 1
order recorded, and it is the region's very first week of history. The
prototype abstains here rather than inventing a driver explanation from a
single data point.

---

## 5. Worked Example (real data, not invented)

**West region, week of 2017-04-17:**
- Profit Margin: **-90.6%** (region average: +15.8%) — material movement
- Average Discount: **20%** (region average: 10.7%) — high-confidence driver (direct formula input)
- Support Ticket Volume: **65** (region average: ~51) — medium-confidence, correlational signal only
- Order Count that week: **6** → falls in the **Medium** confidence tier — the
  engine should present this movement with a caveat about sample size, not
  full certainty
- Following week (2017-04-24): margin recovered to +20.9% as discount fell to
  6% — reinforces discount as the dominant driver, since it moved first
