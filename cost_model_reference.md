# Asteroid Mining Cost Model — Equation Structure & Variable Reference

**Purpose:** First-principles financial model producing $/kg of resource delivered to Low Earth Orbit (LEO), compared against Earth-based mining benchmarks. Model is aggregate across a fleet of missions, with three tiers of increasing complexity.

---

## 1. Output Metric

> **Cost per kilogram delivered to LEO ($/kg)**

$$
\text{Cost per kg} = \frac{\text{Total Fleet Mission Cost (\$)}}{\text{Total Resource Mass Delivered to LEO (kg)}}
$$

This is computed annually (or per campaign) across a fleet of N missions targeting different asteroids from the dataset. The output should be a single number that shifts in real time as sliders move.

Secondary outputs (add later):
- Net Present Value (NPV) at a given commodity price
- Break-even commodity price ($/kg)
- Payback period (years)

---

## 2. Earth-Based Mining Benchmarks (Comparison Anchors)

These are the targets your $/kg number needs to beat — or at least approach — to be economically compelling. Values are approximate 2024–2025 spot prices or cost-of-production figures.

### Metals (returned to Earth market)

| Material | Earth mining cost ($/kg) | Notes |
|---|---|---|
| Iron / structural steel | $0.10 – 0.15 | Commodity baseline; nearly zero marginal value in space |
| Nickel | $14 – $18 | Primary near-term asteroid target (M-type) |
| Cobalt | $25 – $40 | High demand for batteries; often co-mined with Ni |
| Platinum | $28,000 – $35,000 | PGM; realistic economic target for early missions |
| Palladium | $35,000 – $55,000 | PGM; catalytic converters, electronics |
| Gold | $55,000 – $65,000 | Benchmark luxury metal |
| Iridium | $45,000 – $55,000 | Rarest PGM; highly asteroid-enriched |

*Key insight:* Only platinum-group metals (PGMs) have market values that could plausibly justify early mission costs. Nickel and cobalt are targets for volume economics at scale.

### Water / Propellant (in-space market — NOT returned to Earth)

| Market | In-space price ($/kg) | Notes |
|---|---|---|
| Water — Earth surface | ~$0.001 | Irrelevant; baseline only |
| Water — ISS (historical, Shuttle-era) | ~$20,000 | Cost of launching from Earth |
| Water — LEO depot (Starship era, projected) | $500 – $2,000 | The price asteroid water must beat |
| LOX/LH2 propellant — LEO depot (projected) | $300 – $1,500 | Derived from water electrolysis + liquefaction |

*Key insight:* Asteroid water is only valuable if it displaces propellant that would otherwise be launched from Earth. The relevant benchmark is the **launch cost to LEO**, not the Earth surface water price.

---

## 3. Model Architecture — Three Tiers

### Tier 1 — All-Slider Baseline (Build First)

No orbital mechanics computed. Every physical quantity is a slider with scientifically grounded bounds. This gives you a working model immediately and reveals which variables dominate cost sensitivity.

#### 3.1 Equation Chain

**Step 1 — Resource mass available per mission**

$$
m_{\text{resource}} = \rho \cdot V \cdot f_{\text{resource}} \cdot \eta_{\text{extraction}}
$$

Where:
- $\rho$ = asteroid bulk density (kg/m³) — slider per taxonomy family
- $V$ = asteroid volume (m³) — derived from diameter (already in dataset)
- $f_{\text{resource}}$ = resource mass fraction (dimensionless) — slider per family and resource type
- $\eta_{\text{extraction}}$ = mining extraction efficiency (0–1) — single slider

**Step 2 — Propellant mass required (Tsiolkovsky rocket equation)**

$$
m_{\text{prop}} = m_{\text{dry}} \cdot \left(e^{\Delta v / v_e} - 1\right)
$$

Where:
- $m_{\text{dry}}$ = spacecraft dry mass (kg) — slider
- $\Delta v$ = total mission delta-v (km/s) — slider in Tier 1 (replaced in Tier 2)
- $v_e = I_{\text{sp}} \cdot g_0$ = effective exhaust velocity (km/s)
  - $I_{\text{sp}}$ = specific impulse — slider (see propulsion options below)
  - $g_0 = 9.80665 \times 10^{-3}$ km/s² (SI-defined standard gravity)

**Step 3 — Spacecraft wet mass**

$$
m_{\text{wet}} = m_{\text{dry}} + m_{\text{prop}}
$$

**Step 4 — Launch cost**

$$
C_{\text{launch}} = m_{\text{wet}} \cdot P_{\text{launch}}
$$

Where $P_{\text{launch}}$ = launch price per kg to LEO ($/kg) — slider

**Step 5 — Operations cost**

$$
C_{\text{ops}} = C_{\text{ops/day}} \cdot T_{\text{mission}}
$$

Where:
- $C_{\text{ops/day}}$ = daily operations cost ($/day) — slider
- $T_{\text{mission}}$ = mission duration (days) — slider in Tier 1 (derived in Tier 2)

**Step 6 — Development & amortization cost**

$$
C_{\text{dev}} = \frac{C_{\text{dev,total}}}{N_{\text{fleet}}}
$$

Where:
- $C_{\text{dev,total}}$ = total spacecraft development cost ($) — slider
- $N_{\text{fleet}}$ = number of missions in fleet / campaign — slider

**Step 7 — Total mission cost**

$$
C_{\text{mission}} = C_{\text{launch}} + C_{\text{ops}} + C_{\text{dev}}
$$

**Step 8 — Resource delivered to LEO**

$$
m_{\text{delivered}} = m_{\text{resource}} \cdot \eta_{\text{return}}
$$

Where $\eta_{\text{return}}$ = fraction of extracted resource successfully returned — slider (accounting for losses in packaging, transport, re-entry)

**Step 9 — Fleet-aggregate $/kg**

$$
\boxed{\frac{\$}{\text{kg}} = \frac{N_{\text{fleet}} \cdot C_{\text{mission}}}{\sum_{i=1}^{N} m_{\text{delivered},i}}}
$$

In the aggregate model, the sum in the denominator runs over all N missions; each mission targets a different asteroid (or asteroid family) from the dataset, so the resource mass varies per mission based on the asteroid's diameter and taxonomy.

---

### Tier 2 — Orbital-Derived Delta-v (Add Next)

Replace the $\Delta v$ slider with values computed from each asteroid's orbital elements (`a`, `e`, `i`, available for all 41,171 objects in the dataset). Also derives mission duration $T_{\text{mission}}$.

#### 3.2 Delta-v Estimation (Simplified Shoemaker-Helin)

For a ballistic Hohmann-like transfer from Earth LEO to asteroid rendezvous and return:

**Heliocentric transfer delta-v (departure leg):**

Using vis-viva, for a transfer ellipse between Earth (1 AU) and asteroid perihelion $q$:

$$
a_{\text{transfer}} = \frac{1 + q}{2} \quad \text{(AU)}
$$

$$
v_{\infty,\text{dep}} = \left| \sqrt{\mu_\odot \left(\frac{2}{1} - \frac{1}{a_{\text{transfer}}}\right)} - v_{\oplus} \right|
$$

Where $\mu_\odot = 132.7 \times 10^9$ km³/s², $v_\oplus = 29.78$ km/s.

**Departure burn from LEO (Oberth effect):**

$$
\Delta v_{\text{dep}} = \sqrt{v_{\infty,\text{dep}}^2 + v_{\text{esc}}^2} - v_{\text{LEO}}
$$

Where $v_{\text{esc}} = \sqrt{2} \cdot v_{\text{LEO}} \approx 10.93$ km/s (escape velocity from LEO altitude, not Earth's surface), $v_{\text{LEO}} = 7.73$ km/s (circular 400 km orbit).

**Arrival delta-v (rendezvous):**

$$
\Delta v_{\text{arr}} = \left| v_{\text{asteroid,peri}} - v_{\text{transfer,arr}} \right|
$$

With $v_{\text{asteroid,peri}} = \sqrt{\mu_\odot (2/q - 1/a)}$ from vis-viva.

**Plane change penalty:**

$$
\Delta v_{\text{plane}} \approx 2 \cdot v_{\text{transfer,mid}} \cdot \sin(i/2)
$$

Applied at the midpoint of the transfer where velocity is lowest (minimizes cost).

**Return delta-v:** Mirror of departure (symmetric Hohmann), or use a free-return trajectory approximation (≈ 60–80% of outbound cost for many NEAs).

**Total mission delta-v:**

$$
\Delta v_{\text{total}} = \Delta v_{\text{dep}} + \Delta v_{\text{arr}} + \Delta v_{\text{plane}} + \Delta v_{\text{return}}
$$

**Mission duration:** Transfer time = half the synodic period of the transfer ellipse:

$$
T_{\text{transfer}} = \pi \sqrt{\frac{a_{\text{transfer}}^3}{\mu_\odot}} \quad \text{(seconds, convert to days)}
$$

Total mission duration = 2 × transfer time + surface time (slider).

#### 3.3 Accessibility Proxy (Quick Filter)

Before full delta-v computation, use MOID and inclination as fast filters to rank asteroids by accessibility:

$$
\text{Accessibility Score} = w_1 \cdot \text{MOID} + w_2 \cdot i + w_3 \cdot |e - e_{\text{Earth}}|
$$

Low score = more accessible. This drives asteroid selection in the aggregate model.

---

### Tier 3 — Full Mission Architecture (Future)

Add when Tiers 1–2 are working:

- Propulsion architecture choice: chemical (high thrust, short trip) vs. solar electric (high Isp, long trip, lower launch mass) with intermediate Δv cost tradeoffs
- Multi-leg missions (gravity assists via Venus/Moon flyby to reduce Δv)
- On-asteroid processing (refine before return, increasing mass ratio of useful payload)
- In-space propellant depot economics (asteroid water → propellant → fuel cost for other missions)
- Financing and time-value-of-money (NPV model with discount rate slider)
- Technology learning curve (launch cost, Isp improving over time)

---

## 4. Propulsion Options (Tier 1 Slider Presets)

These are the practical choices for slider presets. The user selects a propulsion type; $I_{\text{sp}}$ and associated $\Delta v$ range update accordingly.

| Propulsion | Isp (s) | v_e (km/s) | Best for | Δv range for NEAs |
|---|---|---|---|---|
| Chemical (NTO/MMH) | 300 – 330 | 2.94 – 3.24 | Short missions, large payloads | 4 – 8 km/s |
| Chemical (LOX/LH2) | 430 – 460 | 4.22 – 4.51 | High Δv, crewed heritage | 5 – 10 km/s |
| Solar Electric (Hall thruster) | 1,500 – 3,000 | 14.7 – 29.4 | Long missions, small payloads | Any (very efficient) |
| Nuclear Thermal (future) | 800 – 1,000 | 7.85 – 9.81 | Long-range, large payloads | Any |

*For Tier 1: default to chemical NTO/MMH. SEP is the realistic near-term choice for robotic mining missions (used by Hayabusa2, Dawn).*

---

## 5. Complete Variable Registry

### 5.1 Physical / Asteroid Variables (from dataset)

| Variable | Symbol | Source | Notes |
|---|---|---|---|
| Diameter (km) | $d$ | `diameter` / `lowell_iras_diameter_km` | Already in dataset; used to compute volume |
| Bulk density (kg/m³) | $\rho$ | Slider (per family) | Cannot be measured remotely; key uncertainty |
| Resource mass fraction | $f_{\text{resource}}$ | Slider (per family, per resource type) | Metals or water |
| Semi-major axis (AU) | $a$ | `a` | Complete for all 41,171 objects |
| Eccentricity | $e$ | `e` | Complete |
| Inclination (°) | $i$ | `i` | Complete |
| Perihelion (AU) | $q$ | `q` | Complete; $q = a(1-e)$ |
| MOID (AU) | `moid` | `moid` | Earth Minimum Orbit Intersection Distance; accessibility proxy |
| Taxonomy class | — | `lowell_iras_tax_class` | Drives family classification → density & fraction priors |
| Asteroid class | — | `class` | APO / AMO / ATE / IEO — all NEOs |

### 5.2 Mission Architecture Variables (Sliders)

| Variable | Symbol | Tier | Default | Range | Units |
|---|---|---|---|---|---|
| Total mission delta-v | $\Delta v$ | 1 (slider) / 2 (computed) | 6.0 | 3 – 12 | km/s |
| Specific impulse | $I_{\text{sp}}$ | 1 | 320 | 300 – 3000 | s |
| Spacecraft dry mass | $m_{\text{dry}}$ | 1 | 2,000 | 500 – 10,000 | kg |
| Mission duration | $T_{\text{mission}}$ | 1 (slider) / 2 (computed) | 730 | 180 – 1,825 | days |
| Surface / mining time | $T_{\text{surface}}$ | 1 | 90 | 30 – 365 | days |
| Fleet size | $N_{\text{fleet}}$ | 1 | 5 | 1 – 50 | missions/campaign |
| Extraction efficiency | $\eta_{\text{extraction}}$ | 1 | 0.50 | 0.10 – 0.95 | fraction |
| Return efficiency | $\eta_{\text{return}}$ | 1 | 0.80 | 0.50 – 0.99 | fraction |

### 5.3 Cost Variables (Sliders)

| Variable | Symbol | Tier | Default | Range | Units |
|---|---|---|---|---|---|
| Launch price to LEO | $P_{\text{launch}}$ | 1 | 2,000 | 100 – 10,000 | $/kg |
| Daily operations cost | $C_{\text{ops/day}}$ | 1 | 50,000 | 5,000 – 500,000 | $/day |
| Total development cost | $C_{\text{dev,total}}$ | 1 | 500,000,000 | 50M – 5B | $ |

### 5.4 Economic Variables (Outputs & Comparisons)

| Variable | Symbol | Type | Notes |
|---|---|---|---|
| Cost per kg delivered | $/kg | **Primary output** | Fleet-aggregate |
| Earth benchmark price | $P_{\text{Earth}}$ | Reference | Fixed display; from Section 2 table |
| Cost ratio | $/kg ÷ $P_{\text{Earth}}$ | Display | How many times more expensive than Earth mining |
| Break-even fleet size | $N^*$ | Derived | N at which $/kg = Earth benchmark |

---

## 6. Key Sensitivities to Explore First

Based on the rocket equation and cost structure, these variables will dominate — worth making them the most prominent sliders:

1. **Launch price ($/kg to LEO)** — exponentially drives propellant cost; Starship's target of $10–100/kg changes everything
2. **Mission delta-v** — enters exponentially via rocket equation; small changes in Δv → large changes in propellant mass → large changes in launch cost
3. **Specific impulse** — the other side of the exponential; SEP vs. chemical is often the decisive variable
4. **Resource mass fraction** — especially for metals; M-type asteroids at 60–95% metal fraction vs. C-type at 0–5% is the difference between viable and unviable
5. **Development cost amortization** — at small fleet sizes, dev cost dominates; at large fleet sizes, marginal mission cost dominates
6. **Extraction efficiency** — currently treated as a single number; in reality poorly known (Hayabusa2 extracted ~5 g from Ryugu)

---

## 7. Suggested Build Order

1. **Wire up Tier 1** in a self-contained HTML page with all sliders from Section 5.2–5.3. Use the existing taxonomy family aggregates from `asteroid_resource_bounds.csv` as input (not per-asteroid). Show $/kg prominently with Earth benchmark overlay.

2. **Add sensitivity chart** — tornado diagram showing which slider moves $/kg the most. This is the most important analytical output.

3. **Upgrade to Tier 2** — replace $\Delta v$ and $T_{\text{mission}}$ sliders with values computed from orbital data. Show the distribution of accessible asteroids by $/kg.

4. **Add asteroid selector** — let user filter by class, MOID, diameter range, and see how the accessible pool's $/kg distribution shifts.

5. **Add Tier 3 economics** — NPV, break-even price, financing cost.

---

*Document version: 1.0 — May 2026. Equation set intentionally kept to closed-form expressions computable in JavaScript for browser-side interactivity.*
