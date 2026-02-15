---
title: "feat: Torpedo Effect — Guided Projectile with Glow & Avoidance"
type: feat
date: 2026-02-15
---

# Torpedo Effect — Guided Projectile with Glow & Avoidance

## Overview

Build a pure Geometry Nodes system for glowing, target-seeking torpedo projectiles with obstacle avoidance. Visuals (emission shader) and trajectory physics (attraction, repulsion, speed clamping) live in a single Simulation Zone. No Python handlers — fully persistent in the .blend file.

Based on brainstorm: `torpedo-animation/docs/brainstorms/2026-02-15-torpedo-effect-brainstorm.md`

## Proposed Solution

A single Geometry Nodes modifier on a controller object (single-vertex mesh) that:

1. Reads torpedo start positions from a **Torpedoes** collection (single-vertex mesh objects)
2. Simulates per-torpedo Position + Velocity in a Simulation Zone
3. Applies target attraction + repulsor avoidance forces each frame
4. Instances a small emissive sphere at each torpedo's position

All tunable parameters exposed as modifier inputs, routed through the Simulation Zone as **pass-through state items** (learned constraint from shield-animation).

## Technical Approach

### Architecture

```
Each torpedo start object (single-vertex mesh):
  └── GeoNodes Modifier: "TorpedoActivation"
        Self Object → Object Info → Scale → Length → Compare (> threshold)
        → Store Named Attribute "active" (FLOAT, POINT)

Controller Object (single-vertex mesh):
  └── GeoNodes Modifier: "TorpedoEffect"
        ├── Group Inputs (parameters)
        ├── Collection Info → torpedo start positions + "active" attribute
        ├── Object Info (Target) → target position
        ├── Repulsor force computation (OUTSIDE sim zone)
        ├── Simulation Zone
        │     ├── State: Position (VECTOR per torpedo)
        │     ├── State: Velocity (VECTOR per torpedo)
        │     ├── State: Active (FLOAT — read from "active" attr, latches launch)
        │     ├── State: Arrived (FLOAT — 1.0 when reached target)
        │     ├── Pass-through states for parameters
        │     ├── Physics: attraction + repulsion + clamping (only when Active & !Arrived)
        │     └── Position update
        ├── Instance on Points (torpedo mesh) — only Active & !Arrived torpedoes
        └── Output geometry
```

**Per-torpedo launch timing:** Each torpedo start object's **scale** controls when it launches. Keyframe scale from 0 → 1 on the frame you want that torpedo to fire. The TorpedoActivation modifier (same proven pattern from shield-animation) reads the scale and writes an "active" named attribute. The main TorpedoEffect modifier reads this attribute to decide when to start simulating each torpedo.

**Arrival behavior:** When a torpedo reaches the target (within arrival distance), `Arrived` latches to 1.0. Arrived torpedoes are **not instanced** — they disappear (light goes off). No freeze, no lingering geometry.

### Simulation Zone State Items

Core states use bare names; pass-through parameters use the `Param` suffix to distinguish simulation state from routed inputs. `Arrived` is FLOAT (not boolean) because Simulation Zone state items don't support boolean type.

| State Item              | Type   | Purpose                                     |
| ----------------------- | ------ | ------------------------------------------- |
| Position                | VECTOR | Current world position of each torpedo      |
| Velocity                | VECTOR | Current velocity vector                     |
| Active                  | FLOAT  | 1.0 once torpedo has launched (latched from "active" attr) |
| Arrived                 | FLOAT  | 1.0 when within arrival threshold of target |
| AttractionParam         | FLOAT  | Pass-through for Attraction strength        |
| MaxSpeedParam           | FLOAT  | Pass-through for Max Speed                  |
| InitialSpeedParam       | FLOAT  | Pass-through for Initial Speed              |
| RepulsorStrengthParam   | FLOAT  | Pass-through for Repulsor Strength          |
| RepulsorRadiusParam     | FLOAT  | Pass-through for Repulsor Radius            |
| TargetPosParam          | VECTOR | Pass-through for target world position      |
| RepulsorForceParam      | VECTOR | Pass-through for pre-computed repulsor force (computed outside sim zone) |

**Parameter routing pattern** (from shield-animation learnings):
```
Group Input[Attraction] → Sim Zone Input[AttractionParam]
                          Sim Zone Input[AttractionParam] → (internal physics nodes)
                          Sim Zone Input[AttractionParam] → Sim Zone Output[AttractionParam]
```

This avoids the silent-zeros bug where Group Inputs connected directly to Simulation Zone body nodes produce all zeros.

### Repulsor Force: Computed Outside the Simulation Zone

**Critical constraint:** Collection references fed to Collection Info nodes inside the Simulation Zone will likely hit the same silent-zeros bug as Group Inputs. The repulsor force must be computed **outside** the sim zone.

**Pattern:**
```
Group Input[Repulsors] → Collection Info → Realize Instances
  → Geometry Proximity (to torpedo positions from previous frame)
  → Compute repulse_force vector per torpedo
  → Pass repulse_force into Sim Zone as RepulsorForceParam state item
```

Inside the sim zone, the pre-computed `RepulsorForceParam` is simply added to the attraction force. This mirrors the injection pipeline pattern from the shield-animation.

### Delta Time

The Simulation Zone's built-in **Delta Time** output socket (on the Simulation Zone input node) provides the per-frame time step. All velocity/position updates multiply by this value. Do not use Scene Time for this — it gives absolute seconds, not per-frame delta.

### Per-Frame Physics (inside Simulation Zone)

```
# Read "active" named attribute from geometry (set by TorpedoActivation modifier)
active_now = Named Attribute "active"

# Latch Active state: once a torpedo launches, it stays active forever
Active = max(Active, active_now)

# Skip physics if not yet launched
if Active == 0.0:
    Position = start_position  (stay at spawn point)
    Velocity = (0,0,0)
    → skip to output

# Skip physics if torpedo has arrived at target
if Arrived == 1.0:
    → skip to output (torpedo will not be instanced — it's gone)

# On first frame of activation (Active just became 1.0):
#   Initialize Velocity = normalize(TargetPos - Position) × InitialSpeedParam
# This gives the torpedo a launch impulse toward the target.
# Detect via: Active == 1.0 AND length(Velocity) == 0.0

# 1. Target attraction
to_target = TargetPosParam - Position
dist_to_target = length(to_target)
attraction_force = normalize(to_target) × AttractionParam

# 2. Check arrival (distance threshold = 0.5 hardcoded)
if dist_to_target < 0.5:
    Arrived = 1.0  (latched via MAXIMUM with previous Arrived)
    → skip force application

# 3. Repulsor avoidance (pre-computed outside sim zone)
repulse_force = RepulsorForceParam  # already computed

# 4. Update velocity
Velocity += (attraction_force + repulse_force) × delta_time
Velocity = clamp_length(Velocity, MaxSpeedParam)

# 5. Update position
Position += Velocity × delta_time
```

### Repulsor Force Calculation (outside Simulation Zone)

Uses linear falloff instead of inverse-square — simpler, no singularity at zero distance, visually similar:

```
away = torpedo_position - nearest_repulsor_position
dist = length(away)
falloff = max(0, 1.0 - dist / RepulsorRadiusParam)
repulse_force = normalize(away) × RepulsorStrengthParam × falloff
```

When `dist >= RepulsorRadiusParam`, force is zero (smooth, no hard cutoff jitter). When `dist = 0`, force is `RepulsorStrengthParam` (no divide-by-zero). MVP uses nearest single repulsor only.

### Implementation Gotchas

These are the non-obvious details to watch for during MCP build:

- **Vector Math SCALE**: float input is socket index 3, not index 1
- **Active/Arrived latching**: use Math MAXIMUM(previous_state, new_state) — once latched, stays latched
- **Arrived velocity mask**: Mix (Vector) with factor=Arrived, A=clamped_velocity, B=(0,0,0) — factor=1 returns B
- **Zero velocity normalize**: `normalize((0,0,0))` returns `(0,0,0)` in Blender (safe, not NaN)
- **Launch impulse**: On the first active frame, Velocity is (0,0,0). Detect this to apply InitialSpeed as `normalize(target - pos) × InitialSpeed`. After that frame, Velocity > 0 so the impulse doesn't re-fire.
- **Instancing filter**: After sim zone, use `Active AND NOT Arrived` to control which torpedoes get instanced. Inactive torpedoes are invisible (not yet launched). Arrived torpedoes are invisible (hit target, light off).

### Modifier Parameters (Group Inputs)

| Parameter         | Type       | Default | Description                            |
| ----------------- | ---------- | ------- | -------------------------------------- |
| Torpedoes         | Collection | —       | Collection of torpedo start objects    |
| Target            | Object     | —       | Target object to seek                  |
| Repulsors         | Collection | —       | Collection of obstacle objects         |
| Attraction        | Float      | 5.0     | Target-seeking force strength          |
| Max Speed         | Float      | 10.0    | Maximum velocity magnitude             |
| Initial Speed     | Float      | 2.0     | Launch impulse speed toward target     |
| Repulsor Strength | Float      | 50.0    | Avoidance force multiplier             |
| Repulsor Radius   | Float      | 5.0     | Influence distance of repulsors        |

**Hardcoded values** (promote to parameters if tuning demands it):
- Arrival distance: 0.5
- Torpedo color: set directly in material (blue-white default)
- Emission strength: 15.0 (in material)

### Material Setup

**Torpedo emission material** ("TorpedoEmission"):

```
RGB (0.5, 0.7, 1.0) → Emission Shader [Color]
Value (15.0) → Emission Shader [Strength]
Emission Shader → Material Output [Surface]
```

- `surface_render_method = 'BLENDED'` for EEVEE
- Pure emission — no Principled BSDF
- Rely on compositor bloom/glare for the characteristic glow halo
- Brightness oscillation and per-torpedo color can be added later as visual polish

## Implementation Phases

### Phase 1: Physics + Basic Visuals

Build the complete torpedo flight system with a single torpedo, no repulsors.

**Build:**
- Scene setup: controller object, Torpedoes collection with single-vertex mesh start object, target object
- TorpedoActivation modifier on each torpedo start object (Self Object → Scale → "active" attribute)
- GeoNodes modifier with Group Inputs (Target, Attraction, Max Speed, Initial Speed)
- Simulation Zone with Position, Velocity, Active, Arrived states + parameter pass-throughs
- Activation detection (read "active" attr, latch Active state)
- Launch impulse (InitialSpeed toward target on first active frame)
- Target attraction → velocity update → speed clamping → position update → arrival detection
- Instance on Points with visibility filter: only `Active AND NOT Arrived` torpedoes
- TorpedoEmission material

**Validate:** Keyframe torpedo scale from 0→1 at frame 10. Torpedo should appear at frame 10, fly toward target with initial impulse, then disappear on arrival.

### Phase 2: Repulsor Avoidance

Add repulsor force computation (outside sim zone) and wire into the physics.

**Build:**
- Add Repulsors collection, Repulsor Strength, Repulsor Radius Group Inputs
- Collection Info → Realize Instances → Geometry Proximity (outside sim zone)
- Linear falloff force calculation
- Pass RepulsorForceParam into sim zone
- Add to attraction force inside sim zone

**Validate:** Place repulsor between torpedo and target — torpedo curves around it smoothly. Multiple torpedoes from collection all avoid the repulsor independently.

## Decisions on Open Questions

| Question | Decision | Rationale |
| --- | --- | --- |
| Arrival behavior | Torpedo disappears (not instanced) | Arrived=1.0 excludes from instancing — light goes off cleanly |
| Per-torpedo launch timing | Scale-based activation (keyframeable) | TorpedoActivation modifier reads object scale; keyframe scale 0→1 to fire. Proven pattern from shield-animation. |
| Initial speed | Parameter (default 2.0) | Launch impulse toward target on first active frame; needed for natural-looking launch |
| Trail | Cut from plan | YAGNI — add as separate plan if base effect works well |
| Multiple targets | Single shared target | Per-torpedo targets need vector attributes on collection objects; defer |
| Point lights | Skip — use emission + bloom | GeoNodes can't instance light objects; emission is sufficient |
| Repulsor falloff | Linear instead of inverse-square | No singularity, smooth boundary, simpler nodes |

## Known Constraints & Workarounds

From `AGENTS.md` and shield-animation learnings:

| Constraint | Workaround in This Project |
| --- | --- |
| Group Input → Sim Zone body = silent zeros | Pass-through state items for all parameters |
| Collection Input → Sim Zone body = likely silent zeros | Compute repulsor force OUTSIDE sim zone, pass result in as vector state item |
| Instance Scale always (1,1,1) | Use Store Named Attribute if per-torpedo scale needed |
| Empties → no geometry after Realize | Torpedo start objects must be single-vertex meshes (bmesh) |
| Anonymous attrs don't survive Realize | Use Store Named Attribute with explicit string names |
| Vector Math SCALE float = socket index 3 | Verify socket index in all SCALE connections |
| Node link removal invalidates refs | Iterate over `list(ng.links)` copies |
| Delta time source | Use Simulation Zone's built-in Delta Time output, not Scene Time |

## MCP Build Strategy

Build via `mcp__blender__execute_blender_code` in small chunks. Each chunk produces a testable result. Re-fetch node references between chunks.

1. **Scene setup:** Create controller object, Torpedoes collection with single-vertex mesh start objects, target object, Repulsors collection. Apply TorpedoActivation modifier to each torpedo start object. Add scale keyframes for launch timing.
2. **Node tree skeleton:** GeoNodes modifier, Group Inputs, Simulation Zone with all state items (Position, Velocity, Active, Arrived + param pass-throughs), parameter routing wired
3. **Physics:** Activation detection + launch impulse + target attraction + velocity update + speed clamping + arrival detection — all in one chunk (these are interdependent)
4. **Repulsors:** Collection Info + Realize outside sim zone, Geometry Proximity, linear falloff force, wire RepulsorForceParam into sim zone
5. **Instancing + visuals:** Set Position, filter points by `Active AND NOT Arrived`, Instance on Points, TorpedoEmission material creation + assignment

## References

- Shield animation learnings: `shield-animation/docs/learnings/2026-02-14-shield-ripple-geometry-nodes-learnings.md`
- Shield animation plan (Sim Zone patterns): `shield-animation/docs/plans/2026-02-13-feat-shield-ripple-geometry-nodes-plan.md`
- Torpedo brainstorm: `torpedo-animation/docs/brainstorms/2026-02-15-torpedo-effect-brainstorm.md`
- AGENTS.md (API gotchas): `AGENTS.md`
