# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 17:24:36 2025

@author: user
"""


# ./Multi_flow/run_graph.py
import os, json, argparse
from datetime import datetime
from graph_config import define_graph  # your multi-zone graph
from state_schema import SimulationState

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--idf-out", default=os.path.abspath("./generated_idfs/geom_multizone.idf"))
    parser.add_argument("--epw", default=r"C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw")
    parser.add_argument("--outdir", default="eplusout")
    args = parser.parse_args()

    # Ensure project root
    os.chdir(os.path.dirname(__file__))

    app = define_graph()

    initial_state: SimulationState = {
        "user_input": args.prompt,
        "epw_path": args.epw,
        "output_dir": args.outdir,
        # allow extractor/node to pick this up as default output path
        "idf_path": args.idf_out,
    }

    # Optional: stream logs to console (kept short)
    for _ in app.stream(initial_state):
        pass

    final_state = app.invoke(initial_state)
    # Return compact JSON to caller (Node)
    print(json.dumps(final_state, ensure_ascii=False))

if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Multi_flow/run_graph.py starting…")
    main()

