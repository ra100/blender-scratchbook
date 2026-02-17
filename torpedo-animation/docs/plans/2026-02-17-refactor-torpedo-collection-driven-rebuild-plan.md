---
title: "refactor: Torpedo animation collection-driven rebuild"
type: refactor
date: 2026-02-17
brainstorm: ../brainstorms/2026-02-17-torpedo-rethink-brainstorm.md
---

# Refactor: Torpedo Animation — Collection-Driven Rebuild

## Overview

Replace the hardcoded 2-torpedo `torpedo_physics_handler.py` (550-line monolithic builder with binary Mix chains) with a **data-driven, loop-based builder** that reads from Blender collections. Adding torpedoes becomes: add objects to collections → re-run script. No manual node wiring.

## Problem Statement

The current implementation hardcodes exactly 2 torpedoes via:
- Index == 0 comparisons + binary Mix nodes for per-torpedo selection (`torpedo_physics_handler.py:178-203`)
- Individual Object Info nodes per target, manually wired (`torpedo_physics_handler.py:151-161`)
- Launch frames baked into Mix node defaults (`torpedo_physics_handler.py:199-203`)
- Controller mesh vertex count must match torpedo count
- Single monolithic `build_torpedo_effect()` function with no sub-builders

This makes adding a 3rd torpedo require touching ~15 places in the code. The shield script already solved this scalability problem with sub-builders and collection patterns (`shield_ripple_effect.py:192-369`).

## Architecture Decision: Loop Builder vs Fixed Tree

The brainstorm explored a "fixed node tree topology" using Collection Info + Sample Index inside the Simulation Zone. Research revealed several blockers:

| Constraint | Impact on Fixed Tree |
|------------|---------------------|
| Empties produce no geometry after Realize Instances (AGENTS.md) | Launchpads (arrow empties) can't be read via Collection Info |
| Instance Scale always returns (1,1,1) (shield learnings) | Can't detect launchpad activation from collection instances |
| Sim Zone geometry freezes after frame 1 (AGENTS.md) | External named attributes don't update inside sim zone |
| Collection Info inside Sim Zones — untested | May hit same "silent zeros" as Group Input |

**Decision: Loop-based builder with Object Info per object.**

This approach:
- Reads collections at script time, generates the right number of Object Info nodes automatically
- Uses Object Info inside the Sim Zone (confirmed working with direct refs + `transform_space='ORIGINAL'`)
- Supports arrow empties as launchpads (Object Info reads position/rotation/scale from empties)
- Follows the shield script's helper function pattern (`shield_ripple_effect.py:39-59`)

The tree changes when you re-run the script, but the workflow is just as easy: add objects to collections → re-run script.

## Technical Design

### Scene Layout

```
Scene Collection
├── TorpedoController          (mesh: N vertices, one per launchpad)
│   └── GeoNodes modifier: "TorpedoEffect"
├── Launchpads                  (collection)
│   ├── LP.001                  (empty, display_type='ARROWS')
│   ├── LP.002                  (empty, display_type='ARROWS')
│   └── ...
├── Targets                     (collection)
│   ├── TGT.001                 (empty)
│   ├── TGT.002                 (empty)
│   └── ...
└── Repulsors                   (collection)
    ├── REP.001                 (any object type)
    └── ...
```

### Pairing Rule

**Launchpad[i] fires torpedo[i] at Target[i].** Parallel indexing by **alphabetical name sort** within each collection.

```python
launchpads = sorted(bpy.data.collections["Launchpads"].objects, key=lambda o: o.name)
targets = sorted(bpy.data.collections["Targets"].objects, key=lambda o: o.name)
```

- Number of torpedoes = `min(len(Launchpads), len(Targets))`
- Excess launchpads or targets ignored with a printed warning
- Each launchpad fires exactly once (single-use, edge-triggered)

### Activation Detection

Object Info inside the Sim Zone reads each launchpad's scale every frame:

```
Object Info[LP.001] → Scale → Vector Math(LENGTH) → Compare(GREATER_THAN, 0.5) → activation_signal
activation_signal → Maximum(previous_Active, activation_signal) → Active  (latched)
```

**Edge triggering** is inherent: the MAXIMUM latch means once Active=1, it stays 1 forever. The torpedo fires on the first frame the launchpad scale exceeds 0.5.

### Initial Velocity from Arrow Direction

On the first active frame, compute launch impulse from the launchpad's rotation:

```
Object Info[LP.i] → Rotation → Rotate Vector(base_forward=(0,1,0), rotation) → forward_dir
forward_dir * exit_velocity → initial_velocity
```

Arrow empties point along +Y in local space by default. The Rotate Vector node transforms this into world space using the empty's rotation.

**Launch impulse masking** — only apply on the transition frame (Active goes from 0→1):

```
launch_mask = Active_current - Active_previous  (1 on first frame, 0 after)
velocity += forward_dir * exit_velocity * launch_mask
```

### Per-Torpedo Selection: Cascading Mux Chain

Each torpedo's data (target position, launchpad scale, launchpad rotation) is routed to the correct vertex via a **generic cascading mux**:

```python
# _build_cascading_mux: called once per field (target pos, LP scale, LP rotation)
result_socket = default_socket
for i, per_torpedo_socket in enumerate(sockets):
    is_i = Compare(Index, EQUAL, i)
    mix = Mix(is_i, result_socket, per_torpedo_socket)
    result_socket = mix.outputs[1]
```

For N torpedoes, this creates N Compare + N Mix = 2N nodes per field, 3 fields = 6N selection nodes total.

### Repulsor Handling

Each repulsor gets an Object Info node inside the Sim Zone (loop-generated). Forces are summed:

```
For each repulsor R:
  away = torpedo_pos - R.position
  dist = Length(away)
  falloff = Max(0, 1 - dist / repulsor_radius)
  # Gate: only repulse if torpedo hasn't passed the repulsor yet
  dist_R_to_target = Length(target_pos - R.position)
  gate = dist_to_target > dist_R_to_target
  force_R = Normalize(away) * repulsor_strength * falloff * gate

total_repulsor_force = sum(force_R for all R)
```

This reuses the linear falloff + pass-gate pattern from the current implementation (`torpedo_physics_handler.py:366-393`).

**Note:** Scale-driven repulsor strength from the current code is **dropped**. Flat `repulsor_strength` parameter for all repulsors.

### Arrival Detection

Distance-based with overshoot handling:

```
dist_to_target = Length(target_pos - torpedo_pos)
arrived_check = dist_to_target < arrival_distance
Arrived = Maximum(previous_Arrived, arrived_check)  (latched)
```

On the arrival frame, torpedo position snaps to target position and **velocity is zeroed** to prevent single-frame jitter (defensive guard — torpedo is deleted the same frame, but this prevents a position pop if deletion ordering isn't perfect):

```
first_arrival = Arrived_current - Arrived_previous  (1 on transition frame only)
position = Mix(first_arrival, computed_position, target_position)  # snap
velocity = Mix(Arrived, computed_velocity, (0,0,0))  # zero on arrival
```

The `arrival_distance` parameter should be >= max_speed/fps to prevent tunneling.

### Attraction Distance Boost

Port from current code (`torpedo_physics_handler.py:255-275`): `effective_attraction = Attraction * (1 + RefDist / dist_to_target)` with RefDist=1000.0. This makes torpedoes curve more sharply near the target, preventing flyby oscillation. Internal constant, not exposed as Group Input:

```
ATTRACTION_REF_DISTANCE = 1000.0
boost = 1 + ATTRACTION_REF_DISTANCE / dist_to_target
effective_attraction = attraction * boost
```

### Node Tree Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ Group Inputs                                             │
│   Geometry, Exit Velocity, Attraction, Max Speed,        │
│   Repulsor Strength, Repulsor Radius, Arrival Distance,  │
│   Torpedo Radius                                         │
└──────────┬──────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────── Simulation Zone ──────────────────────────┐
│                                                                       │
│  State items: Position(V), Velocity(V), Active(F), Arrived(F),       │
│               + pass-through params: ExitVelParam, AttrParam,         │
│               MaxSpeedParam, RepStrParam, RepRadParam, ArrDistParam   │
│                                                                       │
│  ┌─ Object Info nodes (per launchpad) ──┐                            │
│  │  LP.001 → pos, rot, scale            │                            │
│  │  LP.002 → pos, rot, scale            │                            │
│  └──────────────────────────────────────┘                            │
│                                                                       │
│  ┌─ Object Info nodes (per target) ─────┐                            │
│  │  TGT.001 → pos                       │                            │
│  │  TGT.002 → pos                       │                            │
│  └──────────────────────────────────────┘                            │
│                                                                       │
│  ┌─ Object Info nodes (per repulsor) ───┐                            │
│  │  REP.001 → pos                       │                            │
│  └──────────────────────────────────────┘                            │
│                                                                       │
│  1. Cascading mux: LP scale → per-torpedo activation                 │
│  2. Activation latch (MAXIMUM) + launch mask                         │
│  3. Cascading mux: LP rotation → per-torpedo direction               │
│  4. Launch impulse (rotation → forward * exit_vel, masked)           │
│  5. Cascading mux: target pos → per-torpedo target                   │
│  6. Attraction force (with distance boost) + repulsor forces         │
│  7. Velocity integration + speed clamping                            │
│  8. Position update                                                   │
│  9. Arrival detection + position snap + velocity zero                │
│  10. Active/Arrived masking                                           │
│                                                                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────┐
│ Post-Sim Pipeline                                        │
│  1. Set Position (from Position state)                   │
│  2. Delete Geometry (inactive OR arrived)                │
│  3. Set Material on UV Sphere source mesh                │
│  4. Instance on Points (pre-materialed UV Sphere)        │
│  (NO Realize Instances — keep as instances for perf)     │
└─────────────────────────────────────────────────────────┘
```

### Script Structure

Follow the shield script pattern: plain helper functions with `nodes` and `links` passed directly. No wrapper class.

```python
torpedo_physics_handler.py

# --- Constants ---
NODE_GROUP_NAME = "TorpedoEffect"
LAUNCHPAD_COLLECTION = "Launchpads"
TARGET_COLLECTION = "Targets"
REPULSOR_COLLECTION = "Repulsors"
ACTIVATION_THRESHOLD = 0.5
ATTRACTION_REF_DISTANCE = 1000.0

# --- Helpers (same pattern as shield_ripple_effect.py:39-59) ---
_add_node(nodes, type_str, label, location) → node
_add_math_node(nodes, operation, label, location) → node
_link(links, from_socket, to_socket)

# --- Validation ---
_validate_collections() → (launchpads, targets, repulsors)
    Fail-fast with RuntimeError. Sorts by name.

# --- Node helpers ---
_create_object_info_nodes(nodes, objects, label_prefix, x, y_start, y_step)
    → list of Object Info nodes (loop-generated, direct refs, ORIGINAL space)

_build_cascading_mux(nodes, links, per_torpedo_sockets, data_type, label_prefix, x_offset)
    → per_point_socket  (generic Index+Compare+Mix chain)

_build_latch(nodes, links, check_socket, prev_socket, label, location)
    → latched_socket  (MAXIMUM pattern, reused for Active and Arrived)

# --- Sub-builders ---
_build_launch(nodes, links, lp_scale_sockets, lp_rotation_sockets,
              exit_vel_socket, prev_active_socket, prev_velocity_socket, x_offset)
    → active_socket, launch_mask_socket, initial_velocity_socket

_build_repulsor_forces(nodes, links, position_socket, target_pos_socket,
                       repulsor_infos, rep_strength_socket, rep_radius_socket, x_offset)
    → total_repulsor_force_socket

_build_velocity_integration(nodes, links, velocity_socket, position_socket,
                            target_pos_socket, attraction_socket, repulsor_socket,
                            active_socket, arrived_socket, max_speed_socket, x_offset)
    → clamped_velocity_socket, new_position_socket
    # Includes attraction force with distance boost inline

_build_arrival_detection(nodes, links, position_socket, target_pos_socket,
                         arrival_dist_socket, prev_arrived_socket, x_offset)
    → arrived_socket, final_position_socket, final_velocity_socket

_build_visual_output(nodes, links, geo_socket, position_socket,
                     active_socket, arrived_socket, torpedo_radius_socket, material, x_offset)
    → final_geometry_socket
    # Set Position → Delete Geometry → Set Material on sphere → Instance on Points (NO Realize)

# --- Scene functions ---
_ensure_clean_node_group(name) → node_group  (idempotent: removes old if exists)
_create_controller_mesh(num_vertices) → obj  (idempotent: replaces mesh data if exists)
_create_torpedo_material() → material
setup_test_scene(num_launchpads=4) → sets up demo empties + keyframes

# --- Main builder ---
build_torpedo_effect(launchpads, targets, repulsors)
    → creates node group, wires everything, applies modifier

# --- Entry point ---
main()
    → validate → build → apply
```

**Re-run:** Delete existing node group, replace controller mesh data in-place via bmesh, rebuild from scratch, reattach modifier. User overrides on modifier params reset to defaults (acceptable).

### Key API Gotchas

From AGENTS.md and learnings (all apply to this rebuild):

| # | Gotcha | Where it applies |
|---|--------|-----------------|
| 1 | Group Input values don't propagate inside Sim Zone | All physics params must use pass-through state items |
| 2 | Object Info: set ref directly, `transform_space='ORIGINAL'` | All Object Info nodes for LP/TGT/REP |
| 3 | Vector Math SCALE float input = socket index 3 | Speed clamping, force scaling |
| 4 | Sim Zone state items: use `'VECTOR'` not `'FLOAT_VECTOR'` | State item creation |
| 5 | No `GREATER_EQUAL` — use `GREATER_THAN` with -0.5 offset | Activation detection |
| 6 | Set Position required after Sim Zone | Post-sim pipeline |
| 7 | Delete Geometry for visibility, not hide_viewport | Inactive/arrived torpedo filtering |
| 8 | ShaderNodeMix: A=index 4, B=index 5, factor=0→A, factor=1→B | Per-torpedo selection chain |
| 9 | FunctionNodeCompare INT inputs: socket indices 2 and 3 | Index comparison |
| 10 | Math DIVIDE returns 0 for 0/0 | Safe for zero-velocity clamping |
| 11 | Node link removal invalidates Python refs — iterate copies | If clearing existing tree |
| 12 | Blender 4.x layered actions for keyframes | Scale keyframing on launchpads |
| 13 | State items must be created on sim_output, then appear on sim_input | Simulation Zone setup |
| 14 | Blender uses `node_group.interface.new_socket()` in 4.0+ | Not the old `inputs.new()` API |

## Acceptance Criteria

- [x] Script reads Launchpads, Targets, Repulsors collections and generates node tree automatically
- [x] Adding a launchpad + target to collections + re-running script adds a torpedo
- [x] Removing objects from collections + re-running script removes torpedoes
- [x] Torpedoes launch when launchpad scale becomes 1 (keyframed)
- [x] Torpedoes launch in the direction the arrow empty points
- [x] Each torpedo tracks its paired target with attraction force (with distance boost)
- [x] Repulsor objects deflect approaching torpedoes (linear falloff, pass-gate)
- [x] Torpedoes snap to target and disappear on arrival (velocity zeroed)
- [x] Physics parameters tunable in modifier UI (exit velocity, attraction, max speed, repulsor strength/radius, arrival distance, torpedo radius)
- [x] Re-running script on existing scene preserves collections, rebuilds node tree
- [x] No hardcoded torpedo count anywhere in the script
- [x] Validation function raises RuntimeError for missing/empty collections

**Out of scope:** If objects are deleted from collections after the node tree is built, behavior is undefined. Re-run the script to rebuild.

## Implementation Phases

### Phase 1: Skeleton + Scene Setup

- [x] Define module constants and helper functions (`_add_node`, `_add_math_node`, `_link`)
- [x] Implement `_validate_collections()`, `_ensure_clean_node_group()`, `_create_controller_mesh()`
- [x] Implement `_create_object_info_nodes()`, `_build_cascading_mux()`, `_build_latch()`
- [x] Create `build_torpedo_effect()` skeleton: node group, Group Interface, Simulation Zone with state items, pass-through params
- [x] `setup_test_scene()`: create collections, 4 arrow empties, 4 target empties, 1-2 repulsors, keyframe launchpad scales
- [x] Verify: modifier shows in Blender UI with tunable params

### Phase 2: Launch + Attraction + Arrival

- [x] `_build_launch`: Object Info per launchpad → cascading mux → activation latch → launch mask → impulse
- [x] `_build_velocity_integration`: cascading mux for target pos → attraction (with boost) → force sum → clamping → position update
- [x] `_build_arrival_detection`: distance check → MAXIMUM latch → position snap → velocity zero
- [x] Verify: torpedoes launch at correct frame/direction, curve toward targets, stop cleanly

### Phase 3: Repulsors + Visuals

- [x] `_build_repulsor_forces`: Object Info per repulsor → linear falloff + pass-gate → sum forces
- [x] Wire repulsor forces into velocity integration
- [x] `_build_visual_output`: Set Position → Delete Geometry → Set Material on UV Sphere → Instance on Points (NO Realize)
- [x] Verify: torpedoes deflect around repulsors, only active in-flight torpedoes visible

### Phase 4: Integration Test

- [x] Test with 4 launchpads, 4 targets, 1-2 repulsors
- [x] Verify staggered launches (different activation frames)
- [ ] Verify moving targets (keyframe target positions)
- [x] Test re-run after adding/removing objects from collections
- [x] Test timeline scrub (rewind to frame 1, replay)
- [ ] Tune physics params for visual quality

## Edge Cases

| Edge Case | Decision |
|-----------|----------|
| More launchpads than targets | `min(len(LP), len(TGT))` torpedoes. Excess ignored with warning. |
| Empty or missing collections | `_validate_collections()` raises RuntimeError. |
| Launchpad scale stays at 1 | No re-trigger — MAXIMUM latch is monotonic. One launch per pad. |
| Target moves after launch | Torpedo tracks live (Object Info updates each frame inside Sim Zone). |
| Two launchpads activate same frame | Both fire. Order = alphabetical name sort. Deterministic. |
| Controller mesh has excess vertices | Post-sim Delete Geometry guard: `Index >= N`. |

## References

### Internal

- Current torpedo script: `torpedo-animation/torpedo_physics_handler.py`
- Shield helpers: `shield-animation/shield_ripple_effect.py:39-59`
- Shield sub-builders: `shield-animation/shield_ripple_effect.py:192-369`
- Shield collection/modifier pattern: `shield-animation/shield_ripple_effect.py:486-490`
- API gotchas: `AGENTS.md:38-55`
- Torpedo learnings: `torpedo-animation/docs/learnings/2026-02-16-torpedo-effect-geometry-nodes-learnings.md`
- Shield learnings: `shield-animation/docs/learnings/2026-02-14-shield-ripple-geometry-nodes-learnings.md`
- Brainstorm: `torpedo-animation/docs/brainstorms/2026-02-17-torpedo-rethink-brainstorm.md`
- Attraction boost: `torpedo-animation/torpedo_physics_handler.py:255-275`

### Physics Param Baseline (from learnings, ~770m range)

| Parameter | Value |
|-----------|-------|
| Exit Velocity | 50.0 |
| Attraction | 200.0 |
| Max Speed | 150.0 |
| Repulsor Strength | 100.0 |
| Repulsor Radius | 150.0 |
| Arrival Distance | 20.0 |
| Torpedo Radius | 10.0 |
