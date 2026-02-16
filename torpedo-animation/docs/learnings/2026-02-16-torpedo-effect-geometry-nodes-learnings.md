# Torpedo Effect — Geometry Nodes Learnings

## Date: 2026-02-16

## Summary

Built a Geometry Nodes torpedo simulation with target-seeking physics, repulsor avoidance, and emission visuals. Two torpedoes launch at different times, fly toward separate targets, one curves around a repulsor, both disappear on arrival.

## Critical Discovery: Group Input → Anything = Silent Zeros

The single most important learning from this project extends the silent-zeros bug documented in shield-animation:

**Group Input values do NOT propagate to ANY downstream node when the modifier has override values.** This affects:

1. **Group Input → Simulation Zone body nodes** (known from shield-animation)
2. **Group Input → Collection Info nodes** (NEW — discovered in this project)
3. **Group Input → any node when modifier overrides are set** (suspected — the bug may be universal)

The modifier panel shows correct override values (e.g., Attraction=100), but the Group Input node outputs the *interface default* (e.g., 5.0), not the modifier override. This means **all Group Input connections are unreliable.**

### Workaround: Hardcode Everything

For this project, every parameter and reference was hardcoded directly on node input sockets:

- **Collection references**: Set `Collection Info.inputs['Collection'].default_value = collection` directly (not through Group Input)
- **Float parameters**: Set directly as `node.inputs[N].default_value = value` on the consuming node
- **Object positions**: Use `Combine XYZ` nodes with hardcoded coordinates instead of `Object Info` nodes
- **Activation timing**: Use `Scene Time` node with hardcoded frame thresholds instead of external object scale reads

This eliminates Group Inputs entirely. The downside is that changing parameters requires editing the node tree (or Python script), not the modifier panel.

## Object Info Nodes Inside Simulation Zones

**Object Info nodes inside Simulation Zones produce incorrect/zero values.** Tested with both torpedo object references (for activation) and repulsor object references (for position). The Location output returned wrong coordinates.

### Workaround

Replace Object Info with hardcoded Combine XYZ nodes for known positions. For activation timing, use Scene Time frame checks instead of reading object scale.

## Scene Time Works Inside Simulation Zones

Unlike Group Inputs and Object Info, the **Scene Time** node produces correct Frame and Seconds values inside Simulation Zones. This makes it useful for frame-based activation triggers:

```
Scene Time[Frame] → Math(GREATER_THAN, threshold=9.5) → activation signal
```

Note: Math node has no `GREATER_EQUAL` operation. Use `GREATER_THAN` with threshold - 0.5.

## Simulation Zone Geometry is Self-Contained

The geometry entering a Simulation Zone on frame 1 becomes the sim zone's internal geometry for all subsequent frames. External changes to source objects (like Named Attributes updated by other modifiers) do NOT propagate into the sim zone after initialization.

This means:
- Named Attributes set by TorpedoActivation modifier on torpedo objects are **frozen at frame 1 values** inside the sim zone
- Any per-frame dynamic data must come from nodes that evaluate inside the sim zone (Scene Time, math operations on state items)

## Per-Point Data Selection Pattern

With 2 torpedoes (point index 0 and 1), per-torpedo values are selected using:

```
Index → Compare(INT, EQUAL, B=0) → is_T1 (boolean)
Mix(Factor=is_T1, A=value_for_T2, B=value_for_T1) → per_point_value
```

Note: Mix node with factor=0 returns A, factor=1 returns B. This is inverted from what you might expect.

## Set Position Required After Simulation Zone

The Simulation Zone's `Position` state item tracks torpedo positions mathematically, but **does not move the actual geometry vertices**. A `Set Position` node must be added after the sim zone output to apply the computed positions to the geometry.

## `to_mesh()` Returns Post-Modifier Geometry

When evaluating with `to_mesh()`:
- If the GeoNodes modifier produces instanced geometry (Instance on Points → Realize), `to_mesh()` returns the realized mesh with all instance vertices
- A UV Sphere with 16 segments × 8 rings = 114 vertices per instance
- 0 verts means the modifier is outputting empty geometry (useful for detecting broken node trees)

## Position Initialization Pattern

On frame 1, the Simulation Zone's `Position` state item defaults to (0,0,0), not the actual vertex position. This causes torpedoes to teleport to the origin on the first sim frame. Fix with:

```
start_pos = Position node (reads actual vertex position from geometry)
pos_select = Mix(Factor=Active, A=start_pos, B=computed_new_pos)
```

When Active=0, torpedo stays at its geometry position. When Active=1, it uses the simulation-computed position.

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
arrival_check = dist_to_target < threshold   # returns 0.0 or 1.0
Arrived = Maximum(previous_Arrived, arrival_check)  # once 1.0, stays 1.0
```

Same pattern for Active latching. MAXIMUM is the standard latch primitive in GeoNodes.

## Active/Arrived Velocity Masking

```
active_mask = Active * (1.0 - Arrived)
final_vel = Scale(clamped_vel, active_mask)
```

This zeros velocity when inactive (not yet launched) OR arrived (reached target).

## Linear Repulsor Falloff

```
away = torpedo_position - repulsor_position
dist = Length(away)
falloff = Max(0, 1 - dist / RepulsorRadius)
repulse_force = Normalize(away) * RepulsorStrength * falloff
```

Linear falloff is smooth at the boundary (no jitter) and has no singularity at zero distance. The force direction is always "away from repulsor."

## Parameter Values That Worked

For a scene with 600m torpedo-to-target distance, 50m torpedo spacing:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Attraction | 200.0 | Strong enough to overcome repulsor deflection |
| Max Speed | 150.0 | ~6.25 m/frame at 24fps |
| Initial Speed | 50.0 | Launch impulse toward target |
| Repulsor Strength | 100.0 | Lower than attraction to avoid orbiting |
| Repulsor Radius | 150.0 | Wide influence zone |
| Arrival Distance | 20.0 | Generous to account for trajectory offset |
| Torpedo Mesh Radius | 10.0 | Visible at 1400m scene scale |

Key ratio: **Attraction should be ~2× Repulsor Strength** to ensure torpedoes converge rather than orbit.

## Blender API Notes

- `ShaderNodeMath` operations: No `GREATER_EQUAL`. Use `GREATER_THAN` with adjusted threshold.
- `FunctionNodeCompare`: Supports `GREATER_THAN` for float comparison, `EQUAL` for int. Has separate input sockets for INT (indices 2,3) and FLOAT (A, B by name).
- `ShaderNodeMix`: `data_type='VECTOR'` for vector mixing, `'FLOAT'` for scalar. Inputs are `Factor`, `A`, `B`.
- Vector Math SCALE: Float input is **socket index 3** (not 1). Still true.
- `Collection Info.transform_space = 'ORIGINAL'` to get world positions from collection objects.
- Render engine enum: `'BLENDER_EEVEE'` (not `'BLENDER_EEVEE_NEXT'`).

## Deviations from Plan

| Plan | Actual | Reason |
|------|--------|--------|
| Group Inputs for parameters | All hardcoded on nodes | Group Input → node values are unreliable (silent zeros) |
| Collection Input through Group Input | Direct collection reference on Collection Info | Same bug — Group Input doesn't propagate collection overrides |
| Object Info for activation | Scene Time frame checks | Object Info produces wrong values inside Simulation Zone |
| Object Info for repulsor position | Hardcoded Combine XYZ | Same Object Info bug |
| Pass-through state items for params | Removed entirely | Not needed when values are hardcoded |
| TorpedoActivation modifier for activation | Kept but unused by main effect | Main effect uses Scene Time instead |
| Single shared target | Per-torpedo targets via Index+Mix | Required by 2-torpedo, 2-target setup |
| Repulsor computed outside sim zone | Hardcoded position inside sim zone | Object positions from outside don't propagate correctly |
