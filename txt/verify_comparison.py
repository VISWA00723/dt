
import logging
from simulation_parameters import generate_physics_based_cycle, MATERIAL_PROPERTIES

# Setup basic logging
logging.basicConfig(level=logging.ERROR) # Only show errors to keep output clean

def test_scenario(name, thickness_mm):
    print(f"\n=== TESTING SCENARIO: {name} (Thickness: {thickness_mm}mm) ===")
    
    # Calculate volume/area to force a specific Tau
    # Tau ~ rho * Cp * Volume / (h * Area)
    # Effectivley, Tau scales with Thickness for plates.
    
    # Base 100x100 plate
    width = 100
    length = 100
    height = thickness_mm
    
    vol = length * width * height
    area = 2 * (length*width + length*height + width*height)
    
    context = {
        "geometry": [
            {
                "part": f"TestPart_{name}",
                "volume": vol,
                "surface_area": area,
                "extents": [length, width, height]
            }
        ],
        "materials": [
            {"alloy": "6061-T6"}
        ],
        "vacuum": {"pump_down_target": 5e-5},
        "filler_material": "AL718"
    }

    try:
        # Generate Cycle
        result_list = generate_physics_based_cycle(context=context)

        # Print Key Results
        current_tau = 0
        # We can't easily retrieve Tau internal variable, but we can see the results
        
        stages = result_list
        print(f"{'Stage':<10} | {'Temp':<8} | {'Hold Time (min)':<15}")
        print("-" * 40)
        for s in stages:
            if s['Stage'] in ["Stage 1", "Stage 2", "Stage 5", "Stage 6"]:
                 print(f"{s['Stage']:<10} | {s['Temperature']:<8} | {s['HoldTime']:<15}")

    except Exception as e:
        import traceback
        traceback.print_exc()

# 1. Run the "Standard/Heavy" case (Reference from User CSV)
test_scenario("HEAVY_PART", 15.0) 

# 2. Run a "Light/Thin" case to PROVE it's dynamic
# If hardcoded, numbers would be same. If dynamic, they should be ~half.
test_scenario("LIGHT_PART", 7.5)
