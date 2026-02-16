# Torpedo Effect — Geometry Nodes Learnings

## Date: 2026-02-16

## Summary

Built a Geometry Nodes torpedo simulation with target-seeking physics, repulsor avoidance, and emission visuals. Two torpedoes launch at different times, fly toward separate targets, one curves around a repulsor, both disappear on arrival. Everything runs inside Geometry Nodes — no Python frame handlers.

## Group Input Propagation: Nuanced Behavior

**Group Input values propagate correctly to nodes OUTSIDE the Simulation Zone** — modifier override values appear on consuming nodes as expected.

**Group Input values do NOT propagate to nodes INSIDE the Simulation Zone.** Nodes inside the sim zone receive the interface default, not the modifier override. This affects:

1. **Group Input → Simulation Zone body nodes** (confirmed)
2. **Group Input → Collection Info inside sim zone** (confirmed)

### Workaround: Pass-Through State Items

Instead of connecting Group Inputs directly to nodes inside the sim zone, use state items as pass-throughs:

```
Group Input[Param] → sim_in.inputs[ParamState]     (external entry)
sim_in.outputs[ParamState] → consuming_node          (use the value)
sim_in.outputs[ParamState] → sim_out.inputs[ParamState]  (pass-through)
```

This was tested and confirmed working — modifier override values (e.g., Attraction=400) propagate correctly through state items to consuming nodes inside the sim zone.

For nodes **outside** the sim zone (e.g., TorpedoSphere.Radius), Group Input connections work directly without state items.

### Alternative: Hardcode on Node Sockets

For values that don't need user-tuning, set directly on consuming node input sockets:
- **Float parameters**: `node.inputs[N].default_value = value`
- **Collection references**: `coll_info.inputs['Collection'].default_value = collection`
- **Object references**: `obj_info.inputs['Object'].default_value = obj`

## CORRECTION: Object Info DOES Work Inside Simulation Zones

**Previous claim was wrong.** Object Info nodes inside Simulation Zones **DO produce correct values** when:
- The object reference is set directly on the node socket (not through Group Input)
- The `transform_space` is set to `'ORIGINAL'`

Tested: Object Info for Target1 inside a Simulation Zone returned the correct world position at all frames, including after the target was moved in the viewport. This was verified with Store Named Attribute debugging.

The earlier failures were likely caused by corrupted node trees from extensive modification, or by routing the object reference through Group Input (which hits the silent zeros bug).

**This means the animation IS driven by scene objects:** moving Target1, Target2, or Repulsor1 in the viewport changes torpedo trajectories without any Python code running.

## Scene Time Works Inside Simulation Zones

The **Scene Time** node produces correct Frame and Seconds values inside Simulation Zones. Used for frame-based activation triggers:

```
Scene Time[Frame] → Math(GREATER_THAN, threshold=launch_frame - 0.5) → activation signal
```

Note: Math node has no `GREATER_EQUAL` operation. Use `GREATER_THAN` with threshold - 0.5.

## Simulation Zone Geometry is Self-Contained

The geometry entering a Simulation Zone on frame 1 becomes the sim zone's internal geometry for all subsequent frames. External changes to source objects (like Named Attributes updated by other modifiers) do NOT propagate into the sim zone after initialization.

Named Attributes stored on geometry BEFORE the sim zone are NOT available inside the sim zone on subsequent frames — only standard attributes (`position`, `.edge_verts`, etc.) survive.

## Per-Point Data Selection Pattern

With 2 torpedoes (point index 0 and 1), per-torpedo values are selected using:

```
Index → Compare(INT, EQUAL, B=0) → is_T1 (boolean)
Mix(Factor=is_T1, A=value_for_T2, B=value_for_T1) → per_point_value
```

Note: Mix node with factor=0 returns A, factor=1 returns B.

## Set Position Required After Simulation Zone

The Simulation Zone's `Position` state item tracks torpedo positions mathematically, but **does not move the actual geometry vertices**. A `Set Position` node must be added after the sim zone output.

## Position Initialization Pattern

On frame 1, the Simulation Zone's `Position` state item defaults to (0,0,0), not the actual vertex position. Fix with:

```
start_pos = Position node (reads actual vertex position from geometry)
pos_select = Mix(Factor=Active, A=start_pos, B=computed_new_pos)
```

When Active=0, torpedo stays at its geometry position. When Active=1, it uses the simulation-computed position.

## Visibility via Delete Geometry (Not hide_viewport/hide_render)

Do NOT use `obj.hide_viewport` or `obj.hide_render` for controlling torpedo visibility. Instead:
- Use **Delete Geometry** node in GeoNodes to output empty geometry when hidden
- Post-sim visibility filter: `Active * (1 - Arrived)` → invert → Delete Geometry selection
- For always-hidden objects (instance sources, markers), use a GeoNodes modifier that deletes all points

## Instancing with GeoNodes Primitives

When the instance source object has a GeoNodes modifier that modifies its geometry (e.g., an AlwaysHidden delete modifier), Object Info returns the post-modifier geometry (0 verts). Use a **Mesh UV Sphere** primitive node instead of referencing an external object for instancing.

## Speed Clamping with Safe Division

```
vel_len = Length(velocity)
clamped_len = Min(vel_len, MaxSpeed)
scale_factor = clamped_len / vel_len   # 0/0 = 0 in Blender (safe)
cap = Min(scale_factor, 1.0)           # safety cap
clamped_vel = Scale(velocity, cap)
```

Blender's Math DIVIDE returns 0 for 0/0, which is safe for the zero-velocity case.

## Arrival Latching

```
arrival_check = dist_to_target < threshold
Arrived = Maximum(previous_Arrived, arrival_check)  # once 1.0, stays 1.0
```

Same pattern for Active latching. MAXIMUM is the standard latch primitive in GeoNodes.

## Active/Arrived Velocity Masking

```
active_mask = Active * (1.0 - Arrived)
final_vel = Scale(clamped_vel, active_mask)
```

Zeros velocity when inactive (not yet launched) OR arrived (reached target).

## Linear Repulsor Falloff with Pass-Through Gate

```
away = torpedo_position - repulsor_position
dist = Length(away)
falloff = Max(0, 1 - dist / RepulsorRadius)
repulse_force = Normalize(away) * RepulsorStrength * falloff

# Gate: only apply repulsor when torpedo hasn't passed it yet
dist_rep_to_target = Length(target_pos - repulsor_pos)
gate = dist_to_target > dist_rep_to_target   # 1 if torpedo is farther, 0 if passed
gated_force = repulse_force * gate
```

The gate check (`dist(torpedo,target) > dist(repulsor,target)`) makes the repulsor behave like a shield — it deflects approaching torpedoes but has no effect once they've flown past it.

Linear falloff is smooth at the boundary and has no singularity at zero distance.

## Parameter Values That Worked

For a scene with ~770m torpedo-to-target distance, 50m torpedo spacing:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Attraction | 200.0 | Strong enough to overcome repulsor deflection |
| Max Speed | 150.0 | ~6.25 m/frame at 24fps |
| Initial Speed | 50.0 | Launch impulse toward target |
| Repulsor Strength | 100.0 | Lower than attraction to avoid orbiting |
| Repulsor Radius | 150.0 | Wide influence zone |
| Arrival Distance | 20.0 | Generous to account for trajectory offset |
| Torpedo Mesh Radius | 10.0 | Visible at scene scale |

Key ratio: **Attraction should be ~2× Repulsor Strength** to ensure torpedoes converge.

## Blender API Notes

- `ShaderNodeMath` operations: No `GREATER_EQUAL`. Use `GREATER_THAN` with adjusted threshold.
- `FunctionNodeCompare`: Supports `GREATER_THAN` for float, `EQUAL` for int. INT inputs at socket indices 2,3.
- `ShaderNodeMix`: `data_type='VECTOR'` for vector mixing. Vector inputs at indices 4 (A), 5 (B).
- Vector Math SCALE: Float input is **socket index 3**.
- Sim zone state items: use `'VECTOR'` not `'FLOAT_VECTOR'` for the `new()` call.
- `Object Info.transform_space = 'ORIGINAL'` for world positions.
- Render engine enum: `'BLENDER_EEVEE'` (not `'BLENDER_EEVEE_NEXT'`).
- `material.surface_render_method = 'BLENDED'` for EEVEE emission.

## Architecture: Final Working Design

```
TorpedoController (2-vertex mesh, one per torpedo)
  └── TorpedoEffect GeoNodes Modifier
        ├── Group Inputs: Attraction, Max Speed, Initial Speed,
        │   Repulsor Strength Base, Repulsor Radius, Arrival Distance, Torpedo Radius
        ├── Object Info nodes for Target1, Target2, Repulsor1 (INSIDE sim zone)
        │   → reads actual scene positions, updates when objects are moved
        ├── Scene Time for activation frame detection
        ├── Index + Compare + Mix for per-torpedo selection
        ├── Simulation Zone
        │     ├── State: Position, Velocity, Active, Arrived
        │     ├── Pass-through states: AttractionParam, MaxSpeedParam, InitialSpeedParam,
        │     │   RepStrengthBaseParam, RepRadiusParam, ArrivalDistParam
        │     ├── Physics: attraction + repulsion + speed clamping
        │     ├── Launch impulse on first active frame
        │     └── Active/Arrived masking
        ├── Set Position
        ├── Delete Geometry (inactive/arrived torpedoes)
        ├── Instance on Points (UV Sphere primitive, radius from Group Input)
        ├── Realize Instances
        └── Set Material (TorpedoEmission)
```

## Deviations from Plan

| Plan | Actual | Reason |
|------|--------|--------|
| Group Inputs directly to nodes | Group Inputs via state item pass-throughs | Group Input → sim zone interior nodes = silent zeros; pass-throughs work |
| Object Info outside sim zone | Object Info INSIDE sim zone | Works correctly when object ref is set directly on the node |
| Pass-through state items for params | Not needed | Object Info inside sim zone provides live positions |
| Repulsor computed outside sim zone | Computed inside sim zone | Object Info works inside, no need for external computation |
| TorpedoActivation modifier for launch | Scene Time frame checks | Simpler, no external modifier needed |
| Python frame handler for physics | Pure GeoNodes Simulation Zone | User requirement: no Python during simulation |
| hide_viewport/hide_render for visibility | Delete Geometry in GeoNodes | User requirement: visibility through geometry output only |
| Separate TorpedoVisual objects | Single TorpedoController outputs everything | User requirement: all visuals from one controller |
| External object for instance source | GeoNodes Mesh UV Sphere primitive | External object's geometry was modified by AlwaysHidden modifier |
