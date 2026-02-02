# -*- coding: utf-8 -*-
"""
Created on Sat Oct 25 01:23:34 2025

@author: Xiguan Liang @SKKU
"""
# fastapi_app.py
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, Any
import subprocess, os, time
import uvicorn

from energyplus_runner import energyplus_runner as run_eplus_node
import os

from run_graph import build_idf_from_prompt, build_idfs_from_file   # from same folder as run_graph.py

ENERGYPLUS_EXE = r"C:\EnergyPlusV8-9-0\energyplus.exe"   # <-- set correctly
EPW_PATH       = r"C:\EnergyPlusV8-9-0\WeatherData\KOR_INCH'ON_IWEC.epw"  
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "ep_outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="LangGraph + EnergyPlus API", version="0.3.0")

class SimInput(BaseModel):
    prompt: Optional[str] = None
    read_local_file: bool = False
    input_file_path: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])




@app.post("/simulate")
def simulate(inp: SimInput):
    try:
        if not inp.read_local_file:
            if not inp.prompt or not inp.prompt.strip():
                return {"ok": False, "error": "Please provide 'prompt' text."}
            final_state, idf_path = build_idf_from_prompt(inp.prompt.strip())
        else:
            # ... your legacy file path flow ...
            pass

        # Helpful while debugging: see exactly what keys your graph produced
        # print("FINAL STATE KEYS:", list(final_state.keys()))  # optional log

        # Be flexible about key names during transition
        # --- resolve IDF path from graph results ---
        idf_path = (
            final_state.get("idf_path")
            or final_state.get("generated_idf")
            or final_state.get("output_idf")
        )
        if not idf_path or not os.path.isfile(idf_path):
            return {"ok": False, "error": f"IDF not found: {idf_path}", "state": final_state}
        
        # --- resolve EPW from request options → graph state → node default ---
        epw_path = (
            (inp.options or {}).get("epw_path")
            or final_state.get("epw_path")        
        )
        if not os.path.isfile(epw_path):
            return {"ok": False, "error": f"Weather file not found: {epw_path}", "idf_path": idf_path, "state": final_state}
        
        # --- ensure state carries what the node expects ---
        final_state["idf_path"]   = idf_path
        final_state["epw_path"]   = epw_path
        final_state["output_dir"] = final_state.get("output_dir") or os.path.join(os.path.dirname(__file__), "ep_outputs")
        
        # --- HAND OFF TO YOUR LANGGRAPH NODE (same as Spyder path) ---
        final_state = run_eplus_node(final_state)
        
        return {
            "ok": True,
            "idf_path": idf_path,
            "epw_path": epw_path,
            "state": final_state,  # node’s results will be inside here (e.g., simulation_result)
        }


    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
