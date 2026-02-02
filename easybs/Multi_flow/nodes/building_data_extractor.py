# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:08:39 2025

@author: user
"""

# building_data_extractor.py

import os, json, re
from typing import Any, Dict, List
from openai import OpenAI
from state_schema import SimulationState

def _client():
    #key = os.getenv("OPENAI_API_KEY")
    key = "XXXXXX" #Input the Key here
    
    if not key:
        raise RuntimeError("KEY not set in environment.")
    return OpenAI(api_key=key)

SCHEMA_EXAMPLE = {
  "floors": 1,
  "floor_height": 2.5,
  "orientation": 0.0,
  "location": "Seoul, South Korea",
  "rooms": {
    "ExampleRoom": [[0.0,0.0],[0.0,3.0],[4.0,3.0],[4.0,0.0]]
  },
  "windows_ext": {
    "ExampleRoom": [ {"ori":"S","w":1.5,"h":1.2} ]
  },
  "windows_int": [
    {"room_a":"ExampleRoom","room_b":"AnotherRoom","w":2.0,"h":2.0,"subtype":"Door"}
  ]
}

PROMPT_TMPL = """You are an EnergyPlus/ASHRAE assistant.
Extract *multi-zone* geometry and windows from the user's text into STRICT JSON that matches this schema:

{schema}

Rules:
- Return JSON only (no markdown, no comments, no code fences).
- rooms: dictionary of room name → list of [x,y] vertices in meters (clockwise or counterclockwise is fine).
- windows_ext: per room, list of objects: {{"ori":"N|E|S|W","w":float,"h":float}}.
- windows_int: list of interior openings: {{"room_a":str,"room_b":str,"w":float,"h":float,"subtype":"Window|Door"}}.
- floors (int), floor_height (m), orientation (deg, 0-360), location (string).
- If a field is missing, infer conservatively or omit it.

User text:
\"\"\"{user_text}\"\"\""""

# ------------------- normalization helpers -------------------

def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def _to_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

def _norm_orientation(deg):
    d = _to_float(deg, 0.0) or 0.0
    d = d % 360.0
    if d < 0: d += 360.0
    return d

def _upper_nesw(s):
    s = (s or "").strip().upper()
    return s if s in ("N","E","S","W") else None

def _norm_rooms(rooms_in: Dict[str, Any]) -> Dict[str, List[List[float]]]:
    rooms_out: Dict[str, List[List[float]]] = {}
    if not isinstance(rooms_in, dict):
        return rooms_out
    for name, verts in rooms_in.items():
        clean: List[List[float]] = []
        if isinstance(verts, list):
            for p in verts:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    x = _to_float(p[0], 0.0)
                    y = _to_float(p[1], 0.0)
                    clean.append([x, y])
        if len(clean) >= 3:
            rooms_out[str(name)] = clean
    return rooms_out

def _norm_windows_ext(win_ext_in: Dict[str, Any]) -> Dict[str, List[Dict[str, float]]]:
    out: Dict[str, List[Dict[str, float]]] = {}
    if not isinstance(win_ext_in, dict):
        return out
    for room, specs in win_ext_in.items():
        lst: List[Dict[str, float]] = []
        if isinstance(specs, list):
            for s in specs:
                if not isinstance(s, dict): continue
                ori = _upper_nesw(s.get("ori") or s.get("orientation"))
                w = _to_float(s.get("w") or s.get("width"), None)
                h = _to_float(s.get("h") or s.get("height"), None)
                if ori and w and h and w > 0 and h > 0:
                    lst.append({"ori": ori, "w": w, "h": h})
        if lst:
            out[str(room)] = lst
    return out

def _norm_windows_int(win_int_in: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(win_int_in, list):
        return out
    for s in win_int_in:
        if not isinstance(s, dict): continue
        room_a = s.get("room_a") or s.get("a") or s.get("from")
        room_b = s.get("room_b") or s.get("b") or s.get("to")
        w = _to_float(s.get("w") or s.get("width"), None)
        h = _to_float(s.get("h") or s.get("height"), None)
        subtype = (s.get("subtype") or "Window").strip().title()
        if room_a and room_b and w and h and w > 0 and h > 0:
            out.append({"room_a": str(room_a), "room_b": str(room_b), "w": w, "h": h, "subtype": subtype})
    return out

def _postprocess(parsed: Dict[str, Any], state: SimulationState) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    cleaned["floors"] = _to_int(parsed.get("floors"), 1) or 1
    cleaned["floor_height"] = _to_float(parsed.get("floor_height"), 2.5) or 2.5
    cleaned["orientation"] = _norm_orientation(parsed.get("orientation"))
    cleaned["location"] = parsed.get("location") or "Seoul, South Korea"
    cleaned["rooms"] = _norm_rooms(parsed.get("rooms") or {})
    cleaned["windows_ext"] = _norm_windows_ext(parsed.get("windows_ext") or {})
    cleaned["windows_int"] = _norm_windows_int(parsed.get("windows_int") or [])

    # Optional: allow caller to hint an output file path via state
    default_out = state.get("idf_path") or os.path.abspath("./generated_idfs/geom_multizone.idf")
    cleaned["out_idf"] = parsed.get("out_idf") or default_out
    return cleaned

# ------------------- main node -------------------

def extract_building_geometry(state: SimulationState) -> SimulationState:
    """
    Multi-zone only extractor.
    If state already contains 'parsed_building_data' (override), it will be normalized and used directly.
    Otherwise, we prompt the LLM and normalize the result.
    """
    # 1) Fast path: allow a pre-parsed override (useful for local tests or UI that posts JSON)
    override = state.get("parsed_building_data")
    if isinstance(override, dict) and (override.get("rooms") or override.get("windows_ext")):
        state["parsed_building_data"] = _postprocess(override, state)
        return state

    # 2) LLM path
    user_input = state.get("user_input") or ""
    if not user_input.strip():
        return {"errors": ["No user input available for geometry extraction."]}

    client = _client()
    prompt = PROMPT_TMPL.format(
        schema=json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=2),
        user_text=user_input
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or "").strip()

        # Trim accidental code fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)

        raw = json.loads(content)
        state["parsed_building_data"] = _postprocess(raw, state)
        return state

    except json.JSONDecodeError as e:
        return {"errors": [f"JSON parsing error: {str(e)}", content]}
    except Exception as e:
        return {"errors": [str(e)]}
