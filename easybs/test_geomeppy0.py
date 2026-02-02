# -*- coding: utf-8 -*-
"""
Standalone builder for an EnergyPlus 8.9 IDF using geomeppy.
- Creates a 3-storey rectangular block
- Places windows per-orientation with equal gaps
- Links inter-storey Floor↔Ceiling as interzone "Surface" pairs (no sun/wind)
- Rotates geometry to the requested orientation (North_Axis left at 0 to avoid double-rotation)
- Assigns simple opaque/glazing constructions
- Adds IdealLoadsAirSystem per zone (for quick thermal checks)

Usage:
    python test_geomeppy.py

Make sure the IDD and seed IDF paths below are correct for your machine.
"""

import os
import uuid
import math
from datetime import datetime
from geomeppy import IDF

# ---------- Paths (EDIT THESE IF NEEDED) ----------
IDD_PATH = r"C:/EnergyPlusV8-9-0/Energy+.idd"
SEED_IDF = r"C:/EnergyPlusV8-9-0/ExampleFiles/Minimal.idf"
OUT_DIR  = "./generated_idfs"
os.makedirs(OUT_DIR, exist_ok=True)

# ---------- Building parameters (from your prompt) ----------
LENGTH_X = 20.8            # m
WIDTH_Y  = 14.2            # m
N_STORIES = 3
STOREY_H  = 3.3            # m each
ORIENTATION_DEG = 45.0     # degrees clockwise from North
WIN_W = 1.5
WIN_H = 1.5

WINDOW_COUNTS = {
    "north": 6,
    "east":  5,
    "south": 7,
    "west":  2,
}
# (Optionally) Site location: Seoul, South Korea
SITE_LOCATION = dict(
    Name="SEOUL_KOR_CUSTOM",
    Latitude=37.5665,
    Longitude=126.9780,
    Time_Zone=9,
    Elevation=38.0,
)

SILL_H = 1.0  # window sill height

# ---------- Helpers from your node (trimmed & adapted) ----------
def _getobjects(idf, key):
    if hasattr(idf, "idfobjects"):
        return idf.idfobjects.get(key, [])
    return idf.getobjects(key)

def _remove_all(idf, key):
    try:
        for o in list(_getobjects(idf, key)):
            idf.removeidfobject(o)
    except Exception:
        pass

def clear_geometry(idf):
    for k in [
        "FENESTRATIONSURFACE:DETAILED",
        "BUILDINGSURFACE:DETAILED",
        "SHADING:ZONE:DETAILED",
        "SHADING:BUILDING:DETAILED",
        "ZONE",
        "SURFACEPROPERTY:EXPOSEDFOUNDATIONPERIMETER",
    ]:
        _remove_all(idf, k)

def get_vertices(surf):
    verts = []
    for i in range(1, 11):
        x_name = f"Vertex_{i}_Xcoordinate"
        y_name = f"Vertex_{i}_Ycoordinate"
        z_name = f"Vertex_{i}_Zcoordinate"
        if not hasattr(surf, x_name):
            break
        try:
            x = float(getattr(surf, x_name)); y = float(getattr(surf, y_name)); z = float(getattr(surf, z_name))
        except (AttributeError, ValueError, TypeError):
            break
        verts.append((x, y, z))
    return verts

def map_orientation(azimuth_deg, tol=12):
    a = int(round(float(azimuth_deg))) % 360
    if min(abs(a - 0),   360 - abs(a - 0))   <= tol: return "north"
    if min(abs(a - 90),  360 - abs(a - 90))  <= tol: return "east"
    if min(abs(a - 180), 360 - abs(a - 180)) <= tol: return "south"
    if min(abs(a - 270), 360 - abs(a - 270)) <= tol: return "west"
    return None

def wall_base_edge(verts):
    vs = sorted(verts, key=lambda v: (v[2], v[0], v[1]))
    return vs[0], vs[1] if len(vs) > 1 else vs[0]

def wall_span_info(verts):
    a, b = wall_base_edge(verts)
    vx, vy = (b[0]-a[0], b[1]-a[1])
    L = math.hypot(vx, vy)
    if L == 0:
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if (x_max - x_min) >= (y_max - y_min):
            a = min(verts, key=lambda v: v[0]); b = max(verts, key=lambda v: v[0])
            vx, vy = (b[0]-a[0], b[1]-a[1]); L = abs(x_max - x_min)
        else:
            a = min(verts, key=lambda v: v[1]); b = max(verts, key=lambda v: v[1])
            vx, vy = (b[0]-a[0], b[1]-a[1]); L = abs(y_max - y_min)
    ulen = math.hypot(vx, vy) or 1.0
    ux, uy = (vx/ulen, vy/ulen)
    proj_a = a[0]*ux + a[1]*uy; proj_b = b[0]*ux + b[1]*uy
    p0 = a if proj_a <= proj_b else b
    if p0 is b:
        ux, uy = -ux, -uy
    return (ux, uy), p0, L

def centers_equal_gaps_along_length(L, n, item_w):
    g = (L - n * item_w) / (n + 1)
    if g < 0:
        raise ValueError(f"Windows do not fit along L={L:.3f} m: n={n}, W={item_w:.3f}")
    centers = []
    left = g
    for i in range(n):
        left_i = left + i * (item_w + g)
        centers.append(left_i + item_w / 2.0)
    return centers, g

def group_walls_by_floor_and_orientation(idf, N_STORIES, STOREY_H):
    groups = {}
    for wall in idf.getsurfaces(surface_type="Wall"):
        verts = get_vertices(wall)
        if len(verts) < 2: continue
        ori = map_orientation(getattr(wall, "azimuth", 0.0))
        if ori is None: continue
        (ux, uy), p0, L = wall_span_info(verts)
        z_floor = min(v[2] for v in verts)
        floor_idx = max(0, min(N_STORIES - 1, int(round(z_floor / STOREY_H))))
        groups.setdefault(floor_idx, {}).setdefault(ori, []).append((wall, verts, L, (ux, uy), p0, z_floor))
    return groups

def add_windows_all_sides(idf, N_STORIES, STOREY_H, WINDOW_COUNTS, WINDOW_WIDTHS, WINDOW_HEIGHTS):
    groups = group_walls_by_floor_and_orientation(idf, N_STORIES, STOREY_H)
    for f in range(N_STORIES):
        for ori in ("north", "east", "south", "west"):
            target_n = WINDOW_COUNTS.get(ori, 0)
            if target_n <= 0: continue
            cand = groups.get(f, {}).get(ori, [])
            if not cand:
                print(f"⚠️ No {ori} wall found for floor {f+1}")
                continue
            wall, verts, L, (ux, uy), p0, z_floor = max(cand, key=lambda t: t[2])
            WIN_W = WINDOW_WIDTHS[ori]; WIN_H = WINDOW_HEIGHTS[ori]
            try:
                centers, g = centers_equal_gaps_along_length(L, target_n, WIN_W)
            except ValueError as e:
                print(f"⚠️ Floor {f+1} {ori}: {e} (skipping)"); continue
            sill = z_floor + SILL_H; head = sill + WIN_H
            for i, s_c in enumerate(centers, 1):
                s1 = s_c - WIN_W/2.0; s2 = s_c + WIN_W/2.0
                x1 = p0[0] + ux * s1; y1 = p0[1] + uy * s1
                x2 = p0[0] + ux * s2; y2 = p0[1] + uy * s2
                coords = [(x1, y1, sill), (x2, y2, sill), (x2, y2, head), (x1, y1, head)]
                idf.newidfobject(
                    "FENESTRATIONSURFACE:DETAILED",
                    Name=f"{ori.capitalize()}Win_F{f+1}_{i}",
                    Surface_Type="Window",
                    Building_Surface_Name=wall.Name,
                    Vertex_1_Xcoordinate=coords[0][0], Vertex_1_Ycoordinate=coords[0][1], Vertex_1_Zcoordinate=coords[0][2],
                    Vertex_2_Xcoordinate=coords[1][0], Vertex_2_Ycoordinate=coords[1][1], Vertex_2_Zcoordinate=coords[1][2],
                    Vertex_3_Xcoordinate=coords[2][0], Vertex_3_Ycoordinate=coords[2][1], Vertex_3_Zcoordinate=coords[2][2],
                    Vertex_4_Xcoordinate=coords[3][0], Vertex_4_Ycoordinate=coords[3][1], Vertex_4_Zcoordinate=coords[3][2],
                )
            print(f"√ Floor {f+1} {ori}: {target_n} windows | gaps g={g:.3f} m | wall L={L:.3f} m")
    try:
        if hasattr(idf, "check_subsurfaces"):
            idf.check_subsurfaces()
    except Exception as e:
        print(f"⚠️ check_subsurfaces failed: {e}")

def _centroid_xy_of_walls(idf):
    xs, ys, n = 0.0, 0.0, 0
    for s in idf.getsurfaces(surface_type="Wall"):
        verts = get_vertices(s)
        for (x, y, _z) in verts:
            xs += x; ys += y; n += 1
    return (xs/n, ys/n) if n else (0.0, 0.0)

def _rotate_xy_vertices(idf, angle_deg, origin_xy):
    cx, cy = origin_xy
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    def _rot(x, y):
        x0, y0 = x - cx, y - cy
        return (x0*c - y0*s + cx, x0*s + y0*c + cy)
    for key in ["BUILDINGSURFACE:DETAILED", "FENESTRATIONSURFACE:DETAILED", "SHADING:ZONE:DETAILED", "SHADING:BUILDING:DETAILED"]:
        for obj in _getobjects(idf, key):
            for i in range(1, 11):
                x_name = f"Vertex_{i}_Xcoordinate"; y_name = f"Vertex_{i}_Ycoordinate"
                if not hasattr(obj, x_name): break
                try:
                    x = float(getattr(obj, x_name)); y = float(getattr(obj, y_name))
                except (AttributeError, ValueError, TypeError):
                    break
                xr, yr = _rot(x, y)
                setattr(obj, x_name, xr); setattr(obj, y_name, yr)

def _zone_base_z(idf, zone_name):
    zmin = None
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name != zone_name: continue
        for (x, y, z) in get_vertices(s):
            zmin = z if zmin is None else min(zmin, z)
    return zmin if zmin is not None else 0.0

def _first_surface_in_zone(idf, zone_name, surface_types):
    stypes = {st.lower() for st in surface_types}
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name == zone_name and s.Surface_Type.lower() in stypes:
            return s
    return None

def _sorted_zones_by_height(idf):
    zones = [z.Name for z in idf.idfobjects["ZONE"]
    ]
    zones_with_z = [(zn, _zone_base_z(idf, zn)) for zn in zones]
    zones_with_z.sort(key=lambda t: t[1])
    return [zn for (zn, _) in zones_with_z]

def link_interzone_floors_ceilings(idf):
    zone_order = _sorted_zones_by_height(idf)
    made, issues = 0, []
    for i in range(len(zone_order) - 1):
        lower_zone = zone_order[i]; upper_zone = zone_order[i + 1]
        lower_ceiling = _first_surface_in_zone(idf, lower_zone, ["Ceiling", "Roof"])
        upper_floor   = _first_surface_in_zone(idf, upper_zone, ["Floor"])
        if not lower_ceiling or not upper_floor:
            issues.append((lower_zone, upper_zone, bool(lower_ceiling), bool(upper_floor))); continue
        lower_ceiling.Outside_Boundary_Condition = "Surface"
        lower_ceiling.Outside_Boundary_Condition_Object = upper_floor.Name
        lower_ceiling.Sun_Exposure = "NoSun"; lower_ceiling.Wind_Exposure = "NoWind"
        upper_floor.Outside_Boundary_Condition = "Surface"
        upper_floor.Outside_Boundary_Condition_Object = lower_ceiling.Name
        upper_floor.Sun_Exposure = "NoSun"; upper_floor.Wind_Exposure = "NoWind"
        made += 1
    if made: print(f"√ Interzone Floor↔Ceiling links created: {made} pair(s)")
    if issues:
        for (lz, uz, has_c, has_f) in issues:
            print(f"⚠️ Could not pair {lz} ↔ {uz} (ceiling/roof: {has_c}, floor: {has_f})")

def build_idf():
    IDF.setiddname(IDD_PATH)
    idf = IDF(SEED_IDF)

    clear_geometry(idf)

    total_h = STOREY_H * N_STORIES
    idf.add_block(
        name="TestBlock",
        coordinates=[(0, 0), (LENGTH_X, 0), (LENGTH_X, WIDTH_Y), (0, WIDTH_Y)],
        height=total_h,
        num_stories=N_STORIES,
        below_ground_stories=0,
        zoning="by_storey",
    )

    # SimulationControl
    for obj in list(idf.idfobjects.get("SIMULATIONCONTROL", [])):
        idf.removeidfobject(obj)
    idf.newidfobject(
        "SIMULATIONCONTROL",
        Do_Zone_Sizing_Calculation="Yes",
        Do_System_Sizing_Calculation="Yes",
        Do_Plant_Sizing_Calculation="Yes",
        Run_Simulation_for_Sizing_Periods="Yes",
        Run_Simulation_for_Weather_File_Run_Periods="Yes",
    )

    # IdealLoads per zone
    for zone in idf.idfobjects["ZONE"]:
        idf.newidfobject(
            "ZONEHVAC:IDEALLOADSAIRSYSTEM",
            Name=f"ILS_{zone.Name}",
            Zone_Supply_Air_Node_Name=f"{zone.Name}_Supply",
            Zone_Exhaust_Air_Node_Name=f"{zone.Name}_Exhaust",
        )

    # Normalize / match
    for fn in ("translate_to_origin", "intersect_match"):
        if hasattr(idf, fn):
            try: getattr(idf, fn)()
            except Exception: pass

    # Link floors/ceilings
    link_interzone_floors_ceilings(idf)

    # Windows
    window_widths  = {ori: WIN_W for ori in ("north", "east", "south", "west")}
    window_heights = {ori: WIN_H for ori in ("north", "east", "south", "west")}
    add_windows_all_sides(idf, N_STORIES, STOREY_H, WINDOW_COUNTS, window_widths, window_heights)

    # Glazing + construction
    idf.newidfobject(
        "WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
        Name="GLZ_Simple_3Wm2K_SHGC60_VT70",
        UFactor=3.0,
        Solar_Heat_Gain_Coefficient=0.60,
        Visible_Transmittance=0.70,
    )
    idf.newidfobject(
        "CONSTRUCTION",
        Name="WIN_Simple_Clear",
        Outside_Layer="GLZ_Simple_3Wm2K_SHGC60_VT70",
    )
    for win in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        win.Construction_Name = "WIN_Simple_Clear"

    # Opaque constructions
    idf.newidfobject(
        "MATERIAL",
        Name="OpaqueWall_Mat",
        Roughness="MediumRough",
        Thickness=0.2,
        Conductivity=0.1,
        Density=1800,
        Specific_Heat=900,
    )
    idf.newidfobject(
        "MATERIAL",
        Name="OpaqueRoof_Mat",
        Roughness="MediumRough",
        Thickness=0.25,
        Conductivity=0.8,
        Density=2000,
        Specific_Heat=900,
    )
    idf.newidfobject(
        "MATERIAL",
        Name="OpaqueFloor_Mat",
        Roughness="MediumRough",
        Thickness=0.25,
        Conductivity=1.4,
        Density=2200,
        Specific_Heat=900,
    )
    idf.newidfobject("CONSTRUCTION", Name="WALL_Const",  Outside_Layer="OpaqueWall_Mat")
    idf.newidfobject("CONSTRUCTION", Name="ROOF_Const",  Outside_Layer="OpaqueRoof_Mat")
    idf.newidfobject("CONSTRUCTION", Name="FLOOR_Const", Outside_Layer="OpaqueFloor_Mat")

    for srf in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if not srf.Construction_Name:
            stype = srf.Surface_Type.upper()
            if stype == "WALL":
                srf.Construction_Name = "WALL_Const"
            elif stype in ("ROOF", "CEILING"):
                srf.Construction_Name = "ROOF_Const"
            elif stype == "FLOOR":
                srf.Construction_Name = "FLOOR_Const"

    # Rotate geometry by -ORIENTATION_DEG; keep Building North_Axis at 0
    cx, cy = _centroid_xy_of_walls(idf)
    try:
        if hasattr(idf, "translate"):
            idf.translate((-cx, -cy, 0.0))
        if hasattr(idf, "rotate"):
            idf.rotate(-ORIENTATION_DEG)
            print(f"↻ Rotated geometry by {-ORIENTATION_DEG}°")
        if hasattr(idf, "translate"):
            idf.translate((cx, cy, 0.0))
    except Exception as e:
        print("⚠️ idf.rotate failed; attempting manual rotation", e)
        try:
            _rotate_xy_vertices(idf, -ORIENTATION_DEG, origin_xy=(cx, cy))
        except Exception as e2:
            print("⚠️ Manual rotation failed:", e2)

    try:
        idf.idfobjects["BUILDING"][0].North_Axis = 0.0
    except Exception:
        pass

    out_idf = os.path.join(OUT_DIR, "test_with_windows.idf")
    idf.saveas(out_idf)
    print(f"√ IDF saved → {out_idf}")
    return out_idf

if __name__ == "__main__":
    IDF.setiddname(IDD_PATH)
    print(f"⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Building IDF...")
    path = build_idf()
    print("Done:", path)
