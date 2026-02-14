# Shield Ripple Effect — Learnings & Replication Notes

**Date:** 2026-02-14
**Original plan:** `docs/plans/2026-02-13-feat-shield-ripple-geometry-nodes-plan.md`

---

## 1. Summary of What Was Built

A Geometry Nodes–based shield ripple/shockwave effect on an existing Blender scene object (`ShieldsGeonode`), triggered by torpedo objects in a `Torpedoes` collection. The effect uses a **two-state wave equation** (Energy + Velocity) inside a Simulation Zone, producing **ring-shaped wavefronts** that expand outward and decay from impact points.

Two node groups were created:

1. **ShieldRippleEffect** — applied to `ShieldsGeonode`, implements the wave simulation
2. **TorpedoActivation** — applied to each torpedo mesh, writes a per-vertex `"active"` attribute based on the object's animated scale

---

## 2. Deviations from the Original Plan

### 2.1 Script vs. Direct MCP Manipulation

| Plan                                                                           | Actual                                                                                  |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| Single Python script (`shield_ripple_effect.py`) creating an entire test scene | Built directly in Blender via MCP on an **existing scene** with real production objects |

The plan assumed creating a standalone demo scene from scratch. In practice, the work was done on an existing `.blend` file with `ShieldsGeonode` (140k vert mesh, existing `ShieldAnimationV2` geo nodes, existing `"Shield"` material that reads `shockwave_intensity`). The script file was created but never used.

### 2.2 Heat Equation → Two-State Wave Equation

| Plan                                                                  | Actual                                                                    |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Blur Attribute diffusion (heat equation) producing **Gaussian blobs** | Two-state wave equation (Energy + Velocity) producing **ring wavefronts** |
| Single state item: `Energy`                                           | Two state items: `Energy` + `Velocity`                                    |
| Noted as "upgrade path, not in scope"                                 | Became necessary because blobs didn't look like shockwaves                |

The plan explicitly chose heat equation diffusion for simplicity and described ring wavefronts as a future enhancement. In practice, the user immediately asked for ring behavior ("the shockwave intensity just grows but doesn't decay and doesn't do the ring effect"), so the wave equation was implemented within the same session.

**Key formula inside the sim zone:**

```
laplacian = blur(energy) - energy
velocity += injection_impulse
velocity += laplacian * speed_scale
energy += velocity
energy *= energy_decay        # per-frame energy fade
velocity *= velocity_decay    # per-frame velocity fade
velocity *= (1 - damping)     # additional velocity damping
```

**Critical:** Energy must be allowed to go **negative inside the sim loop** for ring wavefronts to form. The Laplacian creates negative regions behind the wave crest. Only apply `abs()` + `clamp(0, 1)` at the **post-sim output stage**.

### 2.3 Activation Detection — Three Failed Approaches Before Success

The plan proposed: `Instance Scale → Vector Length → Compare → Delete Geometry → Realize Instances`.

| Approach                                                     | Result                        | Why It Failed                                                                                                                       |
| ------------------------------------------------------------ | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Instance Scale on Collection Info instances** (plan)       | Always returns `(1,1,1)`      | Blender normalizes instance transforms in Collection Info; scale is baked into the instance matrix, not readable via Instance Scale |
| **Object Info node (single torpedo)**                        | Works for one torpedo only    | Object Info takes a single object reference; can't iterate over a collection                                                        |
| **Python frame-change handler** writing `"active"` attribute | Works but **not persistent**  | Handler lives in session memory only; lost when Blender closes                                                                      |
| **TorpedoActivation GeoNode modifier** (final)               | Works, persistent, animatable | Uses `Self Object → Object Info → Scale → Store Named Attribute`                                                                    |

### 2.4 Empties Don't Work as Actuators

| Plan                                             | Actual                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| Single-vertex mesh objects (plan got this right) | Confirmed: Empties produce **no geometry** after Realize Instances |

The plan correctly anticipated this, but the existing scene had `Torpedo.001` as an Empty. It had to be converted to a single-vertex mesh via bmesh.

### 2.5 Layered Actions in Blender 4.x/5.x

| Plan                                        | Actual                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Standard `action.fcurves` API for keyframes | Blender 4.x+ uses **layered actions** with `action.layers[].strips[].channelbags[].fcurves` |

The plan's keyframe code (`action.fcurves`) doesn't work in Blender 4.x+. Layered actions have slots bound to specific objects — copying an action between objects doesn't automatically work because the slot's target ID doesn't match.

**Working keyframe access pattern for Blender 4.x+:**

```python
action = obj.animation_data.action
for layer in action.layers:
    for strip in layer.strips:
        for cb in strip.channelbags:
            for fc in cb.fcurves:
                for kfp in fc.keyframe_points:
                    print(f"{fc.data_path}[{fc.array_index}]: frame={kfp.co[0]}, value={kfp.co[1]}")
```

### 2.6 Parameters — More Exposed, Different Names

| Plan Parameter   | Plan Default | Actual Parameter       | Actual Default | Notes                                                                                   |
| ---------------- | ------------ | ---------------------- | -------------- | --------------------------------------------------------------------------------------- |
| Wave Speed       | 5            | Wave Speed             | 5              | Same — Blur Attribute iterations                                                        |
| Decay Rate       | 0.05         | **Energy Decay**       | 0.65           | Semantics changed: plan used `(1 - rate)` multiplication; actual uses direct multiplier |
| Injection Radius | 0.3          | Injection Radius       | 30.0           | Shield is ~241×497×128 units, not ~1 unit; scale differs by 100x                        |
| _(hardcoded)_    | 1.0          | **Injection Strength** | 2.0            | Exposed as parameter (plan hardcoded it)                                                |
| _(not in plan)_  | —            | **Damping**            | 0.20           | Velocity damping per frame — new parameter for wave equation                            |
| _(not in plan)_  | —            | **Velocity Decay**     | 0.70           | Additional velocity fade — new parameter for wave equation                              |

### 2.7 Attribute Name

| Plan              | Actual                  |
| ----------------- | ----------------------- |
| `"shield_energy"` | `"shockwave_intensity"` |

Changed to match the existing `"Shield"` material which already reads `"shockwave_intensity"`.

### 2.8 No Shader/Material Work

The plan included a full shader setup (Phase 3). In practice, the existing `"Shield"` material already had the shader nodes reading `"shockwave_intensity"`, so no shader work was needed.

### 2.9 Group Input Cannot Connect Directly into Sim Zone Body

The plan didn't mention this, but it's a critical discovery:

**Connecting Group Input sockets directly to Math nodes inside a Simulation Zone breaks the sim evaluation** — output becomes all zeros. The working pattern is to route values through the Simulation Zone as **pass-through state items**:

```
Group Input[param] → Simulation Input[ParamStateItem]
Simulation Input[ParamStateItem] → internal node (use the value)
Simulation Input[ParamStateItem] → Simulation Output[ParamStateItem]  (pass-through)
```

Interestingly, some connections work (e.g., `Group Input[Wave Speed] → Blur Attribute[Iterations]` inside the sim zone works fine). The exact conditions that trigger failure are unclear, but the state-item pass-through pattern is the reliable approach.

---

## 3. Blender API Gotchas Discovered

### 3.1 Instance Scale Always Returns (1,1,1)

`GeometryNodeInputInstanceScale` on instances created by `Collection Info` always returns `(1,1,1)`, regardless of the source object's actual animated scale. Both `transform_space='ORIGINAL'` and `'RELATIVE'` show the same behavior. This was confirmed with a diagnostic test showing `sqrt(1^2 + 1^2 + 1^2) = 1.7321` at all frames.

### 3.2 Blur Attribute Has No Geometry Input

The plan assumed Blur Attribute has a Geometry input socket. In Blender 4.x+, it only has `Value`, `Iterations`, and `Weight`. It operates on the context geometry from the Simulation Zone implicitly.

### 3.3 `display_type = 'PLAIN_AXES'` Invalid for Mesh Objects

Only valid for Empties. Mesh objects accept: `'BOUNDS'`, `'WIRE'`, `'SOLID'`, `'TEXTURED'`.

### 3.4 Capture Attribute Anonymous Attributes Don't Survive Realize Instances

Anonymous attributes created by `Capture Attribute` (for `is_active` on instance domain) don't reliably propagate through `Realize Instances`. Use `Store Named Attribute` with an explicit string name instead.

### 3.5 `FloatAttribute` Has No `foreach_get` in Some Contexts

When reading attributes from `evaluated_get()` meshes, `attr.data[i].value` works but `attr.foreach_get()` may not exist on the attribute object. Use the element-by-element approach for diagnostic scripts.

### 3.6 Node Link Removal Invalidates Python References

Removing a link via `ng.links.remove(link)` can invalidate other Python references to nodes/links in the same execution block (`StructRNA of type NodeLink has been removed`). Always re-fetch references after removal, or iterate over `list(ng.links)` copies.

---

## 4. Replication Guide

### 4.1 Prerequisites

- Blender 4.2+ (tested on 4.x with layered actions)
- A shield mesh object (any topology; uniform vertex density recommended)
- A collection of single-vertex mesh objects as torpedo/impact actuators
- Each actuator must have scale keyframes: rest scale `(0.1, 0.1, 0.1)`, active scale `(1.0, 1.0, 1.0)`, using `CONSTANT` interpolation
- The shield material should read a float attribute (e.g., `"shockwave_intensity"`) for emission/visibility

### 4.2 Create the TorpedoActivation Node Group

This goes on each actuator object. It reads the object's own scale and writes an `"active"` attribute.

**Nodes:**

```
Self Object → Object Info [Scale] → Vector Math (LENGTH) → Compare (GREATER_THAN, B=0.5) → Store Named Attribute ("active", FLOAT, POINT) → Group Output
Group Input [Geometry] → Store Named Attribute [Geometry]
```

**Interface:**

- Input: Geometry
- Input: Scale Threshold (Float, default=0.5)
- Output: Geometry

Apply this modifier to every actuator mesh in the collection.

### 4.3 Create the ShieldRippleEffect Node Group

Applied to the shield object.

**Interface inputs:**

| Name               | Type       | Default          | Description                   |
| ------------------ | ---------- | ---------------- | ----------------------------- |
| Geometry           | Geometry   | —                | Shield mesh                   |
| Impact Collection  | Collection | —                | Actuator collection           |
| Wave Speed         | Int        | 5 (1–15)         | Blur iterations per frame     |
| Damping            | Float      | 0.20 (0–1)       | Velocity damping              |
| Injection Radius   | Float      | 30.0             | Falloff distance              |
| Energy Decay       | Float      | 0.65 (0.01–0.99) | Per-frame energy multiplier   |
| Velocity Decay     | Float      | 0.70 (0.01–0.99) | Per-frame velocity multiplier |
| Injection Strength | Float      | 2.0 (0.1–10)     | Impact impulse magnitude      |

**Simulation Zone state items:**

- `Geometry` (GEOMETRY)
- `Energy` (FLOAT)
- `Velocity` (FLOAT)
- `EnergyDecayParam` (FLOAT) — pass-through for Energy Decay input
- `VelocityDecayParam` (FLOAT) — pass-through for Velocity Decay input
- `InjStrengthParam` (FLOAT) — pass-through for Injection Strength input

**Node chain (simplified):**

```
=== OUTSIDE SIM ZONE: Injection Pipeline ===

Collection Info [Impact Collection]
  → Realize Instances
  → Geometry Proximity (target=realized torpedoes, sample_position=Position)
  → Map Range (0..injection_radius → 1..0)     -- distance falloff
  → Multiply (× Sample Index of "active" attr)  -- per-torpedo gate
  → Switch (guard: Domain Size point_count > 0)  -- empty-collection guard
  → Multiply (× Injection Strength via sim state item)

=== INSIDE SIM ZONE: Wave Equation ===

1. Inject impulse:    velocity += injection
2. Laplacian:         lap = Blur(energy) - energy
3. Update velocity:   velocity += lap × speed_scale(2.0)
4. Update energy:     energy += velocity
5. Energy decay:      energy *= energy_decay_param
6. Velocity decay:    velocity *= velocity_decay_param
7. Velocity damping:  velocity *= (1 - damping)
8. Pass-through:      decay/strength params → sim output unchanged

=== POST-SIM: Output ===

abs(energy) → min(x, 1.0) → displacement (noise × energy × 0.3 along normals)
                            → Store Named Attribute "shockwave_intensity"
```

### 4.4 Per-Torpedo Activation Pipeline (Detail)

The key challenge: reading per-torpedo activation status on the shield mesh.

```
Realize Instances → Sample Nearest (from shield vertex positions to torpedo points)
                  → Sample Index (read "active" attribute at nearest torpedo index)
                  → Multiply with distance falloff → injection value per shield vertex
```

This gives each shield vertex an injection value based on:

1. **Distance** to the nearest torpedo (via Geometry Proximity → Map Range)
2. **Activation status** of that nearest torpedo (via Named Attribute "active" → Sample Nearest → Sample Index)

### 4.5 Tuning Guide

| Want                         | Adjust                                          |
| ---------------------------- | ----------------------------------------------- |
| Faster wave propagation      | Increase **Wave Speed** (more blur iterations)  |
| Wider initial impact         | Increase **Injection Radius**                   |
| Brighter initial flash       | Increase **Injection Strength**                 |
| Faster overall fade          | Decrease **Energy Decay** (e.g., 0.65 → 0.55)   |
| Shorter wave travel distance | Decrease **Velocity Decay** (e.g., 0.70 → 0.50) |
| Less oscillation/ringing     | Increase **Damping** (e.g., 0.20 → 0.40)        |
| Effect gone in ~25 frames    | Energy Decay ≈ 0.65, Velocity Decay ≈ 0.70      |
| Slower, lingering effect     | Energy Decay ≈ 0.85, Velocity Decay ≈ 0.85      |

**Decay math for single impact (approximate):**

- Frames until raw energy peak < 1.0: depends on Injection Strength
- With Injection Strength=2.0, Energy Decay=0.65, Velocity Decay=0.70: raw peak ≈ 1.6, drops below 1.0 in ~10 frames, reaches 0.025 at +25 frames

### 4.6 Adding a New Torpedo

1. Create a single-vertex mesh object (bmesh: `bm.verts.new((0,0,0))`)
2. Move it to the desired impact position on the shield surface
3. Add the `TorpedoActivation` modifier (node group already exists)
4. Add to the `Torpedoes` collection
5. Set rest scale to `(0.1, 0.1, 0.1)`
6. Keyframe scale: `(0.1)` → `(1.0)` for 1 frame → `(0.1)`, using CONSTANT interpolation
7. The shield ripple effect will automatically detect and respond to it

---

## 5. What Didn't Get Implemented (vs. Plan)

| Plan Feature                                         | Status                | Reason                                                   |
| ---------------------------------------------------- | --------------------- | -------------------------------------------------------- |
| `clear_scene()` / scene creation                     | Skipped               | Worked on existing scene                                 |
| `create_test_shield()` (boolean union of 2 spheres)  | Skipped               | Existing `ShieldsGeonode` mesh                           |
| `create_shield_material()` (full shader)             | Skipped               | Existing `"Shield"` material already reads the attribute |
| `setup_bloom_glow()` (EEVEE bloom + Cycles Glare)    | Skipped               | Not requested for this scene                             |
| `setup_demo_scene()` (camera, lights)                | Skipped               | Existing scene setup                                     |
| `setup_test_animation()` (3 impacts over 120 frames) | Partial               | 2 torpedoes with manually placed keyframes               |
| Energy floor clamp inside sim zone                   | Intentionally removed | Negative energy is required for ring wavefronts          |
| Capture Attribute for is_active                      | Abandoned             | Anonymous attributes don't survive Realize Instances     |
| Delete Geometry to filter inactive instances         | Replaced              | Named Attribute + Sample Nearest/Index approach          |

---

## 6. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ Torpedo.001 (single-vertex mesh, scale-animated)                    │
│   └─ TorpedoActivation modifier                                    │
│       Self Object → Object Info → Scale Length → Compare → Store    │
│       → writes "active" = 0.0 or 1.0 on the vertex                 │
├─────────────────────────────────────────────────────────────────────┤
│ Torpedo.002 (same setup)                                            │
├─────────────────────────────────────────────────────────────────────┤
│ Torpedoes collection (contains both torpedoes)                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                    Collection Info
                           │
                    Realize Instances
                     ┌─────┴─────┐
                     │           │
              Geometry     Named Attribute "active"
              Proximity    Sample Nearest → Sample Index
                     │           │
                  Map Range   activation gate
                  (distance    (per-torpedo
                   falloff)     on/off)
                     │           │
                     └─── × ─────┘
                           │
                    Injection value
                           │
┌──────────────── SIMULATION ZONE ───────────────────────────────────┐
│                                                                     │
│  velocity += injection                                              │
│  laplacian = blur(energy) - energy                                  │
│  velocity += laplacian × speed                                      │
│  energy += velocity                                                 │
│  energy *= energy_decay                                             │
│  velocity *= velocity_decay × (1 - damping)                        │
│                                                                     │
│  State: Energy (float), Velocity (float)                            │
│  Pass-through: EnergyDecayParam, VelocityDecayParam, InjStrength   │
│                                                                     │
└──────────────────────────┬─────────────────────────────────────────┘
                           │
                    abs(energy) → clamp(0,1)
                           │
              ┌────────────┴────────────┐
              │                         │
     Displacement               Store Named Attribute
     (noise × energy ×         "shockwave_intensity"
      0.3 along normals)              │
              │                         │
              └────────┬────────────────┘
                       │
                  Group Output
                       │
               ShieldsGeonode mesh
                       │
               "Shield" material
               (reads shockwave_intensity
                for emission + transparency)
```

---

## 7. Key Takeaways

1. **Instance Scale is unreliable** for reading animated object scales through Collection Info. Use a separate modifier on the source objects to write a named attribute instead.

2. **Two-state wave equation** (Energy + Velocity) is straightforward in Geometry Nodes and produces much better shockwave visuals than heat-equation diffusion. The key insight: energy must go negative inside the sim for rings to form.

3. **Sim Zone parameter routing** requires pass-through state items. Direct Group Input → sim body node connections can silently break evaluation.

4. **Layered actions** in Blender 4.x+ change the keyframe API significantly. Animation data is bound to object slots, not freely transferable.

5. **Self Object node** is the clean way to read an object's own properties in its Geometry Nodes modifier — no Python handler needed.

6. **Production scenes have different scales.** The plan assumed a ~1-unit shield; the actual shield was ~240×500 units. All radius/distance parameters needed 100x scaling.

7. **Iterative tuning is essential.** The decay parameters went through many iterations (energy_decay from 0.88 → 0.65, addition of velocity_decay node, injection strength from 1.0 → 7.0 → 2.0). Exposing these as modifier parameters was the right call.
