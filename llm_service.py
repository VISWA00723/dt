
import json
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Ollama Configuration
OLLAMA_API_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.2:3b"

def analyze_temperature_difference(stages: List[Dict[str, Any]]) -> str:
    """
    Analyze the temperature difference between Job 1 and Job 2 using Ollama.
    
    Args:
        stages: List of brazing cycle stage dictionaries
        
    Returns:
        String containing the AI's analysis
    """
    try:
        # Construct a prompt with the cycle data
        prompt_data = []
        for stage in stages:
            s_name = stage.get('Stage', 'Unknown')
            t_master = stage.get('Temperature', 0)
            t_job1 = stage.get('Job1Temp', 0)
            t_job2 = stage.get('Job2Temp', 0)
            prompt_data.append(f"- {s_name}: Master={t_master}°C, Job1={t_job1}°C, Job2={t_job2}°C")
            
        data_str = "\n".join(prompt_data)
        
        prompt = f"""
You are an expert metallurgist and thermal engineer specialized in vacuum brazing of aluminum.
Analyze the following brazing cycle data, specifically focusing on the temperature difference (lag) between Job 1 and Job 2.

Context:
- Job 1 is positioned closer to the heating elements (surface/outer).
- Job 2 is positioned in the center or shadowed by fixtures (core).
- Master Temp is the furnace setpoint.
- There is typically a 5-10°C lag between Job 1 and Job 2.

Cycle Data:
{data_str}

Please explain:
1. Why this temperature difference exists (physics of bad vacuum heat transfer, radiation view factors, thermal mass).
2. Why Job 2 lags behind Job 1.
3. If this difference is safe for AL718/6061 brazing (process window considerations).
4. Any recommendations to improve uniformity (e.g. soak times).

Keep your answer concise (max 3-4 paragraphs) and technical.
"""

        # Prepare the request
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            OLLAMA_API_URL, 
            data=data, 
            headers={'Content-Type': 'application/json'}
        )
        
        # Send request
        logger.info(f"Sending analysis request to Ollama ({MODEL_NAME})...")
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            analysis = result.get('response', 'No response generated.')
            return analysis

    except urllib.error.URLError as e:
        error_msg = f"Could not connect to Ollama at {OLLAMA_API_URL}. Is it running? Error: {e}"
        logger.error(error_msg)
        return f"Error: {error_msg}. Please ensure 'ollama serve' is running and you have pulled the model '{MODEL_NAME}'."
    except Exception as e:
        logger.error(f"Error during AI analysis: {e}")
        return f"Error analyzing cycle: {str(e)}"
