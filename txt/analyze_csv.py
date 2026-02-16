import pandas as pd
import numpy as np

# Load CSV
file_path = 'TEST_REPORT_ID_28082024_221325.csv'
# Skip first 3 rows so row 4 (DATE TIME...) is the header
df = pd.read_csv(file_path, skiprows=3)

# Clean column names (remove spaces)
df.columns = df.columns.str.strip()

# Parse DateTime with error handling
df['TIMESTAMP'] = pd.to_datetime(df['DATE TIME'], format='%d-%m-%Y %H:%M', errors='coerce')
df = df.dropna(subset=['TIMESTAMP'])
print(f"Loaded {len(df)} rows. Range: {df['TIMESTAMP'].min()} to {df['TIMESTAMP'].max()}")

# Columns of interest
# MASTER TEMP, JOB1 TEMP, JOB2 TEMP
cols = ['MASTER TEMP', 'JOB1 TEMP', 'JOB2 TEMP']
for c in cols:
    df[c] = pd.to_numeric(df[c], errors='coerce')

df = df.dropna(subset=cols)
# We want to detect "Stages".
# A stage change is usually characterized by a change in the Master Temp Setpoint behavior (Ramp vs Soak).
# But we don't have Setpoint column, only Actual.
# We can detect "Soak" when dy/dt of Master Temp is near zero.

df['Master_Rate'] = df['MASTER TEMP'].diff() # per minute since freq is 1 min roughly
df['Minutes'] = (df['TIMESTAMP'] - df['TIMESTAMP'].iloc[0]).dt.total_seconds() / 60.0

print("Time,Master,Job1,Job2,Master_Rate,Calc_Lag1,Calc_Lag2")

segments = []

# State Machine
status = "Ramp"
current_segment = {
    'type': 'Ramp', 
    'start_temp': df['MASTER TEMP'].iloc[0], 
    'start_time': df['TIMESTAMP'].iloc[0]
}

segments = []
rolling_rate = df['Master_Rate'].rolling(window=5).mean().abs()

for i in range(10, len(df)):
    rate = rolling_rate.iloc[i]
    temp = df['MASTER TEMP'].iloc[i]
    time_now = df['TIMESTAMP'].iloc[i]
    
    # Transition Logic
    if status == "Ramp":
        if rate < 0.3 and temp > 50: # Settled?
            if rolling_rate.iloc[i:i+5].max() < 0.5:
                # Transition to Soak
                status = "Soak"
                print(f"--- RAMP END / SOAK START at {time_now} Temp={temp} ---")
                
                delta_temp = temp - current_segment['start_temp']
                duration_min = (time_now - current_segment['start_time']).total_seconds() / 60.0
                
                avg_rate = delta_temp / max(1, duration_min)
                
                # Analyze Lags at End of Ramp
                lag1 = temp - df['JOB1 TEMP'].iloc[i]
                lag2 = temp - df['JOB2 TEMP'].iloc[i]
                
                print(f"Ramp Analysis: DeltaT={delta_temp:.1f}, Duration={duration_min:.1f}min, AvgRate={avg_rate:.2f} C/min")
                
                segments.append({
                    "Type": "Ramp",
                    "EndTemp": temp,
                    "Rate": avg_rate,
                    "Lag1": lag1,
                    "Lag2": lag2
                })

    elif status == "Soak":
        if rate > 0.8: # Moving again
             # SOAK ENDED
             soak_end_time = time_now
             soak_duration = (soak_end_time - current_segment['start_time']).total_seconds() / 60.0
             
             print(f"--- SOAK END / RAMP START at {time_now} Temp={temp} Duration={soak_duration:.1f} min ---")
             
             segments.append({
                 "Type": "Soak",
                 "Temp": current_segment['start_temp'],
                 "Duration": soak_duration
             })
             
             status = "Ramp"
             current_segment['start_temp'] = temp
             current_segment['start_time'] = time_now

# Write Summary
print("\n========== SUMMARY TABLE ==========")
# Check if last segment was soak
if status == "Soak":
    soak_duration = (df['TIMESTAMP'].iloc[-1] - current_segment['start_time']).total_seconds() / 60.0
    segments.append({"Type": "Soak", "Temp": current_segment['start_temp'], "Duration": soak_duration})

print("Type,Temp,Duration")
with open('summary_soaks.csv', 'w') as f:
    f.write("Type,Temp,Duration\n")
    for s in segments:
        if s['Type'] == 'Soak':
            f.write(f"{s['Type']},{s['Temp']:.1f},{s['Duration']:.1f}\n")
            print(f"{s['Type']},{s['Temp']:.1f},{s['Duration']:.1f}")
print("Type,EndTemp,Rate,Lag1,Lag2")
for s in segments:
    print(f"{s['Type']},{s['EndTemp']:.1f},{s['Rate']:.2f},{s['Lag1']:.1f},{s['Lag2']:.1f}")
with open('summary.csv', 'w') as f:
    f.write("Type,EndTemp,Rate,Lag1,Lag2\n")
    for s in segments:
        f.write(f"{s['Type']},{s['EndTemp']:.1f},{s['Rate']:.2f},{s['Lag1']:.1f},{s['Lag2']:.1f}\n")

# Manually check key timestamps from user report if needed
# 360 C soak start
# 384 C soak start
# ...
