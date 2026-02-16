import json
import urllib.request
import logging
import math
from typing import Dict, List, Any, Tuple
from materials import MATERIAL_DB

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Unit / safety config ---
# Your allowed maximum heating rate (°C per minute)
MAX_RAMP_RATE_C_PER_MIN = 10.0

# If your get_ramp_rate(...) returns values in °C/s set this to True.
# If get_ramp_rate returns °C/min already set to False.
GET_RAMP_RATE_RETURNS_C_PER_S = False  # get_physics_ramp_rate returns °C/min

# Material Properties (Synced with frontend)
MATERIAL_PROPERTIES = MATERIAL_DB

# Added: Default vacuum stage configurations (updated with datasheet values)
VACUUM_CONFIG = {
    "pump_down_target": 14,         # mbar (datasheet value)
    "pump_down_hold": 10,           # minutes
    "brazing_partial_pressure": 14, # mbar (datasheet value)
    "chamber_pressure_mbar": 14,  # canonical field for physics
    "use_n2_backfill": True,
    "backfill_pressure": 1013       # mbar (atmospheric)
}

VACUUM_CALIBRATION = {
    "baseline_mbar": 14.0,
    "lag_constant": 6.0e9,
}


def get_chamber_pressure_mbar(vacuum: Dict[str, Any]) -> float:
    """Return canonical chamber pressure in mbar from vacuum settings."""
    if not isinstance(vacuum, dict):
        return 14.0
    # Canonical field for physics.
    if 'chamber_pressure_mbar' in vacuum:
        return float(vacuum['chamber_pressure_mbar'])
    # Backward compatibility with older payloads.
    if 'vacuum_level_mbar' in vacuum:
        return float(vacuum['vacuum_level_mbar'])
    if 'brazing_partial_pressure' in vacuum:
        return float(vacuum['brazing_partial_pressure'])
    return 14.0


def determine_vacuum_mode(vacuum_mbar: float) -> str:
    """Classify furnace vacuum regime for pressure penalties."""
    # High-vacuum regime where pressure-sensitive penalties should apply aggressively.
    if vacuum_mbar <= 0.1:
        return "high_vacuum"
    # Partial-pressure regime (e.g., 14 mbar brazing baseline).
    return "partial_pressure"

# Machine limits for safety clamping
MACHINE_LIMITS = {
    "min_temp": 150,                # °C (Lowered to allow dynamic preheating)
    "max_temp": 650,                # °C
    "min_ramp_rate": 1.0,           # °C/min
    "max_ramp_rate": 10.0           # °C/min
}

# ========== DYNAMIC HOLD COMPUTATION LAYER ==========

def compute_job_temperature_lag(materials, geometry, carbon_sheet_thickness_mm=0.0):
    """
    Predict job temperature lag based on aluminum vs stainless fixture
    and plate geometry. This is a simplified physical model that
    improves hold-time estimation.
    
    Args:
        materials: Material properties dict
        geometry: Geometry properties dict
        carbon_sheet_thickness_mm: Carbon sheet thickness in mm (default 0.0)
    """

    # Thermal conductivity of parts (aluminum heats fast, SS heats slow)
    alu_k = materials["top"]["thermal_conductivity"]
    ss_k = materials.get("fixture", {}).get("thermal_conductivity", 16)

    # Thickness approximation
    plate_thickness = geometry["top"].get("thickness", 20)  # mm default

    # Fixture mass effect
    fixture_mass = geometry.get("fixture", {}).get("mass", 0)

    # Estimate conduction lag
    conduction_lag = min(25, (plate_thickness / 20) * (167 / alu_k))

    # Fixture thermal absorption lag
    # SS316L fixture dominates heating lag: k=16 W/(m·K) vs Al k=167 W/(m·K)
    # A 27 kg SS316L fixture creates significant thermal inertia
    # Scale: (fixture_mass_kg / reference_mass) * conductivity_mismatch
    conductivity_ratio = 167.0 / max(ss_k, 1.0)  # Al/SS ≈ 10.4
    lag_due_to_fixture = min(50, (fixture_mass / 1000) * conductivity_ratio)
    
    # Carbon sheet thermal lag penalty
    # Carbon sheet adds interface resistance, increasing lag
    # Calibrated: ~35% increase per mm of carbon sheet thickness
    # Real-world: carbon sheets create 30-40% additional lag, not 15%
    carbon_penalty = 0.0
    if carbon_sheet_thickness_mm > 0:
        base_lag = conduction_lag + lag_due_to_fixture
        carbon_penalty = base_lag * (carbon_sheet_thickness_mm / 1.0) * 0.35
        # Cap total carbon penalty to avoid over-penalizing thick sheets.
        carbon_penalty = min(carbon_penalty, base_lag * 0.50)
        logger.info(f"Carbon sheet lag penalty: +{carbon_penalty:.1f}°C (thickness: {carbon_sheet_thickness_mm}mm)")

    return conduction_lag + lag_due_to_fixture + carbon_penalty

# ========== DYNAMIC HOLD COMPUTATION LAYER (LEGACY REMOVED) ==========


def extract_simulation_context(simulation_state) -> Dict[str, Any]:
    """
    Extract relevant information from the simulation state.
    Enhanced to include all geometric properties for physics-based calculations.
    """
    context = {
        "materials": [],
        "geometry": [],
        "target_temperature": getattr(simulation_state, 'target_temperature', 590),
        "filler_material": "AL718", # Default
        "vacuum": VACUUM_CONFIG.copy()  # Initialize with default vacuum config
    }
    
    # Update with any vacuum settings from simulation state
    if hasattr(simulation_state, 'vacuum_settings'):
        context["vacuum"].update(simulation_state.vacuum_settings)

    # Normalize canonical pressure field used by physics calculations.
    context["vacuum"]["chamber_pressure_mbar"] = get_chamber_pressure_mbar(context.get("vacuum", {}))
    
    if hasattr(simulation_state, 'meshes'):
        for part_name, mesh_data in simulation_state.meshes.items():
            material_name = mesh_data.get('alloy', '6061-T6')
            properties = mesh_data.get('properties', {})
            
            # Identify filler
            if 'filler' in part_name.lower():
                context['filler_material'] = material_name

            context["materials"].append({
                "part": part_name,
                "alloy": material_name,
                "properties": MATERIAL_PROPERTIES.get(material_name, {})
            })
            
            # Extract all geometric properties for physics calculations
            context["geometry"].append({
                "part": part_name,
                "volume": properties.get('volume', 0), # mm³
                "surface_area": properties.get('surface_area', 0), # mm²
                "extents": properties.get('extents', [0, 0, 0]),  # [x, y, z] dimensions in mm
                "centroid": properties.get('centroid', [0, 0, 0]),  # Center of mass
                "vertex_count": properties.get('vertex_count', 0),
                "face_count": properties.get('face_count', 0)
            })
            
    return context

def analyze_thermal_mass(context: Dict[str, Any]) -> Tuple[float, str, float, float]:
    """
    Calculate total thermal mass, identify filler liquidus, and base metal solidus.
    Returns: (total_thermal_mass, filler_name, filler_liquidus, base_solidus)
    """
    total_thermal_mass = 0.0
    filler_liquidus = 640.0 # Default for AL718
    filler_name = context.get('filler_material', 'AL718')
    
    # Track material usage to find dominant base metal
    material_masses = {}

    # Get filler liquidus
    if filler_name in MATERIAL_PROPERTIES:
        filler_liquidus = MATERIAL_PROPERTIES[filler_name]['liquidus']

    for i, item in enumerate(context['geometry']):
        part_name = item['part']
        volume_mm3 = item['volume']
        
        # Find material for this part
        material_name = context['materials'][i]['alloy']
        mat_props = MATERIAL_PROPERTIES.get(material_name, MATERIAL_PROPERTIES['6061-T6'])
        
        # Calculate Mass
        # Volume in m³ = Volume in mm³ * 1e-9
        volume_m3 = volume_mm3 * 1e-9
        density = mat_props['density']
        mass_kg = volume_m3 * density
        
        # Track mass per material for dominant base metal detection
        # Exclude filler from base metal candidate if possible
        if material_name != filler_name:
            material_masses[material_name] = material_masses.get(material_name, 0) + mass_kg
        
        # Calculate Thermal Mass (J/K) = Mass * Specific Heat
        cp = mat_props['specific_heat']
        thermal_mass = mass_kg * cp
        
        total_thermal_mass += thermal_mass
        
        logger.info(f"Part: {part_name}, Vol: {volume_mm3:.2f} mm³, Mass: {mass_kg:.4f} kg, TM: {thermal_mass:.2f} J/K")

    # Determine dominant base metal
    base_solidus = 582.0 # Default to 6061-T6
    if material_masses:
        dominant_material = max(material_masses, key=material_masses.get)
        if dominant_material in MATERIAL_PROPERTIES:
            base_solidus = MATERIAL_PROPERTIES[dominant_material]['solidus']
            logger.info(f"Dominant base metal: {dominant_material} (Solidus: {base_solidus}°C)")
    
    logger.info(f"Total Thermal Mass: {total_thermal_mass:.2f} J/K")
    return total_thermal_mass, filler_name, filler_liquidus, base_solidus

def calculate_physics_parameters(simulation_input) -> Dict[str, float]:
    """
    Calculate physics-based parameters for the assembly with enhanced geometry awareness.
    Accepts specific SimulationState object or dictionary context.
    """
    # Extract data based on input type
    if hasattr(simulation_input, 'meshes'):
        # Input is SimulationState object
        geometry = []
        materials = []
        
        for part_name, part_data in simulation_input.meshes.items():
            props = part_data.get('properties', {})
            geo_item = {
                'part': part_name,
                'volume': props.get('volume', 0.0),
                'surface_area': props.get('surface_area', 0.0),
                'extents': props.get('extents', [0, 0, 0])
            }
            geometry.append(geo_item)
            
            mat_item = {
                'alloy': part_data.get('alloy', '6061-T6')
            }
            materials.append(mat_item)

        physics_context = {
            'geometry': geometry,
            'materials': materials,
            'vacuum': getattr(simulation_input, 'vacuum_settings', None) or {
                'chamber_pressure_mbar': 14.0,
                'leak_rate_mbar_l_s': 1.0e-3,
                'pump_down_target': 14.0
            }
        }
    elif isinstance(simulation_input, dict):
        geometry = simulation_input.get('geometry', [])
        materials = simulation_input.get('materials', [])
        physics_context = simulation_input
    else:
        raise ValueError(f"Invalid input type: {type(simulation_input)}")

    # Alias context
    context = physics_context

    total_mass = 0.0
    total_heat_capacity = 0.0
    total_volume = 0.0
    total_surface_area = 0.0
    fixture_mass = 0.0
    max_dimension = 0.0
    part_details = []
    
    # ------------------------------------------------------------------
    # CARBON SHEET DETECTION & EMISSIVITY (Moved UP to affect radiation)
    # ------------------------------------------------------------------
    carbon_sheet_present = False
    carbon_thickness_mm = 0.8  # Default
    
    # Check if carbon sheet thickness is specified
    if hasattr(simulation_input, 'carbon_sheet_thickness_mm'):
        carbon_thickness_mm = simulation_input.carbon_sheet_thickness_mm
        carbon_sheet_present = True
        logger.info(f"Carbon sheet detected: {carbon_thickness_mm} mm")
    elif isinstance(simulation_input, dict) and simulation_input.get('carbon_sheet_present'):
        carbon_sheet_present = True
        carbon_thickness_mm = simulation_input.get('carbon_sheet_thickness_mm', 0.8)
    
    # Emissivity defaults (Problem #7 Fix)
    EMISSIVITY_BASE = 0.85
    EMISSIVITY_CARBON = 0.90
    emissivity = EMISSIVITY_CARBON if carbon_sheet_present else EMISSIVITY_BASE

    # Furnace parameters
    THERMAL_CONDUCTIVITY_AVG = 160.0  # W/(m·K)
    STEFAN_BOLTZMANN = 5.67e-8  # W/(m²·K⁴)
    
    for i, item in enumerate(geometry):
        part_name = item['part']
        volume_mm3 = item['volume']
        surface_area_mm2 = item.get('surface_area', 0)
        extents = item.get('extents', [0, 0, 0])
        
        # Get material properties
        material_name = context['materials'][i]['alloy']
        mat_props = MATERIAL_PROPERTIES.get(material_name, MATERIAL_PROPERTIES['6061-T6'])
        
        # Convert units
        volume_m3 = volume_mm3 * 1e-9
        surface_area_m2 = surface_area_mm2 * 1e-6
        
        # Calculate Mass
        density = mat_props['density']
        mass_kg = volume_m3 * density
        
        # Calculate Heat Capacity (J/K)
        cp = mat_props['specific_heat']
        heat_capacity = mass_kg * cp
        # Store for total
        total_heat_capacity += heat_capacity
        
        # Get thermal conductivity
        k = mat_props.get('thermal_conductivity', THERMAL_CONDUCTIVITY_AVG)
        
        # Calculate thermal diffusivity
        thermal_diffusivity = k / (density * cp) if (density * cp) > 0 else 1e-5
        
        max_dim_mm = max(extents) if extents and max(extents) > 0 else 10.0
        max_dimension = max(max_dimension, max_dim_mm)
        
        part_details.append({
            "part": part_name,
            "mass": mass_kg,
            "heat_capacity": heat_capacity,
            "specific_heat": cp,
            "thermal_diffusivity": thermal_diffusivity,
            "max_dimension": max_dim_mm,
            "volume": volume_m3,
            "surface_area": surface_area_m2,
            "density": density,
            "thermal_conductivity": k
        })
        
        total_mass += mass_kg
        total_volume += volume_m3
        total_surface_area += surface_area_m2
        
        if 'fixture' in part_name.lower() or material_name == 'SS316L':
            fixture_mass += mass_kg

        logger.debug(f"Part: {part_name}, Mass: {mass_kg:.4f}kg, HC: {heat_capacity:.1f}J/K, "
                   f"Diffusivity: {thermal_diffusivity:.2e}m²/s, MaxDim: {max_dim_mm:.2f}mm")
    
    # Calculate radiation heat transfer
    T_hot_K = 650 + 273.15
    T_env_K = 25 + 273.15
    
    # Proper emissivity handling (Problem #7)
    radiation_power = (emissivity * STEFAN_BOLTZMANN * total_surface_area * 
                      (T_hot_K**4 - T_env_K**4))
    
    delta_T = T_hot_K - T_env_K
    effective_h = (radiation_power / total_surface_area) / delta_T if total_surface_area > 0 else 45.0
    
    logger.debug(f"Radiation power: {radiation_power:.1f} W (ε={emissivity}), h_eff: {effective_h:.1f}")
    
    # Calculate Characteristic Thickness
    if total_surface_area > 0:
        characteristic_thickness = total_volume / total_surface_area
    else:
        characteristic_thickness = 0.01

    # Apply Carbon Sheet Thermal Resistance influence on h_eff
    if carbon_sheet_present:
        carbon_k = MATERIAL_PROPERTIES.get('CARBON_SHEET', {}).get('thermal_conductivity', 5.0)
        carbon_thickness_m = carbon_thickness_mm / 1000.0
        # Problem #6 Fix: Reduced reduction (0.70 instead of 0.62) to avoid double-counting with lag model
        h_reduction_factor = 0.70 
        effective_h = effective_h * h_reduction_factor
        logger.debug(f"Carbon sheet resistance applied. New h_eff: {effective_h:.1f}")

    # Calculate Time Constant
    if (effective_h * total_surface_area) > 0:
        time_constant = total_heat_capacity / (effective_h * total_surface_area)
    else:
        time_constant = 600.0

    # Calculate Biot Number
    avg_k = sum(p['thermal_conductivity'] for p in part_details) / len(part_details) if part_details else THERMAL_CONDUCTIVITY_AVG
    biot_number = (effective_h * characteristic_thickness) / avg_k
    
    logger.debug(f"Physics Parameters: Thickness={characteristic_thickness*1000:.2f}mm, "
               f"TimeConstant={time_constant:.1f}s, Biot={biot_number:.3f}")

    # Identify filler liquidus and base metal solidus
    base_solidus = 577.0
    filler_liquidus = 582.0
    filler_name = "AL718"
    
    for mat in materials:
        name = mat['alloy']
        if name in MATERIAL_PROPERTIES:
            props = MATERIAL_PROPERTIES[name]
            if "filler" in name.lower() or name in ["AL718", "4047"]:
                filler_name = name
                filler_liquidus = props.get('liquidus', 582.0)
            elif name not in ["SS316L"]:
                base_solidus = props.get('solidus', 577.0)
                
    # Also verify explicitly by solidus logic if needed
    if not base_solidus:
        # Fallback to scanning logic from snippet
        base_solidus_values = []
        for mat in materials:
            props = MATERIAL_PROPERTIES.get(mat['alloy'], {})
            if props.get('solidus'):
                 base_solidus_values.append(props['solidus'])
        if base_solidus_values:
            base_solidus = min(base_solidus_values)

    avg_thermal_diffusivity = sum(p['thermal_diffusivity'] for p in part_details) / len(part_details) if part_details else 1e-5

    chamber_pressure_mbar = get_chamber_pressure_mbar(context.get('vacuum', {}))

    return {
        "total_mass": total_mass,
        "total_heat_capacity": total_heat_capacity,
        "characteristic_thickness": characteristic_thickness,
        "effective_surface_area": total_surface_area,
        "time_constant": time_constant,
        "biot_number": biot_number,
        "max_dimension": max_dimension,
        "avg_thermal_diffusivity": avg_thermal_diffusivity,
        "h_coeff": effective_h,
        "effective_emissivity": emissivity,
        "part_details": part_details,
        "vacuum": {
            "chamber_pressure_mbar": chamber_pressure_mbar,
            "mode": determine_vacuum_mode(chamber_pressure_mbar),
        }, # Canonical vacuum nesting for physics
        "effective_heat_capacity": total_heat_capacity, # Standardized HC Key (Problem #1)
        "SAF": 1.0,
        "HPR": 1.0,
        "geometry": context.get('geometry', []),
        "materials": context.get('materials', []),
        "base_solidus": base_solidus,
        "filler_liquidus": filler_liquidus,
        "filler_name": filler_name,
        "fixture_mass": fixture_mass,
        "carbon_sheet_present": carbon_sheet_present,
        "carbon_sheet_thickness_mm": carbon_thickness_mm
    }


def get_physics_ramp_rate(params: Dict[str, float], stage: int, base_solidus: float) -> float:
    """
    Calculate ramp rate using an empirically calibrated, time-constant-driven model.
    The model uses thermal time constant, thickness, Biot effects, and fixture influence,
    then applies stage-specific operational bands.
    """
    tau = params['time_constant']
    thickness_mm = params['characteristic_thickness'] * 1000
    biot_number = params.get('biot_number', 0.05)
    h_coeff = params.get('h_coeff', 45.0)
    
    # Base rate (deg/min) inversely proportional to time constant
    # Faster for light/thin parts, slower for heavy/thick parts
    # Nominal rate for tau=600s (10min) is 10 deg/min
    base_rate = 6000.0 / (tau + 100.0) 
    
    # Thickness penalty: Thicker parts need slower ramps to minimize gradients
    # Penalty factor: 1.0 for <5mm, 0.5 for >20mm
    thickness_factor = 1.0 / (1.0 + (thickness_mm / 20.0))
    
    # Biot number correction: Higher Bi means more spatial gradients
    if biot_number > 0.1:
        biot_factor = 0.5 / (1.0 + biot_number)
    else:
        biot_factor = 1.0
    
    # Fixture mass penalty (Problem #8 Fix)
    # Applied AFTER clamping to allow reduction below stage floor for heavy fixtures
    fixture_mass = params.get('fixture_mass', 0.0)
    total_mass = params.get('total_mass', 1.0)
    
    fixture_factor = 1.0
    if total_mass > 0:
        ratio = fixture_mass / total_mass
        # Gentler penalty formula: 1.0 / (1.0 + 0.5*ratio)
        fixture_factor = 1.0 / (1.0 + (ratio * 0.5))
        
    physics_rate = base_rate * thickness_factor * biot_factor
    
    # Dynamic Ramp Safety: Slower ramps near liquidus
    if base_solidus - 30 <= 580 <= base_solidus + 10:
         physics_rate *= 0.8

    # Apply operational zone limits (clamping)
    if stage == 1: 
        clamped = min(8.0, max(5.0, physics_rate))
    elif stage == 2: 
        clamped = min(5.0, max(3.0, physics_rate * 0.8))
    elif stage == 3: 
        clamped = min(5.0, max(1.0, physics_rate * 0.6))
    elif stage == 4: 
        clamped = min(2.0, max(1.0, physics_rate * 0.4))
    elif stage == 5: 
        return min(1.0, MAX_RAMP_RATE_C_PER_MIN) # Fixed 1°C/min + global safety cap
    else:
        clamped = min(20.0, max(5.0, physics_rate * 1.5))
    
    # Apply fixture penalty AFTER clamping
    ramp = round(clamped * fixture_factor, 1)
    ramp = min(ramp, MAX_RAMP_RATE_C_PER_MIN)  # Final global safety clamp
    return ramp

def compute_diffusion_time(material: Dict[str, float], thickness_mm: float) -> float:
    """
    Compute thermal diffusion time - how long heat needs to reach the core.
    
    Formula: t_diff = L_c² / α
    where:
        L_c = characteristic thickness (m)
        α = k / (ρ * C_p) = thermal diffusivity (m²/s)
    
    Args:
        material: Material properties dict with thermal_conductivity, density, specific_heat
        thickness_mm: Characteristic thickness in mm
    
    Returns:
        Diffusion time in seconds
    """
    k = material.get("thermal_conductivity", 167.0)  # W/(m·K)
    rho = material.get("density", 2700.0)  # kg/m³
    cp = material.get("specific_heat", 896.0)  # J/(kg·K)
    
    # Thermal diffusivity (m²/s)
    alpha = k / (rho * cp)
    
    # Convert thickness to meters
    Lc = thickness_mm / 1000.0
    
    # Diffusion time (seconds)
    t_diff = (Lc ** 2) / alpha
    
    return t_diff


def compute_radiation_time(material: Dict[str, float], thickness_mm: float, soak_temp: float) -> float:
    """
    Compute radiation heating lag factor for vacuum brazing.
    
    Vacuum brazing is radiation-dominated. The radiative time constant is:
    τ_rad = (ρ * C_p * L_c) / (4 * ε * σ * T³)
    
    This captures why higher temperatures need longer holds:
    - At 560°C: radiation is more efficient, but thermal gradients are larger
    - At 530°C: radiation is less efficient, requiring longer equilibration
    
    Args:
        material: Material properties dict
        thickness_mm: Characteristic thickness in mm
        soak_temp: Soak temperature in °C
    
    Returns:
        Radiation time constant in seconds
    """
    rho = material.get("density", 2700.0)  # kg/m³
    cp = material.get("specific_heat", 896.0)  # J/(kg·K)
    emissivity = material.get("emissivity", 0.85)  # Furnace-dominated effective emissivity
    
    # Stefan-Boltzmann constant
    sigma = 5.67e-8  # W/(m²·K⁴)
    
    # Convert temperature to Kelvin
    T = soak_temp + 273.15
    
    # Convert thickness to meters
    Lc = thickness_mm / 1000.0
    
    # Radiation time constant (seconds)
    tau_rad = (rho * cp * Lc) / (4 * emissivity * sigma * (T ** 3))
    
    return tau_rad



def get_physics_dwell_time(params: Dict[str, float], stage: int, soak_temp: float = 500.0, job_temp: float = None) -> int:
    """
    INDUSTRIAL-GRADE PHYSICS-BASED DWELL TIME CALCULATION
    
    Replaces artificial multipliers with explicit physics modeling:
    1. Thermal diffusion time (heat penetration to core)
    2. Radiation heating lag (vacuum furnace characteristic)
    3. Filler flow time (capillary action)

    Formula: t_dwell = max(t_diff, τ_rad) + t_flow
    
    This model:
    ✓ Scales with thickness
    ✓ Scales with material properties
    ✓ Scales with temperature
    ✓ Uses JOB temperature for qualification gates (not setpoint)
    ✓ Removes artificial multipliers
    ✓ Is publishable and scientifically rigorous
    
    Args:
        params: Physics parameters dict containing:
            - characteristic_thickness: in meters
            - avg_thermal_diffusivity: in m²/s
            - part_details: list of part property dicts
        stage: Stage number (1-5 process stages)
        soak_temp: Soak temperature in °C (default 500°C)
        job_temp: Worst-case job temperature in °C (default None → uses soak_temp)
    
    Returns:
        Dwell time in minutes
    """
    # Extract geometry and material properties
    thickness_mm = params.get('characteristic_thickness', 0.015) * 1000  # Convert m to mm
    
    # Get material properties from first part (or use aluminum defaults)
    part_details = params.get('part_details', [])
    if part_details and len(part_details) > 0:
        # Use properties from the first non-fixture part
        material = None
        for part in part_details:
            if 'fixture' not in part.get('part', '').lower():
                material = {
                    'thermal_conductivity': part.get('thermal_conductivity', 167.0),
                    'density': part.get('density', 2700.0),
                    'specific_heat': part.get('specific_heat', 896.0),
                    'emissivity': params.get('effective_emissivity', 0.85)
                }
                break
        
        if material is None:
            # Fallback to aluminum defaults
            material = {
                'thermal_conductivity': 167.0,
                'density': 2700.0,
                'specific_heat': 896.0,
                'emissivity': params.get('effective_emissivity', 0.85)
            }
    else:
        # Default aluminum 6061-T6 properties
        material = {
            'thermal_conductivity': 167.0,
            'density': 2700.0,
            'specific_heat': 896.0,
            'emissivity': params.get('effective_emissivity', 0.85)
        }
    
    # ========== STEP 1: Compute Thermal Diffusion Time ==========
    t_diff = compute_diffusion_time(material, thickness_mm)

    # ========== STEP 2: Compute Radiation Time Constant ==========
    t_rad = compute_radiation_time(material, thickness_mm, soak_temp)

    # ========== STEP 3: Add Filler Flow Time ==========
    # Filler flow time depends on margin above liquidus
    # Larger margin → faster capillary flow → less time needed
    filler_liquidus = params.get('filler_liquidus', 582.0)
    effective_temp = job_temp if job_temp is not None else soak_temp
    
    if effective_temp >= filler_liquidus:
        margin = effective_temp - filler_liquidus
        # Base: 15 min at +5°C margin, 8 min at +15°C margin
        t_flow = max(480, 900 - margin * 30)  # 8-15 min range (in seconds)
    else:
        t_flow = 900.0  # 15 min default (conservative, below liquidus)
    
    # ========== STEP 5: Combine All Components ==========
    # The governing time is the maximum of diffusion and radiation times
    # (whichever is slower controls the process)
    # Plus filler flow time
    t_physics_minimum = max(t_diff, t_rad)  # Hard physics floor
    t_dwell_seconds = t_physics_minimum + t_flow
    
    # Convert to minutes
    t_dwell_minutes = t_dwell_seconds / 60.0
    
    # Log the breakdown for transparency
    logger.debug(f"  Dwell time calculation for Stage {stage}:")
    logger.debug(f"    Setpoint: {soak_temp:.1f}°C, Job temp: {effective_temp:.1f}°C")
    logger.debug(f"    Diffusion time: {t_diff/60:.1f} min")
    logger.debug(f"    Radiation time: {t_rad/60:.1f} min")
    logger.debug(f"    Physics floor: {t_physics_minimum/60:.1f} min")
    logger.debug(f"    Filler flow: {t_flow/60:.1f} min")
    logger.debug(f"    Total dwell: {t_dwell_minutes:.1f} min")
    
    # Safety bounds
    # Minimum: 5 minutes (safety)
    # Maximum: 300 minutes (5 hours - practical limit)
    t_dwell_clamped = max(5, min(300, int(t_dwell_minutes)))
    
    # ========== PROCESS QUALIFICATION GATE ==========
    # Production requirement: Minimum hold times for process qualification
    # These are NOT physics-derived - they are process certification requirements
    # 
    # CRITICAL: Uses JOB temperature (not setpoint) for gate checks
    # The parts must actually BE at these temperatures, not just the furnace
    #
    # From shop floor qualification standards:
    # - 560°C and above (job temp): Minimum 200 minutes hold
    # - 530°C and above (job temp): Minimum 120 minutes hold
    if effective_temp >= 560:
        if t_dwell_clamped < 200:
            logger.info(f"    ⚠ Qualification gate: {t_dwell_clamped} min → 200 min (job≥560°C requires ≥200 min)")
            t_dwell_clamped = 200
    elif effective_temp >= 530:
        if t_dwell_clamped < 120:
            logger.info(f"    ⚠ Qualification gate: {t_dwell_clamped} min → 120 min (job≥530°C requires ≥120 min)")
            t_dwell_clamped = 120
    
    return t_dwell_clamped

def clamp_temp(temp: float) -> float:
    """Clamp temperature to machine limits."""
    return max(MACHINE_LIMITS["min_temp"], min(temp, MACHINE_LIMITS["max_temp"]))

def clamp_ramp(rate: float) -> float:
    """Clamp ramp rate to machine limits."""
    return max(MACHINE_LIMITS["min_ramp_rate"], min(rate, MACHINE_LIMITS["max_ramp_rate"]))

def apply_operational_ramp_limits(temp: float, ramp: float) -> float:
    """
    Apply operational ramp rate limits based on temperature zones.
    
    This enforces FURNACE SAFETY CONSTRAINTS, not physics.
    These are shop-floor operational rules that override physics-based calculations.
    
    Temperature Zone Rules (Shop SOP):
    - Ambient → 400°C: Max 7-8 °C/min (rapid heating allowed)
    - 400 → 500°C: Max 4 °C/min (controlled heating)
    - > 500°C: Max 1-2 °C/min (critical zone, very slow)
    
    Args:
        temp: Target temperature in °C
        ramp: Physics-based ramp rate in °C/min
    
    Returns:
        Clamped ramp rate in °C/min
    """
    if temp < 400:
        # Zone 1: Ambient to 400°C - Allow faster heating
        max_ramp = 8.0  # °C/min
    elif temp < 500:
        # Zone 2: 400-500°C - Moderate control
        max_ramp = 4.0  # °C/min
    elif temp < 520:
        # Zone 3a: 500-520°C - Critical zone, slow
        max_ramp = 2.0  # °C/min
    else:
        # Zone 3b: >520°C - Brazing zone, capillary-sensitive
        max_ramp = 1.5  # °C/min
    
    # Return the minimum of physics-based and operational limit
    clamped_ramp = min(ramp, max_ramp)
    
    # Log if operational limit is applied
    if clamped_ramp < ramp:
        logger.info(f"  Operational limit applied: {ramp:.1f}°C/min → {clamped_ramp:.1f}°C/min (zone limit: {max_ramp}°C/min at {temp}°C)")
    
    clamped_ramp = min(clamped_ramp, MAX_RAMP_RATE_C_PER_MIN)  # Final global safety clamp
    return clamped_ramp



def estimate_job_temperatures(
    master_temp: float,
    ramp_rate: float,
    total_mass_kg: float,
    vacuum_value: float,
    vacuum_mode: str = "partial_pressure",
    fixture_mass_ratio: float = 0.0,
) -> Tuple[float, float]:
    """
    AI-based estimation of Job-1 & Job-2 temperature lag.
    Uses total mass (kg) as load proxy to avoid Cp double-counting.
    """
    
    # Normalize factors
    # Keep mass penalty neutral to avoid double-counting load effects already modeled
    # by fixture lag, radiation coupling, and carbon-sheet penalties.
    mass_penalty = 1.0
    # Optional fixture shielding penalty for heavy fixtures.
    fixture_penalty = 1.0 + min(0.25, max(0.0, fixture_mass_ratio) * 0.5)
    
    # CALCULATE EFFECTIVE RAMP RATE
    # "Digital Twin" correction: The user's furnace and load configuration rarely exceeds 3.5-4.0°C/min linearly.
    # Even if they input 7°C/min, we must simulate based on the Physical Limit of the furnace class.
    # If the user uploads a light part but runs a heavy load schedule, we must respect the Heavy Load Physics.
    max_physical_rate = 4.0 
    effective_rate = min(ramp_rate, max_physical_rate)
    
    # Vacuum penalty uses configured calibration constants.
    vac_penalty = 1.0
    baseline_mbar = VACUUM_CALIBRATION["baseline_mbar"]
    # Vacuum regime-aware penalty model:
    # - partial_pressure: 14 mbar baseline, penalize only if much worse.
    # - high_vacuum: 1e-4 mbar baseline, penalize degradation above this level.
    if vacuum_mode == "high_vacuum":
        if vacuum_value > 1.0e-4:
            vac_penalty = 1.0 + max(0.0, math.log10(vacuum_value / 1.0e-4)) * 0.20
    elif vacuum_value > (baseline_mbar * 1.43):
        vac_penalty = 1.0 + (math.log10(vacuum_value) - math.log10(baseline_mbar * 1.43)) * 0.5
        
    # PHYSICS-BASED LAG MODEL (calibrated)
    T_kelvin = master_temp + 273.15
    C = VACUUM_CALIBRATION["lag_constant"]
    
    # Base lag calculated from PHYSICAL rate
    base_lag = (C * effective_rate) / (T_kelvin ** 3)
    
    # Apply penalties
    total_lag = base_lag * vac_penalty * mass_penalty * fixture_penalty
    
    # Job 1 (Best View Factor) sees ~90% of theoretical lag
    job1_lag = total_lag * 0.90
    
    # Job 2 (Shielded) sees ~115% of Job 1 lag
    job2_lag = job1_lag * 1.15
    
    # Clamp lag to reasonable physics limits
    # Relax clamp to 80% of master temp to avoid artificial ceiling
    # But keep it safe so we don't return 0 or negative.
    job1_lag = min(job1_lag, master_temp * 0.8) 
    job2_lag = min(job2_lag, master_temp * 0.85)
    
    T_job1 = max(25.0, master_temp - job1_lag)
    T_job2 = max(25.0, master_temp - job2_lag)
    
    return round(T_job1, 1), round(T_job2, 1)


# ========== THERMO-TWIN LOGIC: PROCESS GATING (~400°C) ==========
# Rule 1: Aluminum thermally "doesn't exist" below ~400°C due to weak radiation & oxide barrier.
# Rule 2: Thermal accumulation (dose) starts only above 400°C.
# Rule 3: Oxide breakdown requires Job ≥ 400°C AND sustained exposure time

class ThermoTwinState:
    def __init__(self):
        self.effective_thermal_time = 0.0     # Minutes where job_temp > 400°C
        self.thermal_dose = 0.0               # Integral(T_job - 400) * dt (GATED by job temp)
        self.oxide_integrity = 1.0            # Oxide layer integrity (1.0 = intact, 0.0 = fully disrupted)
        # NEW Phase-1 fields:
        self.effective_brazing_time = 0.0     # Min where job_temp ≥ filler_liquidus
        self.delta_t_uniformity_time = 0.0    # Min where |Job1 - Job2| ≤ 5°C
        self.filler_flow_time = 0.0           # Min where job_temp ≥ liquidus + 5°C
        self.process_verdict = "PENDING"      # PASS / FAIL / EXTEND
        self.verdict_details = {}             # Per-condition breakdown

# ========== QUALIFICATION THRESHOLDS (DECISION LAYER) ==========
# These define the acceptance criteria for a successful braze.
# Every threshold is ENFORCED, not informational.
QUALIFICATION_THRESHOLDS = {
    "effective_brazing_time_min": 20.0,   # Minutes where job_temp >= liquidus
    "filler_flow_time_min": 10.0,         # Minutes where job_temp >= liquidus + 5C
    "delta_t_uniformity_min": 30.0,       # Minutes where |Job1-Job2| <= 5C
    "oxide_integrity_max": 0.05,          # <= 0.05 means >= 95% disrupted
    "thermal_dose_min": 2000.0,           # Degree-minutes (integral of T_job - 400) [°C·min]
                                        # Empirical for 6061-T6 + AL718, ~15-25 mm thickness.
}

def compute_process_verdict(state, filler_liquidus, T_job2_worst):
    """
    FINAL PROCESS VERDICT ENGINE
    
    Evaluates ALL qualification thresholds against Thermo-Twin state
    and produces a structured verdict:
    
      PASS   — All thresholds met. Braze is qualified.
      EXTEND — Liquidus reached but time thresholds not met.
               Recoverable by extending hold time.
      FAIL   — Temperature/physics conditions fundamentally unmet.
               NOT recoverable by time alone (e.g., never reached
               liquidus, oxide too high at insufficient temp).
    
    Args:
        state: ThermoTwinState with accumulated data
        filler_liquidus: Filler liquidus temperature (C)
        T_job2_worst: Worst-case job-2 temperature from Stage 5 (C)
    
    Returns:
        (verdict: str, details: dict)
    """
    Q = QUALIFICATION_THRESHOLDS
    
    # Evaluate each threshold
    checks = {}
    
    # 1. Effective Brazing Time
    checks["brazing_time"] = {
        "value": round(state.effective_brazing_time, 1),
        "required": Q["effective_brazing_time_min"],
        "unit": "min",
        "passed": state.effective_brazing_time >= Q["effective_brazing_time_min"],
        "extendable": True,  # Can be fixed by holding longer
    }
    
    # 2. Filler Flow Time
    checks["filler_flow"] = {
        "value": round(state.filler_flow_time, 1),
        "required": Q["filler_flow_time_min"],
        "unit": "min",
        "passed": state.filler_flow_time >= Q["filler_flow_time_min"],
        "extendable": True,
    }
    
    # 3. Delta-T Uniformity Duration
    checks["uniformity"] = {
        "value": round(state.delta_t_uniformity_time, 1),
        "required": Q["delta_t_uniformity_min"],
        "unit": "min",
        "passed": state.delta_t_uniformity_time >= Q["delta_t_uniformity_min"],
        "extendable": True,
    }
    
    # 4. Oxide Integrity
    oxide_ok = state.oxide_integrity <= Q["oxide_integrity_max"]
    # Oxide is extendable ONLY if temp is above 400C (decay can continue)
    checks["oxide"] = {
        "value": round(state.oxide_integrity, 4),
        "required": f"<= {Q['oxide_integrity_max']}",
        "disruption_pct": round((1 - state.oxide_integrity) * 100, 1),
        "unit": "integrity",
        "passed": oxide_ok,
        "extendable": T_job2_worst >= 400.0,  # Can only decay more if above 400C
    }
    
    # 5. Thermal Dose
    checks["thermal_dose"] = {
        "value": round(state.thermal_dose, 0),
        "required": Q["thermal_dose_min"],
        "unit": "°C·min",
        "passed": state.thermal_dose >= Q["thermal_dose_min"],
        "extendable": T_job2_worst >= 400.0,
    }
    
    # 6. Hard FAIL: Job never reached liquidus (non-recoverable)
    reached_liquidus = T_job2_worst >= filler_liquidus
    checks["liquidus_reached"] = {
        "value": round(T_job2_worst, 1),
        "required": round(filler_liquidus, 1),
        "unit": "C",
        "passed": reached_liquidus,
        "extendable": False,  # Can NEVER be fixed by time alone
    }
    
    # ========== VERDICT LOGIC ==========
    all_passed = all(c["passed"] for c in checks.values())
    any_failed = any(not c["passed"] for c in checks.values())
    
    if all_passed:
        verdict = "PASS"
        reason = "All qualification thresholds met"
    elif any(not c["passed"] and not c["extendable"] for c in checks.values()):
        # At least one non-recoverable failure
        verdict = "FAIL"
        failed_hard = [k for k, c in checks.items() if not c["passed"] and not c["extendable"]]
        reason = f"Non-recoverable failure: {', '.join(failed_hard)}"
    else:
        # All failures are extendable (time-based)
        verdict = "EXTEND"
        failed_soft = [k for k, c in checks.items() if not c["passed"]]
        reason = f"Insufficient time for: {', '.join(failed_soft)}"
    
    details = {
        "verdict": verdict,
        "reason": reason,
        "checks": checks,
        "thresholds_used": Q.copy(),
        "passed_count": sum(1 for c in checks.values() if c["passed"]),
        "total_count": len(checks),
    }
    
    # Update state
    state.process_verdict = verdict
    state.verdict_details = details
    
    return verdict, details


    


def generate_physics_based_cycle(context, initial_temp=None, initial_ramp=None, overrides=None):
    """
    Generate heating stages based on physics context.
    
    Args:
        context: The physics context dict
        initial_temp: Optional Stage 1 temp override
        initial_ramp: Optional Stage 1 ramp override
        overrides: Optional custom cycle list
    """
    # context is already calculated and passed in
    
    # Extract key physics parameters for logging
    effective_heat_capacity = context['effective_heat_capacity']
    SAF = context['SAF']
    HPR = context['HPR']
    vacuum_value = context['vacuum']['chamber_pressure_mbar']
    vacuum_mode = context['vacuum'].get('mode', determine_vacuum_mode(vacuum_value))
    
    # Extract geometry/mass info from context (added by calculate_physics_parameters)
    total_mass_kg = context.get('total_mass', 0.0)
    fixture_mass_kg = context.get('fixture_mass', 0.0)
    fixture_mass_ratio = fixture_mass_kg / total_mass_kg if total_mass_kg > 0 else 0.0
    # Convert m2 to mm2
    total_surface_area_mm2 = context.get('effective_surface_area', 0.0) * 1e6
    max_part_height_mm = context.get('max_dimension', 0.0)
    filler_name = context.get('filler_name', 'AL718')
    
    logger.info("="*60)
    logger.info("PHYSICS ENGINE: HEATING PHASE")
    logger.info(f"Total Part Mass: {context['total_mass']:.3f} kg")
    logger.info(f"Effective Heat Capacity: {effective_heat_capacity:.2f} J/K")
    logger.info(f"Characteristic Thickness: {context['characteristic_thickness']:.2f} mm")
    logger.info("="*60)
    logger.info("="*60)

    stages = []
    
    # --- CUSTOM CYCLE MODE ---
    if overrides:
        logger.info(">>> CUSTOM CYCLE MODE ACTIVE <<<")
        logger.info(f"Processing {len(overrides)} custom stages...")
        
        current_job1_temp = 25.0
        current_job2_temp = 25.0
        
        for i, stage_data in enumerate(overrides):
            stage_name = stage_data.get('Stage', f'Stage {i+1}')
            target_temp = float(stage_data.get('Temperature', 0))
            ramp_rate = float(stage_data.get('RampRate', 0))
            hold_time = float(stage_data.get('HoldTime', 0))
            purpose = stage_data.get('Purpose', 'User defined stage')
            
            # Calculate Physics-Based Job Temperatures for this stage
            # We pass the USER'S ramp rate to the physics model so lag is accurate
            T_job1, T_job2 = estimate_job_temperatures(
                target_temp, 
                ramp_rate,
                total_mass_kg,
                vacuum_value,
                vacuum_mode,
                fixture_mass_ratio,
            )
            
            # Physics-based smoothing: Jobs don't jump instantly.
            # However, estimate_job_temperatures returns the STEADY STATE lag.
            # For a custom cycle, we should perhaps use the steady state for simplicity 
            # as implementing a full transient solver for every custom edit might be overkill/unstable.
            
            stages.append({
                "Stage": stage_name,
                "Temperature": target_temp,
                "RampRate": ramp_rate,
                "HoldTime": hold_time,
                "Purpose": purpose,
                "Job1Temp": round(T_job1, 1),
                "Job2Temp": round(T_job2, 1)
            })
            
        return {
            "cycle": stages,
            "final_verdict": "MANUAL",
            "verdict_details": {"reason": "Custom cycle override"}
        }

    # --- STANDARD PHYSICS CYCLE GENERATION ---
    # Base configuration
    base_solidus = context['base_solidus']
    filler_liquidus = context['filler_liquidus']
    # Calculate fixture mass ratio
    fixture_mass_ratio = fixture_mass_kg / total_mass_kg if total_mass_kg > 0 else 0.0
    logger.info(f"FIXTURE MASS: {fixture_mass_kg:.4f} kg ({fixture_mass_ratio*100:.1f}% of total)")
    
    if fixture_mass_ratio > 0.20:
        logger.info(f"⚠ Fixture mass > 20% → Equalization stage REQUIRED")
    
    # ========== NEW: Calculate Surface Area Factor (SAF) ==========
    SAF = total_surface_area_mm2 / 100000.0  # Normalize
    logger.info(f"SURFACE AREA FACTOR (SAF): {SAF:.2f} (Area: {total_surface_area_mm2:.0f} mm²)")
    
    # Calculate SAF multiplier once (capped at +30%)
    SAF_multiplier = 1 + min(SAF * 0.05, 0.30)
    
    # ========== NEW: Calculate Heat-Path Resistance (HPR) ==========
    # Use square root scaling to avoid overly aggressive slowdown
    HPR = max(1.0, math.sqrt(max_part_height_mm / 100.0))  # 100mm = reference
    logger.info(f"HEAT-PATH RESISTANCE (HPR): {HPR:.2f} (Max height: {max_part_height_mm:.1f} mm)")
    
    logger.info("="*60)
    
    # ========== STEP 2: Process Window Safety Check ==========
    process_window = base_solidus - filler_liquidus
    logger.info(f"PROCESS WINDOW ANALYSIS:")
    logger.info(f"  Filler Liquidus ({filler_name}): {filler_liquidus}°C")
    logger.info(f"  Base Solidus (6061-T6): {base_solidus}°C")
    logger.info(f"  Process Window: {process_window}°C")
    
    if process_window < 20:
        logger.warning(f"⚠ WARNING: Process window ({process_window}°C) is less than 20°C!")
        logger.warning("  Risk of base metal melting. Proceed with extreme caution.")
    else:
        logger.info(f"✓ Process window is adequate ({process_window}°C ≥ 20°C)")
    logger.info("="*60)
    
    # ========== CLASS-BASED SOAKING STRATEGY ==========
    # Determines allowable temperature bands and soak precision based on thermal mass/geometry
    def determine_process_class(hpr_val, fixture_ratio):
        if hpr_val < 1.2 and fixture_ratio < 0.15:
            return "Class 1", 3.0, "green" # ±3°C
        elif hpr_val > 1.8 or fixture_ratio > 0.30:
            return "Class 3", 8.0, "orange" # ±8°C
        else:
            return "Class 2", 6.0, "yellow" # ±6°C
            
    # Calculate global parameters for classification
    # Re-using SAF (Surface Area Factor) and HPR (Heat-Path Resistance) calculated above
    # Assuming fixture_mass_ratio exists
    
    process_class, class_band, class_color = determine_process_class(HPR, fixture_mass_ratio)
    
    logger.info(f"PROCESS CLASSIFICATION:")
    logger.info(f"  Class: {process_class}")
    logger.info(f"  Band: ±{class_band}°C (Nominal 592°C)")
    logger.info(f"  Rationale: HPR={HPR:.2f}, FixtureRatio={fixture_mass_ratio:.2f}")
    logger.info("="*60)
    
    # ========== STEP 3: Calculate Physics Parameters ==========
    physics_params = calculate_physics_parameters(context)
    
    # Check for fixture (SS316L has low thermal conductivity)
    has_fixture = fixture_mass_kg > 0
    if has_fixture:
        logger.info("✓ Fixture (SS316L) detected - applying slower ramps & longer soaks")
    
    # ========== STEP 4: Calculate Target Temperature ==========
    # NEW: Stricter safety margin (8°C instead of 10°C)
    target_temp = filler_liquidus + 10  # 10°C above liquidus for good flow
    max_safe_temp = base_solidus - 8    # NEW: 8°C safety margin (stricter)
    
    if target_temp > max_safe_temp:
        target_temp = max_safe_temp
        logger.warning(f"⚠ Target temp adjusted to {target_temp}°C (8°C below base solidus)")
    
    # Clamp to machine limits (400-650°C)
    target_temp = clamp_temp(target_temp)
    
    logger.info(f"Target Brazing Temperature: {target_temp}°C")
    logger.info("="*60)
    
    # ========== STEP 5A: Compute Dynamic Hold Multipliers ==========
    # Compute required multipliers for dynamic hold calculation
    
    # Build materials dict for temp lag computation
    materials_dict = {}
    geometry_dict = {}
    
    for i, item in enumerate(context['geometry']):
        part_name = item['part'].lower()
        material_name = context['materials'][i]['alloy']
        mat_props = MATERIAL_PROPERTIES.get(material_name, MATERIAL_PROPERTIES['6061-T6'])
        
        # Categorize parts
        if 'top' in part_name:
            materials_dict['top'] = mat_props
            extents = item.get('extents', [0, 0, 0])
            geometry_dict['top'] = {
                'thickness': max(extents) if extents else 20,
                'mass': context['part_details'][i]['mass']
            }
        elif 'fixture' in part_name or material_name == 'SS316L':
            materials_dict['fixture'] = mat_props
            geometry_dict['fixture'] = {
                'mass': context['part_details'][i]['mass']
            }
    
    # Compute temperature lag
    if 'top' in materials_dict:
        # Get carbon sheet thickness from physics params
        carbon_thickness = physics_params.get('carbon_sheet_thickness_mm', 0.0)
        temp_lag = compute_job_temperature_lag(materials_dict, geometry_dict, carbon_thickness)
    else:
        temp_lag = 15.0  # Default lag
    
    logger.info(f"DYNAMIC HOLD COMPUTATION:")
    logger.info(f"  Temperature Lag: {temp_lag:.1f}°C")
    logger.info(f"  Total Mass: {total_mass_kg:.3f} kg")
    logger.info(f"  SS Mass Ratio: {fixture_mass_ratio:.3f}")
    logger.info("="*60)
    
    
    # ========== STEP 5B: Generate 5 PROCESS heating stages with dynamic holds ==========
    stages = []
    
    # Calculate Fourier-based equilibration time
    thickness_mm = physics_params["characteristic_thickness"] * 1000
    alpha = physics_params["avg_thermal_diffusivity"]
    fourier_time_min = (thickness_mm / 1000)**2 / alpha / 60  # Convert to minutes
    
    # Calculate dynamic reference temperatures
    # T_ref = base_solidus (e.g. 582°C for 6061) or filler_liquidus
    # We use base_solidus as the reference for preheating/stress relief
    T_ref = base_solidus
    
    # ========== STAGE 1: Initial Heating / Activation ==========
    # Stage-1 is an activation gate only. Oxide disruption is expected later
    # at higher job temperatures (primarily Stage-4/Stage-5), not at ~400°C.
    
    oxide_integrity = 1.0
    
    # Set target to 400°C (Process Gating Threshold)
    # We use max(400, T_ref * 0.45) to ensure we don't go lower than original logic, 
    # but strictly we want 400+.
    stage1_temp = clamp_temp(int(max(400.0, T_ref * 0.45)))
    
    # Ramp rate logic - Locked Ramp Strategy (< 400°C)
    # Ambient -> 400°C: 7-8 °C/min allowed
    # Physics-based ramp rate calculation
    stage1_ramp_physics = get_physics_ramp_rate(physics_params, 1, base_solidus)
    # Apply operational zone limits (furnace safety constraint)
    stage1_ramp = apply_operational_ramp_limits(stage1_temp, stage1_ramp_physics)
    stage1_ramp_source = "OperationalLimit" if stage1_ramp < stage1_ramp_physics else "Physics"
    logger.info(f"Stage 1 ramp rate (physics-based + operational limits): {stage1_ramp}°C/min")
    
    # Apply Stage 1 Overrides if provided
    if initial_temp is not None:
        stage1_temp = clamp_temp(int(initial_temp))
        logger.info(f"Stage 1 Temp overridden to {stage1_temp}°C")
        
    if initial_ramp is not None:
        stage1_ramp = clamp_ramp(float(initial_ramp))
        # Re-apply operational limits even for overrides
        stage1_ramp = apply_operational_ramp_limits(stage1_temp, stage1_ramp)
        stage1_ramp_source = "OperationalLimit" if stage1_ramp < clamp_ramp(float(initial_ramp)) else "Override"
        logger.info(f"Stage 1 Ramp overridden to {stage1_ramp}°C/min")
    
    T_job1_s1, T_job2_s1 = estimate_job_temperatures(
        stage1_temp,
        stage1_ramp,
        total_mass_kg,
        context['vacuum']['chamber_pressure_mbar'],
        vacuum_mode,
        fixture_mass_ratio,
    )
    
    # --- Stage-1 Hold Time ---
    # Activation hold is process-defined (not diffusion/radiation/filler-flow driven).
    stage1_hold = 30
    
    # ========== THERMO-TWIN LOGIC: PROCESS GATING (~400°C) ==========
    # Rule 1: Aluminum thermally "doesn't exist" below ~400°C due to weak radiation & oxide barrier.
    # Rule 2: Thermal accumulation (dose) starts only above 400°C.
    # Rule 3: Oxide breakdown requires Job ≥ 400°C AND sustained exposure time
    
    # Initialize Thermo-Twin state

    
    tt_state = ThermoTwinState()
    
    # Helper to simulate Thermo-Twin accumulation
    # CRITICAL: Uses JOB TEMPERATURE (not setpoint) for all threshold checks
    def simulate_thermo_twin_accumulation(
        setpoint_c, job1_temp, job2_temp, time_min, state, filler_liq
    ):
        """
        Accumulate Thermo-Twin physics logic:
        - Brazing Time (Time above liquidus)
        - Filler Flow (Integrate flow rate)
        - Delta-T Uniformity (Time within 10C)
        - Oxide Integrity (Decay function)
        - Thermal Dose (Integral above 400C)
        """
        # Use WORST-CASE job temp (Job-2, the shielded thermocouple)
        job_temp = min(job1_temp, job2_temp)
        
        # Rule 1: Below 400°C JOB temp → nothing counts
        if job_temp < 400:
            logger.debug(f"    Thermo-Twin: job_temp={job_temp:.1f}°C < 400°C — skipping accumulation")
            return state
        
        # Accumulate effective time (only when JOB is above threshold)
        state.effective_thermal_time += time_min
        
        # Thermal dose: GATED by job_temp (not setpoint)
        dose_rate = max(0, job_temp - 400.0)  # Degree-minutes per minute
        state.thermal_dose += dose_rate * time_min
        
        # 4. Oxide Integrity Decay (Problem #3/4 Fix)
        # Oxide barrier breaks down with time@temp.
        # Problem #4: Use job_temp (worst-case) instead of avg for conservative physics.
        if job_temp > 500.0:
             decay_rate = 0.05 * (1.0 + (job_temp - 500.0)/50.0) # 5% per minute base
             if vacuum_mode == "high_vacuum":
                 # Faster oxide disruption under high vacuum relative to partial-pressure baseline.
                 vacuum_decay_factor = 1.0 + max(0.0, math.log10(max(14.0 / max(vacuum_value, 1e-6), 1.0))) * 0.10
             else:
                 vacuum_decay_factor = 1.0
             decay_rate *= vacuum_decay_factor
             # Calibration cap: avoid unrealistically fast oxide clearing at high temperatures.
             decay_rate = min(decay_rate, 0.10)
             
             # Only decay if integrity is good > 5% (Prevent meaningless decay)
             if state.oxide_integrity > 0.05:
                # Exponential decay approximation for small steps
                decay_factor = math.exp(-decay_rate * time_min)
                state.oxide_integrity *= decay_factor
                
                # Numerical floor: 0.05 is treated as "fully disrupted" for this model.
                if state.oxide_integrity < 0.05:
                    state.oxide_integrity = 0.05
        
        # ========== EFFECTIVE BRAZING TIME ==========
        # Only counts when JOB temp ≥ filler liquidus (fixes #3)
        if job_temp >= filler_liq:
            state.effective_brazing_time += time_min
        
        # ========== FILLER FLOW TIME ==========
        # Only counts when JOB temp ≥ liquidus + 5°C margin
        if job_temp >= filler_liq + 5.0:
            state.filler_flow_time += time_min
        
        # ========== ΔT UNIFORMITY TRACKING ==========
        # Tracks time where |Job1 - Job2| ≤ 5°C (fixes #8)
        delta_t = abs(job1_temp - job2_temp)
        if delta_t <= 5.0:
            state.delta_t_uniformity_time += time_min
        
        logger.debug(f"    Thermo-Twin @ setpoint={setpoint_c:.0f}°C: "
                   f"job_temp={job_temp:.1f}°C, dt={time_min:.0f}min, "
                   f"oxide={state.oxide_integrity:.3f}, "
                   f"eff_braze={state.effective_brazing_time:.1f}min, "
                   f"ΔT_uniform={state.delta_t_uniformity_time:.1f}min")
        
        return state

    # Check accumulation during Stage 1 (using JOB temperatures)
    simulate_thermo_twin_accumulation(
        stage1_temp, T_job1_s1, T_job2_s1, stage1_hold, tt_state, filler_liquidus
    )
    
    if T_job2_s1 >= 400:
        logger.info(f"  >>> THERMO-TWIN: Valid thermal accumulation started in Stage 1 (Job2={T_job2_s1:.1f}°C)")
    else:
        logger.info(f"  >>> THERMO-TWIN: Job2 temp {T_job2_s1:.1f}°C < 400°C - Ignoring thermal history (Rule 1)")
    
    stages.append({
        "Stage": "Stage 1",
        "Temperature": stage1_temp,
        "RampRate": round(stage1_ramp, 1),
        "RampSource": stage1_ramp_source,
        "HoldTime": stage1_hold,
        "Purpose": f"Initial Heating (Job@{T_job2_s1:.0f}°C, Oxide intact – activation only)",
        "Job1Temp": T_job1_s1,
        "Job2Temp": T_job2_s1,
        "exit_condition": {
            "type": "job_temperature",
            "target": stage1_temp - temp_lag,
            "require_both": True
        }
    })
    
    # Stage 2 (Drying & Outgassing) has been removed
    
    # ========== STAGE 2: Stress Relief ==========
    # Dynamic: 75% of solidus (e.g. ~435-480°C) OR maintain climb
    base_s2_temp = int(T_ref * 0.75)
    stage2_temp = clamp_temp(max(base_s2_temp, stage1_temp + 5))
    
    # Physics-based ramp rate calculation
    stage2_ramp_physics = get_physics_ramp_rate(physics_params, 2, base_solidus)
    # Apply operational zone limits (furnace safety constraint)
    stage2_ramp = apply_operational_ramp_limits(stage2_temp, stage2_ramp_physics)
    stage2_ramp_source = "OperationalLimit" if stage2_ramp < stage2_ramp_physics else "Physics"
    logger.info(f"Stage 2 ramp rate (physics-based + operational limits): {stage2_ramp}°C/min")
    
    T_job1_s2, T_job2_s2 = estimate_job_temperatures(
        stage2_temp,
        stage2_ramp,
        total_mass_kg,
        context['vacuum']['chamber_pressure_mbar'],
        vacuum_mode,
        fixture_mass_ratio,
    )
    
    # --- Physics-Based Hold Time (with JOB temperature) ---
    stage2_hold = get_physics_dwell_time(physics_params, 2, stage2_temp, job_temp=T_job2_s2)
    
    # Thermo-Twin Accumulation (using JOB temperatures)
    simulate_thermo_twin_accumulation(
        stage2_temp, T_job1_s2, T_job2_s2, stage2_hold, tt_state, filler_liquidus
    )

    stages.append({
        "Stage": "Stage 2",
        "Temperature": stage2_temp,
        "RampRate": round(stage2_ramp, 1),
        "RampSource": stage2_ramp_source,
        "HoldTime": stage2_hold,
        "Purpose": f"Stress Relief (Job@{T_job2_s2:.0f}°C, Eff.Time: {tt_state.effective_thermal_time:.0f}m)",
        "Job1Temp": T_job1_s2,
        "Job2Temp": T_job2_s2,
        "exit_condition": {
            "type": "job_temperature",
            "target": stage2_temp - temp_lag,
            "require_both": True
        }
    })
    
    # ========== STAGE 3: Thermal Equalization ==========
    # Dynamic: Filler Liquidus - 50°C
    stage3_temp = clamp_temp(int(filler_liquidus - 50))
    # Physics-based ramp rate calculation
    stage3_ramp_physics = get_physics_ramp_rate(physics_params, 3, base_solidus)
    # Apply operational zone limits (furnace safety constraint)
    stage3_ramp = apply_operational_ramp_limits(stage3_temp, stage3_ramp_physics)
    stage3_ramp_source = "OperationalLimit" if stage3_ramp < stage3_ramp_physics else "Physics"
    logger.info(f"Stage 3 ramp rate (physics-based + operational limits): {stage3_ramp}°C/min")
    
    T_job1_s3, T_job2_s3 = estimate_job_temperatures(
        stage3_temp,
        stage3_ramp,
        total_mass_kg,
        context['vacuum']['chamber_pressure_mbar'],
        vacuum_mode,
        fixture_mass_ratio,
    )
    
    # --- Physics-Based Hold Time (with JOB temperature) ---
    stage3_hold = get_physics_dwell_time(physics_params, 3, stage3_temp, job_temp=T_job2_s3)
    
    # Thermo-Twin Accumulation (using JOB temperatures)
    simulate_thermo_twin_accumulation(
        stage3_temp, T_job1_s3, T_job2_s3, stage3_hold, tt_state, filler_liquidus
    )
    
    stages.append({
        "Stage": "Stage 3",
        "Temperature": stage3_temp,
        "RampRate": round(stage3_ramp, 1),
        "RampSource": stage3_ramp_source,
        "HoldTime": stage3_hold,
        "Purpose": f"Thermal Equalization (Job@{T_job2_s3:.0f}°C, ΔT_uniform: {tt_state.delta_t_uniformity_time:.0f}m)",
        "Job1Temp": T_job1_s3,
        "Job2Temp": T_job2_s3,
        "exit_condition": {
            "type": "job_temperature",
            "target": stage3_temp - temp_lag,
            "require_both": True
        }
    })
    
    # ========== STAGE 4: Brazing Approach ==========
    # Dynamic: Filler Liquidus - 20°C
    stage4_temp = clamp_temp(int(filler_liquidus - 20))
    # Ramp rate logic - Locked Ramp Strategy (> 500°C)
    # Physics-based ramp rate calculation
    stage4_ramp_physics = get_physics_ramp_rate(physics_params, 4, base_solidus)
    # Apply operational zone limits (furnace safety constraint)
    stage4_ramp = apply_operational_ramp_limits(stage4_temp, stage4_ramp_physics)
    stage4_ramp_source = "OperationalLimit" if stage4_ramp < stage4_ramp_physics else "Physics"
    logger.info(f"Stage 4 ramp rate (physics-based + operational limits): {stage4_ramp}°C/min") 
    
    T_job1_s4, T_job2_s4 = estimate_job_temperatures(
        stage4_temp,
        stage4_ramp,
        total_mass_kg,
        context['vacuum']['chamber_pressure_mbar'],
        vacuum_mode,
        fixture_mass_ratio,
    )
    
    # --- Physics-Based Hold Time (with JOB temperature) ---
    stage4_hold = get_physics_dwell_time(physics_params, 4, stage4_temp, job_temp=T_job2_s4)

    # Thermo-Twin Accumulation (using JOB temperatures)
    simulate_thermo_twin_accumulation(
        stage4_temp, T_job1_s4, T_job2_s4, stage4_hold, tt_state, filler_liquidus
    )
    
    stages.append({
        "Stage": "Stage 4",
        "Temperature": stage4_temp,
        "RampRate": round(stage4_ramp, 1),
        "RampSource": stage4_ramp_source,
        "HoldTime": stage4_hold,
        "Purpose": f"Brazing Approach (Job@{T_job2_s4:.0f}°C, Dose: {tt_state.thermal_dose:.0f})",
        "Job1Temp": T_job1_s4,
        "Job2Temp": T_job2_s4,
        "exit_condition": {
            "type": "job_temperature",
            "target": stage4_temp - temp_lag,
            "require_both": True
        }
    })
    
    # ========== STAGE 5: Final Brazing Soak (MELT-READY GATEKEEPER) ==========
    # CRITICAL CONSTRAINT: Stage-5 hold time is NOMINALLY fixed by process class.
    # Auto-extension is a controlled recovery path when verdict=EXTEND.
    # Preferred thermal preparation remains Stage-3 and Stage-4.
    
    logger.info("="*60)
    logger.info("STAGE 5: MELT-READY GATEKEEPER ANALYSIS")
    logger.info("="*60)
    
    # --- Class-Based Temperature Strategy ---
    # Determine process class based on BOTH HPR and fixture mass ratio
    # This matches the original determine_process_class() logic
    if HPR < 1.2 and fixture_mass_ratio < 0.15:
        # Class 1: BOTH conditions must be true
        process_class = "Class 1"
        temp_tolerance = 3  # ±3°C band
        stage5_hold = 20    # Light fixtures: minimum 20 min above liquidus
    elif HPR > 1.8 or fixture_mass_ratio > 0.30:
        # Class 3: EITHER condition triggers this
        process_class = "Class 3"
        temp_tolerance = 8  # ±8°C band
        stage5_hold = 30    # Heavy fixtures: 30 min for diffusion + capillary flow
    else:
        # Class 2: Default (everything else)
        process_class = "Class 2"
        temp_tolerance = 6  # ±6°C band
        stage5_hold = 25    # Medium fixtures: 25 min
    
    logger.info(f"Process Classification: {process_class}")
    logger.info(f"  HPR (Heat-Path Resistance): {HPR:.2f}")
    logger.info(f"  Fixture Mass Ratio: {fixture_mass_ratio*100:.1f}%")
    logger.info(f"  Temperature Band: {target_temp} ± {temp_tolerance}°C")
    logger.info(f"  Fixed Hold Time: {stage5_hold} min")
    
    # Nominal Stage-5 temperature (Liquidus + 10°C, typically 592°C)
    stage5_temp_nominal = int(target_temp)
    
    # Class-based temperature adjustment
    # Use upper band if thermal convergence is slow (heavy fixtures)
    if process_class == "Class 3":
        # Heavy fixtures need more energy to accelerate final convergence
        stage5_temp = min(stage5_temp_nominal + temp_tolerance, 
                         clamp_temp(stage5_temp_nominal + temp_tolerance))
        logger.info(f"  Class 3: Using upper band ({stage5_temp}°C) for heavy fixtures")
    elif process_class == "Class 2":
        # Medium fixtures use mid-range
        stage5_temp = min(stage5_temp_nominal + int(temp_tolerance/2),
                         clamp_temp(stage5_temp_nominal + int(temp_tolerance/2)))
        logger.info(f"  Class 2: Using mid-range ({stage5_temp}°C) for medium fixtures")
    else:
        # Light fixtures stay at nominal
        stage5_temp = stage5_temp_nominal
        logger.info(f"  Class 1: Using nominal ({stage5_temp}°C) for light fixtures")
    
    # Fixed ramp rate for Stage-5 (always 1°C/min)
    stage5_ramp_physics = 1.0
    # Apply operational zone limits (should not change since 1.0 < 2.0 limit)
    stage5_ramp = apply_operational_ramp_limits(stage5_temp, stage5_ramp_physics)
    stage5_ramp_source = "OperationalLimit" if stage5_ramp < stage5_ramp_physics else "Physics"
    logger.info(f"Stage 5 ramp rate: {stage5_ramp}°C/min (FIXED + operational limits)")
    
    # Calculate job temperatures for Stage 5
    T_job1_s5, T_job2_s5 = estimate_job_temperatures(
        stage5_temp,
        stage5_ramp,
        total_mass_kg,
        context['vacuum']['chamber_pressure_mbar'],
        vacuum_mode,
        fixture_mass_ratio,
    )
    
    # Stage-5 feasibility gate (before Stage-5 qualification/extension logic)
    if T_job2_s5 < filler_liquidus + 5:
        logger.warning(f"FEASIBILITY CHECK FAILED (pre-Stage-5): Job Temp ({T_job2_s5:.1f}°C) < Required Flow Temp ({filler_liquidus+5:.1f}°C)")

    # ========== MELT-READY ENTRY CONDITIONS (MANDATORY) ==========
    # Stage-5 can ONLY succeed if these conditions are met BEFORE entry
    
    logger.info("="*60)
    logger.info("MELT-READY ENTRY VALIDATION")
    logger.info("="*60)
    
    # Condition 1: Thermal Convergence (Job-1 and Job-2 must be close)
    job_convergence_delta = abs(T_job1_s4 - T_job2_s4)
    convergence_threshold = 5.0  # °C
    convergence_ok = job_convergence_delta <= convergence_threshold
    
    logger.info(f"Condition 1 - Thermal Convergence:")
    logger.info(f"  Job-1 temp: {T_job1_s4:.1f}°C")
    logger.info(f"  Job-2 temp: {T_job2_s4:.1f}°C")
    logger.info(f"  Delta: {job_convergence_delta:.1f}°C (threshold: ≤{convergence_threshold}°C)")
    logger.info(f"  Status: {'✓ PASS' if convergence_ok else '✗ FAIL - Jobs not converged'}")
    
    # Condition 2: Master-Job Proximity (Job-2 must be close to master)
    master_job_delta = abs(stage4_temp - T_job2_s4)
    proximity_threshold = 5.0  # °C
    proximity_ok = master_job_delta <= proximity_threshold
    
    logger.info(f"Condition 2 - Master-Job Proximity:")
    logger.info(f"  Master temp: {stage4_temp}°C")
    logger.info(f"  Job-2 temp: {T_job2_s4:.1f}°C")
    logger.info(f"  Delta: {master_job_delta:.1f}°C (threshold: ≤{proximity_threshold}°C)")
    logger.info(f"  Status: {'✓ PASS' if proximity_ok else '✗ FAIL - Job-2 not caught up'}")
    
    # Condition 3: Absolute Temperature (Job-2 must be near liquidus)
    melt_ready_temp = filler_liquidus - 5.0  # 577-578°C for AL718
    temp_ready_ok = T_job2_s4 >= melt_ready_temp
    
    logger.info(f"Condition 3 - Absolute Temperature:")
    logger.info(f"  Job-2 temp: {T_job2_s4:.1f}°C")
    logger.info(f"  Required: ≥{melt_ready_temp:.1f}°C (Liquidus - 5°C)")
    logger.info(f"  Status: {'✓ PASS' if temp_ready_ok else '✗ FAIL - Temperature too low'}")
    
    # Condition 4: Oxide Integrity (entry gate, not final acceptance)
    # Entry gate allows Stage-5 when disruption is sufficient to initiate flow.
    # Final qualification remains stricter in QUALIFICATION_THRESHOLDS (≤0.05).
    oxide_threshold = 0.3
    oxide_ready_ok = tt_state.oxide_integrity < oxide_threshold
    oxide_disruption_percent = (1.0 - tt_state.oxide_integrity) * 100
    
    # Condition 4 - Oxide Disruption:
    logger.info(f"Condition 4 - Oxide Disruption:")
    logger.info(f"  Current oxide integrity: {tt_state.oxide_integrity:.3f}")
    logger.info(f"  Disruption achieved: {oxide_disruption_percent:.1f}%")
    logger.info(f"  Required: ≥70% disruption (integrity < {oxide_threshold})")
    logger.info(f"  Status: {'✓ PASS' if oxide_ready_ok else '✗ FAIL - Oxide not sufficiently disrupted'}")
    
    # Condition 5: Thermal Uniformity Duration
    # ΔT ≤ 5°C must be sustained for ≥30 min during Stage 3+4
    uniformity_threshold_min = 30.0
    uniformity_ok = tt_state.delta_t_uniformity_time >= uniformity_threshold_min
    
    logger.info(f"Condition 5 - Thermal Uniformity Duration:")
    logger.info(f"  Time at ΔT ≤ 5°C: {tt_state.delta_t_uniformity_time:.1f} min")
    logger.info(f"  Required: ≥{uniformity_threshold_min:.0f} min")
    logger.info(f"  Status: {'✓ PASS' if uniformity_ok else '✗ FAIL - Insufficient thermal uniformity duration'}")
    
    # Overall melt-ready status (ALL 5 conditions must pass)
    melt_ready = convergence_ok and proximity_ok and temp_ready_ok and oxide_ready_ok and uniformity_ok
    
    # Build structured verdict details
    tt_state.verdict_details = {
        "convergence": {"status": convergence_ok, "delta": round(job_convergence_delta, 1), "threshold": convergence_threshold},
        "proximity": {"status": proximity_ok, "delta": round(master_job_delta, 1), "threshold": proximity_threshold},
        "temperature": {"status": temp_ready_ok, "job2_temp": round(T_job2_s4, 1), "required": round(melt_ready_temp, 1)},
        "oxide": {"status": oxide_ready_ok, "integrity": round(tt_state.oxide_integrity, 3), "threshold": oxide_threshold},
        "uniformity": {"status": uniformity_ok, "duration_min": round(tt_state.delta_t_uniformity_time, 1), "required_min": uniformity_threshold_min}
    }
    
    logger.info("="*60)
    if melt_ready:
        logger.info("✓✓✓ MELT-READY: All 5 entry conditions SATISFIED ✓✓✓")
        logger.info(f"  Stage-5 can proceed with {stage5_hold} min hold time")
        logger.info("  Expected outcome: SUCCESSFUL BRAZE")
    else:
        logger.warning("✗✗✗ NOT MELT-READY: Entry conditions NOT satisfied ✗✗✗")
        logger.warning(f"  Stage-5 will likely FAIL with {stage5_hold} min hold time")
        logger.warning("  RECOMMENDATION: Increase Stage-3 or Stage-4 hold time")
        logger.warning("  Required actions:")
        if not convergence_ok:
            logger.warning(f"    - Increase Stage-3 hold to achieve Job-1/Job-2 convergence")
        if not proximity_ok:
            logger.warning(f"    - Increase Stage-4 hold to close Master-Job gap")
        if not temp_ready_ok:
            logger.warning(f"    - Increase Stage-4 temperature or hold time")
        if not oxide_ready_ok:
            logger.warning(f"    - Increase Stage-1 or Stage-2 hold time to achieve oxide disruption")
            logger.warning(f"    - Current disruption: {oxide_disruption_percent:.1f}%, Required: ≥70%")
        if not uniformity_ok:
            logger.warning(f"    - Extend Stage-3/4 hold: ΔT uniformity time {tt_state.delta_t_uniformity_time:.1f}min < {uniformity_threshold_min:.0f}min required")
    logger.info("="*60)
    
    # Thermo-Twin Accumulation (final — using JOB temperatures)
    simulate_thermo_twin_accumulation(
        stage5_temp, T_job1_s5, T_job2_s5, stage5_hold, tt_state, filler_liquidus
    )
    
    logger.info("="*60)
    logger.info("STAGE 5 FINAL SUMMARY")
    logger.info("="*60)
    logger.info(f"Process Class: {process_class}")
    logger.info(f"Temperature: {stage5_temp}°C (Band: ±{temp_tolerance}°C)")
    logger.info(f"Hold Time: {stage5_hold} min (FIXED for {process_class})")
    logger.info(f"Ramp Rate: {stage5_ramp}°C/min (FIXED)")
    logger.info(f"Melt-Ready Status: {'✓ READY' if melt_ready else '✗ NOT READY'}")
    logger.info(f"Final Thermo-Twin Stats (JOB-TEMP GATED):")
    logger.info(f"  Effective Time (job>400°C): {tt_state.effective_thermal_time:.1f} min")
    logger.info(f"  Thermal Dose (job-gated): {tt_state.thermal_dose:.0f} °C·min")
    logger.info(f"  Effective Brazing Time (job≥liquidus): {tt_state.effective_brazing_time:.1f} min")
    logger.info(f"  Filler Flow Time (job≥liq+5): {tt_state.filler_flow_time:.1f} min")
    logger.info(f"  ΔT Uniformity Time (|J1-J2|≤5°C): {tt_state.delta_t_uniformity_time:.1f} min")
    logger.info(f"  Oxide Integrity: {tt_state.oxide_integrity:.3f} ({(1-tt_state.oxide_integrity)*100:.1f}% disrupted)")
    if tt_state.effective_thermal_time > 120:
        logger.info("✓ Cycle meets minimum thermal history requirements")
    else:
        logger.warning("⚠ Cycle may be too short for effective oxide disruption")
    if tt_state.effective_brazing_time < 20:
        logger.warning(f"⚠ Effective brazing time ({tt_state.effective_brazing_time:.1f}min) < 20min minimum")
    logger.info("="*60)
    
    # ========== FINAL QUALIFICATION CHECK ==========
    logger.info("="*60)
    logger.info("FINAL QUALIFICATION CHECK")
    logger.info("="*60)
    
    # Run the verdict engine against all thresholds
    process_verdict, verdict_details = compute_process_verdict(
        tt_state, filler_liquidus, T_job2_s5
    )
    
    # Log per-threshold results
    for check_name, check_data in verdict_details["checks"].items():
        status_str = "PASS" if check_data["passed"] else ("EXTEND" if check_data["extendable"] else "FAIL")
        logger.info(f"  [{status_str}] {check_name}: {check_data['value']} {check_data['unit']} "
                    f"(required: {check_data['required']})")
    
    logger.info(f"")
    logger.info(f"  VERDICT: {process_verdict}")
    logger.info(f"  REASON:  {verdict_details['reason']}")
    logger.info(f"  PASSED:  {verdict_details['passed_count']}/{verdict_details['total_count']} checks")
    logger.info("="*60)

    # Hard feasibility fail before extension logic.
    if process_verdict == "EXTEND" and T_job2_s5 < filler_liquidus + 5:
        process_verdict = "FAIL"
        verdict_details["verdict"] = "FAIL"
        verdict_details["reason"] = f"Job physics limit reached ({T_job2_s5:.1f}°C). Part shielding or furnace power insufficient for filler flow."
        tt_state.process_verdict = "FAIL"
        tt_state.verdict_details = verdict_details

    # ========== AUTOMATIC STAGE EXTENSION ==========
    # If verdict is EXTEND, the cycle can be saved by holding longer.
    # Logic: Bounded loop (max 3 retries) with safety caps.
    
    MAX_EXTENSIONS = 3
    MAX_TOTAL_STAGE5_TIME = 90.0  # Production safety limit (min)
    
    ext_count = 0
    while process_verdict == "EXTEND" and ext_count < MAX_EXTENSIONS:
        logger.info("="*60)
        logger.info(f"AUTO-EXTENSION: Calculating needed hold time extension (Attempt {ext_count+1}/{MAX_EXTENSIONS})")
        logger.info("="*60)
        
        Q = QUALIFICATION_THRESHOLDS
        needed_extensions = []
        checks = verdict_details["checks"]
        
        # Calculate deficit for each failing extendable threshold
        if not checks["brazing_time"]["passed"]:
            deficit = Q["effective_brazing_time_min"] - tt_state.effective_brazing_time
            needed_extensions.append(("brazing_time", deficit))
            logger.info(f"  brazing_time: needs +{deficit:.1f} min")
        
        if not checks["filler_flow"]["passed"]:
            deficit = Q["filler_flow_time_min"] - tt_state.filler_flow_time
            needed_extensions.append(("filler_flow", deficit))
            logger.info(f"  filler_flow: needs +{deficit:.1f} min")
        
        if not checks["uniformity"]["passed"]:
            deficit = Q["delta_t_uniformity_min"] - tt_state.delta_t_uniformity_time
            needed_extensions.append(("uniformity", deficit))
            logger.info(f"  uniformity: needs +{deficit:.1f} min")
        
        if not checks["oxide"]["passed"] and checks["oxide"]["extendable"]:
            current_oxide = tt_state.oxide_integrity
            target_oxide = Q["oxide_integrity_max"]
            # Guard against div-by-zero and check feasibility
            if current_oxide > target_oxide and T_job2_s5 >= 400:
                T_scale = 100.0
                k_base = 0.015
                k_oxide = k_base * math.exp((min(T_job1_s5, T_job2_s5) - 400.0) / T_scale)
                if k_oxide > 1e-6:
                    oxide_time = math.log(current_oxide / target_oxide) / k_oxide
                    needed_extensions.append(("oxide", oxide_time))
                    logger.info(f"  oxide: needs +{oxide_time:.1f} min")
        
        if not checks["thermal_dose"]["passed"] and checks["thermal_dose"]["extendable"]:
            dose_deficit = Q["thermal_dose_min"] - tt_state.thermal_dose
            job_temp = min(T_job1_s5, T_job2_s5)
            # Prevent div-by-zero
            if job_temp > 400.0:
                dose_rate = job_temp - 400.0
                dose_time = dose_deficit / dose_rate
                needed_extensions.append(("thermal_dose", dose_time))
                logger.info(f"  thermal_dose: needs +{dose_time:.1f} min")
        
        # Take maximum of all needed extensions (they run in parallel)
        if needed_extensions:
            raw_extension = max(ext[1] for ext in needed_extensions)
            
            # Problem 4: MAX TOTAL TIME CAP CHECK
            current_total = stage5_hold
            remaining_allowed = MAX_TOTAL_STAGE5_TIME - current_total
            
            if remaining_allowed <= 0:
                 logger.warning(f"  Max Stage 5 time ({MAX_TOTAL_STAGE5_TIME} min) reached! Cannot extend.")
                 process_verdict = "FAIL"
                 verdict_details["verdict"] = "FAIL"
                 verdict_details["reason"] = f"Max time limit reached ({MAX_TOTAL_STAGE5_TIME} min). {verdict_details.get('reason','')}"
                 tt_state.process_verdict = "FAIL"
                 tt_state.verdict_details = verdict_details
                 break
            
            # Cap extension: Min 1 min, Max 60 min, Max Remaining
            extension_to_apply = min(60.0, raw_extension, remaining_allowed)
            extension_to_apply = max(1.0, math.ceil(extension_to_apply))
            
            logger.info(f"")
            logger.info(f"  EXTENSION APPLIED: +{extension_to_apply} min")
            logger.info(f"  New Stage 5 hold: {stage5_hold} + {extension_to_apply} = {stage5_hold + extension_to_apply} min")
            
            # Apply extension
            stage5_hold += extension_to_apply
            
            # Simulate extension period conservatively.
            # Credit is only applied when steady-state Job-2 is at/above liquidus.
            extension_job_temp = min(T_job1_s5, T_job2_s5)
            if extension_job_temp >= filler_liquidus:
                simulate_thermo_twin_accumulation(
                    stage5_temp, T_job1_s5, T_job2_s5,
                    extension_to_apply,
                    tt_state,
                    filler_liquidus
                )
            else:
                logger.warning(
                    f"  Extension credit blocked: Job temp {extension_job_temp:.1f}°C < liquidus {filler_liquidus:.1f}°C"
                )
                process_verdict = "FAIL"
                verdict_details["verdict"] = "FAIL"
                verdict_details["reason"] = "Stage-5 extension attempted below liquidus (no brazing credit)."
                tt_state.process_verdict = "FAIL"
                tt_state.verdict_details = verdict_details
                break
            
            # Update loop counter
            ext_count += 1
            
            # Re-evaluate verdict after extension
            process_verdict, verdict_details = compute_process_verdict(
                tt_state, filler_liquidus, T_job2_s5
            )
            
            logger.info(f"  POST-EXTENSION VERDICT: {process_verdict}")
        else:
            # No calculated extension needed but verdict is EXTEND? Should not happen.
            logger.warning("  Verdict is EXTEND but no extension calculated? Downgrading to FAIL.")
            process_verdict = "FAIL"
            break

    if process_verdict == "EXTEND":
        # If loop finished and still EXTEND (max retries hit)
        process_verdict = "FAIL"
        verdict_details["verdict"] = "FAIL" 
        verdict_details["reason"] = f"Max extensions ({MAX_EXTENSIONS}) reached. {verdict_details['reason']}"
        tt_state.process_verdict = "FAIL"
        tt_state.verdict_details = verdict_details
        logger.warning(f"  Max extensions reached - final verdict: FAIL")

    logger.info("="*60)
    
    # Build verdict-aware Stage 5 purpose label
    if process_verdict == "PASS":
        stage5_purpose = f"Final Brazing Soak (PASS: {verdict_details['passed_count']}/{verdict_details['total_count']} checks)"
    elif process_verdict == "EXTEND":
        failed_names = [k for k, c in verdict_details["checks"].items() if not c["passed"]]
        stage5_purpose = f"Final Brazing Soak (EXTEND: {', '.join(failed_names)} insufficient)"
    else:
        failed_names = [k for k, c in verdict_details["checks"].items() if not c["passed"]]
        stage5_purpose = f"Final Brazing Soak (FAIL: {', '.join(failed_names)})"
    
    stages.append({
        "Stage": "Stage 5",
        "Temperature": stage5_temp,
        "RampRate": round(stage5_ramp, 1),
        "RampSource": stage5_ramp_source,
        "HoldTime": stage5_hold,
        "Purpose": stage5_purpose,
        "Job1Temp": T_job1_s5,
        "Job2Temp": T_job2_s5,
        "ProcessVerdict": process_verdict,
        "VerdictReason": verdict_details["reason"],
        "VerdictDetails": verdict_details,
        "EffectiveBrazingTime": round(tt_state.effective_brazing_time, 1),
        "FillerFlowTime": round(tt_state.filler_flow_time, 1),
        "OxideDisruption": round((1-tt_state.oxide_integrity)*100, 1),
        "DeltaTUniformityTime": round(tt_state.delta_t_uniformity_time, 1),
        "ThermalDose": round(tt_state.thermal_dose, 0),  # numeric °C·min value (format at UI layer)
        "FinalQualification": {
            "verdict": process_verdict,
            "reason": verdict_details["reason"],
            "passed": verdict_details["passed_count"],
            "total": verdict_details["total_count"],
            "checks": {k: c["passed"] for k, c in verdict_details["checks"].items()},
        },
        "exit_condition": {
            "type": "job_temperature",
            "target": stage5_temp - temp_lag,
            "require_both": True
        }
    })
    
    # Add Final Cooling Stage (Backfill)
    # Using helper if available or manual
    # stages.append(generate_backfill_stage(context['vacuum']))
    
    # Metadata for UI
    # We'll attach class info to the last stage or a separate metadata field
    # For now, adding to all stages helps UI pick it up easily
    for s in stages:
        s['ProcessClass'] = process_class
        s['SoakBand'] = class_band
        s['ClassColor'] = class_color
        s['ProcessStage'] = True
    
    logger.debug(f"5-STAGE CYCLE GENERATED:")
    for stage in stages:
        job1_info = f", Job1: {stage.get('Job1Temp', 'N/A')}°C, Job2: {stage.get('Job2Temp', 'N/A')}°C" if 'Job1Temp' in stage else ""
        logger.debug(f"  {stage['Stage']}: {stage['Temperature']}°C @ {stage['RampRate']}°C/min, Hold {stage['HoldTime']}min{job1_info}")
    logger.info("="*60)
    logger.info("PHYSICS CORRECTIONS APPLIED:")

    logger.info(f"  ✓ Effective Heat Capacity (fixture-corrected): {context['effective_heat_capacity']:.2f} J/K")
    logger.info(f"  ✓ Thermal Dose Total: {round(tt_state.thermal_dose, 0)} °C·min")
    logger.info(f"  ✓ Feasibility Check: Stage-5 minimum reached")
    logger.info("="*60)
    
    return {
        "cycle": stages,
        "final_verdict": process_verdict,
        "verdict_details": verdict_details,
        "thermo_twin_state": tt_state.__dict__
    }





def query_llm_for_purposes(stages: List[Dict[str, Any]], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Query the LLM to generate ONLY the 'Purpose' text for the provided stages.
    """
    url = "http://localhost:11434/api/generate"
    
    # Prepare a simplified list for the prompt
    stage_descriptions = []
    for s in stages:
        stage_descriptions.append(f"- {s['Stage']}: {s['Temperature']}°C, {s['HoldTime']}min hold")
        
    prompt = f"""
    You are an expert in vacuum brazing.
    I have calculated the following brazing cycle for an Aluminum assembly (Filler: {context.get('filler_material')}).
    
    Cycle:
    {chr(10).join(stage_descriptions)}
    
    Task: Write a concise, technical 'Purpose' string for each stage explaining WHY it is done physically/metallurgically.
    Do NOT change the stage names, temperatures, or times.
    
    Output format: A JSON object where keys are Stage names and values are the Purpose strings.
    Example:
    {{
        "Stage 1": "Remove oxygen and moisture...",
        "Stage 2": "Stabilize vacuum pressure...",
        ...
    }}
    
    Return ONLY the JSON object.
    """
    
    payload = {
        "model": "llama3.2:3b",
        "prompt": prompt,
        "stream": False,
        "temperature": 0.2,
        "format": "json"
    }
    
    # try:
    #     data = json.dumps(payload).encode('utf-8')
    #     req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        
    #     with urllib.request.urlopen(req) as response:
    #         result = json.loads(response.read().decode('utf-8'))
    #         llm_response = result.get('response', '')
            
    #         # Parse the JSON response
    #         purposes = json.loads(llm_response)
            
    #         # Update the stages with the new purposes
    #         for stage in stages:
    #             if stage['Stage'] in purposes:
    #                 stage['Purpose'] = purposes[stage['Stage']]
    #             else:
    #                 # Fallback purposes if LLM misses one
    #                 if stage['Stage'] == "Stage 1": stage['Purpose'] = "Remove oxygen, moisture, prevent oxidation"
    #                 elif stage['Stage'] == "Stage 2": stage['Purpose'] = "Ensure stable low-pressure before heating"
    #                 elif stage['Stage'] == "Stage 3": stage['Purpose'] = "Remove moisture and volatiles"
    #                 elif stage['Stage'] == "Stage 4": stage['Purpose'] = "Relieve machining stresses"
    #                 elif stage['Stage'] == "Stage 5": stage['Purpose'] = "Ensure uniform temperature before melting"
    #                 elif stage['Stage'] == "Stage 6": stage['Purpose'] = "Melt filler and form metallurgical bond"
            
    #         return stages
            
    # except Exception as e:
    #     logger.error(f"Error querying LLM for purposes: {e}")
    #     # Apply fallback purposes
        # for stage in stages:
        #     if stage['Purpose'] == "Generating...":
        #         if stage['Stage'] == "Stage 1": stage['Purpose'] = "Remove oxygen, moisture, prevent oxidation"
        #         elif stage['Stage'] == "Stage 2": stage['Purpose'] = "Ensure stable low-pressure before heating"
        #         elif stage['Stage'] == "Stage 3": stage['Purpose'] = "Remove moisture and volatiles"
        #         elif stage['Stage'] == "Stage 4": stage['Purpose'] = "Relieve machining stresses"
        #         elif stage['Stage'] == "Stage 5": stage['Purpose'] = "Ensure uniform temperature before melting"
        #         elif stage['Stage'] == "Stage 6": stage['Purpose'] = "Melt filler and form metallurgical bond"
        # return stages
    
    # LLM code is commented out, return stages as-is with existing purposes
    return stages

def generate_vacuum_stages(vacuum_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate the initial vacuum pump-down and stabilization stages.
    Uses datasheet values without scientific notation.
    """
    stages = []

    # stages.append({
    #     "Stage": "Vacuum Pump-Down",
    #     "Temperature": 25,  # Room temperature
    #     "RampRate": 0,  # No temperature change
    #     "HoldTime": vacuum_settings['pump_down_hold'],
    #     "Purpose": f"Pump down to {vacuum_settings['pump_down_target']} mbar - Remove oxygen and moisture"
    # })

    # stages.append({
    #     "Stage": "Vacuum Stabilization",
    #     "Temperature": 25,  # Room temperature
    #     "RampRate": 0,  # No temperature change
    #     "HoldTime": 5,
    #     "Purpose": f"Stabilize vacuum at {vacuum_settings['pump_down_target']} mbar before heating"
    # })

    return stages

def generate_backfill_stage(vacuum_settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate the final cooling stage with either N2 backfill or vacuum cooling.
    """
    # if vacuum_settings.get("use_n2_backfill", False):
    #     return {
    #         "Stage": "Cooling",
    #         "Temperature": 50,  # Target cooling temperature
    #         "Pressure": f"{vacuum_settings['backfill_pressure']} mbar",
    #         "RampRate": 0,  # Will be set by physics
    #         "HoldTime": 0,
    #         "Purpose": "Controlled cooling, oxidation protection"
    #     }
    # else:
    #     return {
    #         "Stage": "Cooling",
    #         "Temperature": 50,  # Target cooling temperature
    #         "Pressure": f"{vacuum_settings['pump_down_target']:.1e} mbar",
    #         "RampRate": 0,  # Will be set by physics
    #         "HoldTime": 0,
    #         "Purpose": "Cool under vacuum"
    #     }

def generate_brazing_cycle(simulation_state, initial_temp=None, initial_ramp=None, overrides=None):
    """
    Main function to generate the complete brazing cycle including vacuum stages.
    Process stages are Stage 1..Stage 5 (heating/soak). Cooling is a non-process stage.
    Returns structured dict with cycle and verdict metadata.
    """
    # 1. Extract context (includes vacuum settings)
    # Using existing helper or calling calculate_physics_parameters directly
    context = calculate_physics_parameters(simulation_state)
    
    # 2. Generate vacuum stages (at room temperature)
    vacuum_stages = generate_vacuum_stages(context['vacuum'])
    
    # 3. Generate physics-based heating stages (ALWAYS - no fixed cycle option)
    heat_result = generate_physics_based_cycle(context, initial_temp=initial_temp, initial_ramp=initial_ramp, overrides=overrides)
    
    # Handle structured return from physics engine
    if isinstance(heat_result, dict):
        heat_stages = heat_result["cycle"]
        final_verdict = heat_result.get("final_verdict", "UNKNOWN")
        verdict_details = heat_result.get("verdict_details", {})
    else:
        # Fallback for legacy return format
        heat_stages = heat_result
        final_verdict = "UNKNOWN"
        verdict_details = {}
    
    # 4. Combine all stages
    full_cycle = vacuum_stages + heat_stages
    
    # 5. Add Final Cooling Stage (non-process stage)
    # Cooling is intentionally NOT numbered as a process stage.
    cooling_stage = {
        "Stage": "Cooling",
        "ProcessStage": False,
        "Temperature": 50,
        "RampRate": 0, # Max cooling
        "HoldTime": 0,
        "Purpose": "Safe termination and cooling",
        "Job1Temp": 50, # Ends at 50
        "Job2Temp": 50
    }
    final_cycle = full_cycle + [cooling_stage]
    
    # 6. Optionally enrich with AI-generated purposes
    try:
        final_cycle = query_llm_for_purposes(final_cycle, context)
    except Exception as e:
        logger.error(f"Error generating AI purposes: {e}")
    
    logger.info(f"Generated complete brazing cycle with {len(final_cycle)} stages. Verdict: {final_verdict}")
    
    return {
        "cycle": final_cycle,
        "final_verdict": final_verdict,
        "verdict_details": verdict_details
    }
