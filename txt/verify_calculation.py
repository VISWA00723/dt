
import logging
from simulation_parameters import generate_physics_based_cycle, MATERIAL_PROPERTIES

# Setup basic logging
logging.basicConfig(level=logging.INFO)

# Construct a Context that mimics the frontend payload
# We want a part that results in roughly Tau ~ 30 min.
# A heavy Al6061 block.
# 100mm x 100mm x 40mm
vol = 100 * 100 * 40 # 400,000 mm3
area = 2 * (100*100 + 100*40 + 100*40) # 2 * (10000 + 4000 + 4000) = 36,000 mm2
# L_c = 400000 / 36000 = 11.1 mm

context = {
    "geometry": [
        {
            "part": "TestPart_Heavy",
            "volume": vol,
            "surface_area": area,
            "extents": [100, 100, 40]
        }
    ],
    "materials": [
        {"alloy": "6061-T6"}
    ],
    "vacuum": {"pump_down_target": 5e-5},
    "filler_material": "AL718"
}

print("Running Simulation Verification...")
try:
    # Generate Cycle
    result_list = generate_physics_based_cycle(context=context)

    print("\n--- SIMULATION RESULTS ---")
    # result_list is a list of Dicts (stages)? No, implementation returns List[Dict] or Dict?
    # Docstring says: Returns exactly 4 stages... List[Dict[str, Any]]
    
    stages = result_list
    for s in stages:
        print(f"{s['Stage']}: Temp={s['Temperature']}C | Hold={s['HoldTime']} min | Purpose={s['Purpose']}")

except Exception as e:
    # Print full traceback for debugging
    import traceback
    traceback.print_exc()
