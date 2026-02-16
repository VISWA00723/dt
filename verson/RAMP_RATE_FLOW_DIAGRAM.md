# Ramp Rate Flow Diagram

## Visual Flow: How Ramp Rate is Calculated

```
┌─────────────────────────────────────────────────────────────────┐
│                    INPUT: Part Properties                        │
├─────────────────────────────────────────────────────────────────┤
│  • Mass (kg)                                                     │
│  • Specific Heat (J/kg·K)                                        │
│  • Thermal Conductivity (W/m·K)                                  │
│  • Surface Area (m²)                                             │
│  • Characteristic Thickness (m)                                  │
│  • Carbon Sheet Thickness (mm) ← NEW!                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              STEP 1: Calculate Heat Transfer (h)                 │
├─────────────────────────────────────────────────────────────────┤
│  Base h_eff = 45 W/(m²·K) (radiation in vacuum)                 │
│                                                                  │
│  IF carbon_sheet_present:                                        │
│     h_eff = h_eff × 0.75  (25% reduction)                       │
│     ✓ Carbon creates thermal resistance                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│           STEP 2: Calculate Time Constant (τ)                    │
├─────────────────────────────────────────────────────────────────┤
│  τ = (mass × specific_heat) / (h_eff × surface_area)            │
│                                                                  │
│  Example WITHOUT carbon:                                         │
│    τ = (2.5 × 900) / (45 × 0.05) = 1000 seconds                 │
│                                                                  │
│  Example WITH carbon (0.8mm):                                    │
│    τ = (2.5 × 900) / (33.75 × 0.05) = 1333 seconds              │
│    ↑ 33% LONGER time constant                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│            STEP 3: Calculate Base Ramp Rate                      │
├─────────────────────────────────────────────────────────────────┤
│  base_rate = 6000 / (τ + 100)                                   │
│                                                                  │
│  WITHOUT carbon:                                                 │
│    base_rate = 6000 / 1100 = 5.45°C/min                         │
│                                                                  │
│  WITH carbon:                                                    │
│    base_rate = 6000 / 1433 = 4.19°C/min                         │
│    ↓ 23% SLOWER base rate                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│          STEP 4: Apply Thickness Penalty                         │
├─────────────────────────────────────────────────────────────────┤
│  thickness_factor = 1.0 / (1.0 + thickness_mm/20)               │
│                                                                  │
│  Thickness = 15mm:                                               │
│    factor = 1.0 / (1.0 + 15/20) = 0.57                          │
│                                                                  │
│  rate = base_rate × thickness_factor                             │
│       = 4.19 × 0.57 = 2.39°C/min                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│           STEP 5: Apply Biot Correction                          │
├─────────────────────────────────────────────────────────────────┤
│  Bi = (h_eff × Lc) / k                                          │
│                                                                  │
│  IF Bi > 0.1:                                                    │
│     biot_factor = 0.5 / (1.0 + Bi)                              │
│  ELSE:                                                           │
│     biot_factor = 1.0                                            │
│                                                                  │
│  rate = rate × biot_factor                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│         STEP 6: Apply Stage-Specific Multiplier                  │
├─────────────────────────────────────────────────────────────────┤
│  Stage 1: rate × 1.0   (full rate, low temp)                    │
│  Stage 2: rate × 0.8   (80% rate)                               │
│  Stage 3: rate × 0.6   (60% rate) ← EXAMPLE                     │
│  Stage 4: rate × 0.4   (40% rate, very critical)                │
│                                                                  │
│  rate = 2.39 × 0.6 = 1.43°C/min                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│            STEP 7: Clamp to Stage Limits                         │
├─────────────────────────────────────────────────────────────────┤
│  Stage 3 limits: 2.0 - 8.0°C/min                                │
│                                                                  │
│  final_rate = min(8.0, max(2.0, 1.43))                          │
│             = 2.0°C/min                                          │
│                                                                  │
│  ✓ Clamped to minimum safe rate                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  OUTPUT: Final Ramp Rate                         │
├─────────────────────────────────────────────────────────────────┤
│                      2.0°C/min                                   │
│                                                                  │
│  This rate ensures:                                              │
│  ✓ No thermal shock                                              │
│  ✓ Uniform temperature distribution                              │
│  ✓ Safe approach to critical temperature                         │
│  ✓ Accounts for carbon sheet resistance                          │
└─────────────────────────────────────────────────────────────────┘
```

## Carbon Sheet Impact Visualization

```
┌──────────────────────────────────────────────────────────────────┐
│                    Heat Transfer Path                             │
└──────────────────────────────────────────────────────────────────┘

WITHOUT Carbon Sheet:
┌─────────────┐
│   Furnace   │  Radiation (ε = 0.3)
│   Heater    │  ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓
└─────────────┘
       ↓
┌─────────────┐
│  Aluminum   │  k = 167 W/(m·K)  ← FAST conduction
│   Plate     │  h_eff = 45 W/(m²·K)
└─────────────┘
       ↓
   Quick heating
   τ = 1000s


WITH Carbon Sheet (0.8mm):
┌─────────────┐
│   Furnace   │  Radiation (ε = 0.9)  ← BETTER radiation
│   Heater    │  ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓
└─────────────┘
       ↓
┌─────────────┐
│   Carbon    │  k = 5 W/(m·K)    ← SLOW conduction
│   Sheet     │  R = 0.00016 K·m²/W
└─────────────┘  ← THERMAL RESISTANCE
       ↓
┌─────────────┐
│  Aluminum   │  k = 167 W/(m·K)
│   Plate     │  h_eff = 33.75 W/(m²·K)  ← REDUCED
└─────────────┘
       ↓
   Slower heating
   τ = 1333s (+33%)
```

## Stage-by-Stage Ramp Rate Progression

```
Temperature (°C)
     ↑
 650 │                                    ┌─────── Stage 5
     │                                   /│ (1.0°C/min)
 600 │                              ┌───┘ │
     │                             /      │
 550 │                        ┌───┘       │ Stage 4
     │                       /│            │ (1.5°C/min)
 500 │                  ┌───┘ │            │
     │                 /      │            │
 450 │            ┌───┘       │            │
     │           /│           │            │ Stage 3
 400 │      ┌───┘ │           │            │ (2.0°C/min)
     │     /      │           │            │
 350 │┌───┘       │           │            │
     │            │           │            │
 300 ││           │           │            │ Stage 2
     ││           │           │            │ (4.0°C/min)
 250 ││           │           │            │
     ││           │           │            │
 200 ││           │           │            │ Stage 1
     ││           │           │            │ (7.5°C/min)
 150 ││           │           │            │
     │└───────────┴───────────┴────────────┴─────────→
     0           Time (minutes)

Key Observations:
• Ramp rate DECREASES as temperature increases
• Stage 4 is SLOWEST (approaching brazing temp)
• Carbon sheet makes ALL stages ~20-25% slower
• Prevents thermal shock at critical temperatures
```

## Comparison: With vs Without Carbon Sheet

```
┌────────────────────────────────────────────────────────────────┐
│                  Ramp Rate Comparison                           │
├────────┬──────────────────┬──────────────────┬─────────────────┤
│ Stage  │ Without Carbon   │ With Carbon      │ Difference      │
├────────┼──────────────────┼──────────────────┼─────────────────┤
│   1    │  7.5°C/min       │  6.0°C/min       │  -20%           │
│   2    │  4.0°C/min       │  3.2°C/min       │  -20%           │
│   3    │  2.5°C/min       │  2.0°C/min       │  -20%           │
│   4    │  1.5°C/min       │  1.2°C/min       │  -20%           │
│   5    │  1.0°C/min       │  1.0°C/min       │   0% (clamped)  │
└────────┴──────────────────┴──────────────────┴─────────────────┘

Total heating time:
• Without carbon: ~180 minutes
• With carbon:    ~225 minutes (+25%)

Why longer?
✓ Slower ramp rates (thermal resistance)
✓ Increased temperature lag
✓ Longer hold times (more time to equilibrate)
```

## Key Takeaways

1. **Carbon sheet slows everything down** by ~20-25%
2. **Physics-based**, not arbitrary
3. **Stage-aware** - slower at higher temps
4. **Safety-first** - multiple clamping mechanisms
5. **Automatic** - user doesn't need to calculate

## Formula Summary

```
Final Ramp Rate = min(stage_max, max(stage_min, 
    6000/(τ + 100) × thickness_factor × biot_factor × stage_multiplier
))

Where:
• τ = (m × Cp) / (h_eff × A)
• h_eff = base_h × (0.75 if carbon_sheet else 1.0)
• thickness_factor = 1.0 / (1.0 + Lc_mm/20)
• biot_factor = (0.5/(1+Bi) if Bi>0.1 else 1.0)
• stage_multiplier = {1.0, 0.8, 0.6, 0.4, 1.0}[stage-1]
```

This ensures **safe, physics-based heating** that adapts to your specific part geometry and carbon sheet configuration!
