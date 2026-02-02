# -*- coding: utf-8 -*-
"""
Created on Sat Sep 27 16:05:56 2025

@author: user
"""

import os
from geomeppy import IDF
from state_schema import SimulationState

# Set the IDD path
IDD_PATH = "C:/EnergyPlusV8-9-0/Energy+.idd"
IDF.setiddname(IDD_PATH)

def set_simulation_control_to_runperiod_only(idf):
    """Ensure SimulationControl runs only for weather-file run periods, not sizing."""
    # Remove any existing SimulationControl objects
    for sc in list(idf.idfobjects["SIMULATIONCONTROL"]):
        idf.removeidfobject(sc)

    # Create a clean SimulationControl
    sc = idf.newidfobject("SIMULATIONCONTROL")
    sc.Do_Zone_Sizing_Calculation = "No"
    sc.Do_System_Sizing_Calculation = "No"
    sc.Do_Plant_Sizing_Calculation = "No"
    sc.Run_Simulation_for_Sizing_Periods = "No"
    sc.Run_Simulation_for_Weather_File_Run_Periods = "Yes"

    print("✓ SimulationControl updated: RunPeriod only (sizing calculations disabled).")
    return sc


def idf_defi_output(state: SimulationState) -> SimulationState:
    print("[Multi] entering energyplus_defi_output with:", state.get("idf_path"))

    idf_path = state.get("idf_path")
    epw_path = state.get("epw_path")
    user_input = state.get("user_input", "").lower()

    if not idf_path or not os.path.exists(idf_path):
        return {"errors": ["No IDF path found to modify."]}

    try:
        idf = IDF(idf_path)

        # Remove all existing outputs
        idf.idfobjects["OUTPUT:VARIABLE"] = []
        idf.idfobjects["OUTPUT:METER"] = []
        
        # Remove all design day sizing periods
        #idf.idfobjects["SIZINGPERIOD:DESIGNDAY"] = []
        
        # Adjust timestep to 1 per hour
        idf.idfobjects["TIMESTEP"][0].Number_of_Timesteps_per_Hour = 1
        
        # Remove existing Site:Location objects if any
        idf.idfobjects["SITE:LOCATION"] = []
        
        # Add Seoul location
        idf.newidfobject(
            "SITE:LOCATION",
            Name="SEOUL_KOR_WMO_471100",
            Latitude=37.566,
            Longitude=126.978,
            Time_Zone=9,
            Elevation=86
        )

        
        # Adjust RunPeriod (Jan 1 to Jan 2)
        if idf.idfobjects["RUNPERIOD"]:
            runperiod = idf.idfobjects["RUNPERIOD"][0]
            runperiod.Begin_Month = 1
            runperiod.Begin_Day_of_Month = 1
            runperiod.End_Month = 12
            runperiod.End_Day_of_Month = 31
        else:
            idf.newidfobject(
                "RUNPERIOD",
                Name="Custom RunPeriod",
                Begin_Month=1,
                Begin_Day_of_Month=1,
                End_Month=1,
                End_Day_of_Month=2
            )


        # ✅ Add indoor temperature outputs if requested
        #print("❗User input received:", repr(user_input))

        #if "indoor temperature" in user_input or "zone temperature" in user_input:
        for zone in idf.idfobjects["ZONE"]:
            idf.newidfobject(
                "OUTPUT:VARIABLE",
                Key_Value=zone.Name,
                Variable_Name="Zone Mean Air Temperature",
                Reporting_Frequency="hourly"
            )        
        
        set_simulation_control_to_runperiod_only(idf)
        #%%
        # Save with updated path (IMPORTANT!)
        # ✅ Save modified IDF
        modified_path = idf_path.replace(".idf", "_modified.idf")
        idf.saveas(modified_path)

        return {
            "idf_path": modified_path,
            "epw_path": epw_path or "C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw",
            "output_dir": state.get("output_dir", "eplusout"),
            "message": "IDF modified: only hourly indoor temperature outputs included."
        }

    except Exception as e:
        return {"errors": [f"Error modifying IDF: {str(e)}"]}
