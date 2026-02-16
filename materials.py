# Material database
MATERIAL_DB = {
    "6061-T6": {
        "density": 2700,  # kg/m³
        "thermal_conductivity": 167,  # W/(m·K)
        "specific_heat": 900,  # J/(kg·K)
        "solidus": 640,  # °C
        "liquidus": 651,  # °C
        "color": [0.1, 0.1, 0.8, 1.0]  # RGBA - blue
    },
    # "3003-O": {
    #     "density": 2730,  # kg/m³
    #     "thermal_conductivity": 160,  # W/(m·K)
    #     "specific_heat": 880,  # J/(kg·K)
    #     "solidus": 630,  # °C
    #     "liquidus": 655,  # °C
    #     "color": [0.8, 0.8, 0.1, 1.0]  # RGBA - yellow/gold
    # },
    "AL718": {
        "density": 2700,  # kg/m³
        "thermal_conductivity": 160,  # W/(m·K)
        "specific_heat": 900,  # J/(kg·K)
        "solidus": 577,  # °C
        "liquidus": 582,  # °C
        "color": [0.9, 0.7, 0.1, 1.0]  # RGBA - gold
    },
    "SS316L": {
        "density": 8000,  # kg/m³
        "thermal_conductivity": 16,  # W/(m·K)
        "specific_heat": 500,  # J/(kg·K)
        "solidus": 1370,  # °C
        "liquidus": 1400,  # °C
        "color": [0.7, 0.7, 0.7, 1.0]  # RGBA - gray (stainless steel)
    },
    "CARBON_SHEET": {
        "density": 1800,  # kg/m³ (not critical for interface layer)
        "thermal_conductivity": 5.0,  # W/(m·K) - VERY LOW vs aluminum
        "specific_heat": 700,  # J/(kg·K) (low impact)
        "emissivity": 0.9,  # HIGH - key effect for radiation
        "role": "thermal_interface",  # Special marker
        "color": [0.2, 0.2, 0.2, 1.0]  # RGBA - dark gray/black
    }
}
