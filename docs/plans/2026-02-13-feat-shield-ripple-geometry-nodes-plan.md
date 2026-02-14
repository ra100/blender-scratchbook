---
title: "feat: Shield Ripple Effect via Geometry Nodes Simulation Zone"
type: feat
date: 2026-02-13
deepened: 2026-02-13
brainstorm: docs/brainstorms/2026-02-13-shield-ripple-effect-brainstorm.md
---

# Shield Ripple Effect via Geometry Nodes Simulation Zone

## Enhancement Summary

**Deepened on:** 2026-02-13
**Research agents used:** Geometry Nodes Skill, Shader Nodes Skill, Python Scripting Skill, Animation Skill, Architecture Strategist, Performance Oracle, Simplicity Reviewer, Python Code Reviewer, Best Practices Researcher

### Key Improvements

1. **Critical API corrections** — Fixed `NodeSocketFloatFactor` (use `NodeSocketFloat` + min/max), Collection Info `transform_space = 'RELATIVE'`, Capture Attribute `capture_items.new()` API, Vector Math SCALE socket index (3 not 1)
2. **Performance calibration** — Blur Attribute is O(E) not O(V) per iteration (E ≈ 3V). Corrected voxel size from 0.05 to 0.035 for ~15k-20k verts. Capped Wave Speed max at 15. Estimated 12-20 FPS at 50k verts / 5 iterations.
3. **Missing bloom/glow** — Added EEVEE bloom and Cycles compositor Glare node configuration for characteristic sci-fi energy halo
4. **Python code quality** — Split `create_geometry_nodes()` into sub-builders, add helper functions, version guards, `if __name__ == "__main__"` guard, bmesh for single-vertex creation
5. **Parameter simplification** — Cut 3 parameters (Injection Strength → hardcoded 1.0, Noise Scale → hardcoded 5.0, Displacement Strength → hardcoded 0.05) reducing interface complexity

### Corrections Applied

- **Injection Radius inconsistency:** Standardized to 0.3 (was 0.5 in interface table, 0.3 in defaults table)
- **Wave Speed max:** Reduced from 30 to 15 (>15 causes blurring artifacts, not faster propagation)
- **Voxel size:** Changed from 0.05 to 0.035 (0.05 produces only ~5k-9k verts, insufficient for smooth waves)
- **Performance estimates:** Corrected from "1M operations" to "~3M edge evaluations" for 50k verts at 20 iterations
- **Energy floor check:** Removed (shader Color Ramp handles near-zero values; adds unnecessary nodes)

### Upgrade Path (Not In Scope)

- **Ring wavefronts:** Two-State Wave Equation (stores velocity + energy) can produce ring-shaped waves instead of diffusion blobs. The current diffuse/soft choice matches the brainstorm decision. Document as future enhancement.

---

## Overview

A Python script that generates a complete Blender scene with an animated sci-fi shield ripple effect. When impact markers are activated (via scale keyframes), energy waves propagate across the shield surface using a Simulation Zone, creating organic vertex displacement and emission glow that fades over time. The shield is invisible until hit, with a subtle Fresnel edge hint.

The deliverable is a single Python script (`shield_ripple_effect.py`) runnable in Blender's text editor or via command line. It creates all geometry, the geometry nodes modifier, the shader material, and a test animation.

## Problem Statement / Motivation

Sci-fi shield impact effects are a common VFX need. The challenge is creating surface-following wave propagation that works on arbitrary mesh topology (including non-convex shapes). Euclidean distance-based approaches shortcut through concave geometry. A Simulation Zone with per-vertex energy diffusion via Blur Attribute solves this by propagating along actual mesh connectivity.

### Research Insights: Wave Propagation Approaches

| Approach | Wavefront Shape | Complexity | Geodesic Fidelity |
|----------|----------------|------------|-------------------|
| **Blur Attribute diffusion** (chosen) | Gaussian blob (diffuse/soft) | Low — single node | Excellent — follows edges |
| Two-State Wave Equation | Ring wavefront | High — 2 attributes, more math | Excellent — follows edges |
| Distance field + shader | Ring or blob | Medium — no sim zone needed | Poor — Euclidean shortcuts |

Blur Attribute implements the heat equation (discrete Laplacian diffusion). Each iteration spreads energy to adjacent vertices weighted by edge connectivity. This produces expanding Gaussian blobs, not ring wavefronts — which matches the brainstorm's "diffuse/soft" specification. If ring-shaped waves are desired later, a Two-State Wave Equation storing both `energy` and `velocity` per vertex can be implemented inside the same Simulation Zone architecture.

## Proposed Solution

A Python script (`shield_ripple_effect.py`) with four components:

1. **Scene setup** - Test shield geometry (2 merged spheres), impact marker objects, collection hierarchy
2. **Geometry Nodes modifier** - Simulation Zone with energy injection, diffusion (Blur Attribute), exponential decay, vertex displacement, and attribute output
3. **Shield shader** - Transparent + Emission mix driven by the energy attribute, plus subtle Fresnel edge
4. **Test animation** - Pre-keyframed impact activations demonstrating the effect

## Technical Approach

### Phase 1: Scene Setup

**File:** `shield_ripple_effect.py` — `clear_scene()` and `create_test_shield()` functions

**Test shield geometry (2 merged spheres):**
- Two UV Spheres: 32 segments, 32 rings each (~1000 verts each before merge)
- Sphere A at origin, Sphere B offset by ~1.2x radius on X axis (overlapping)
- Boolean Union modifier applied to merge them
- Remesh modifier (voxel mode, voxel size ~0.035) applied to create uniform vertex density — this is critical because non-uniform density causes uneven wave propagation speed
- Smooth shading enabled
- Final mesh target: ~15k-20k verts at voxel size 0.035 (voxel size 0.05 only produces ~5k-9k verts, insufficient for smooth waves)

**Impact markers:**
- Single-vertex mesh objects (NOT Empties — Empties produce no geometry after Realize Instances, which breaks Collection Info + Geometry Proximity pipeline)
- Created via bmesh API (avoids mode switching): `bm = bmesh.new(); bm.verts.new((0,0,0)); bm.to_mesh(mesh); bm.free()`
- Display type set to `'PLAIN_AXES'` so they look like Empties in the viewport
- Hidden from render (`obj.hide_render = True`)
- 3 markers pre-created, placed near the shield surface at different positions
- All in a collection named `"Impacts"`
- Initial scale `(0, 0, 0)` — inactive

**Collection hierarchy:**
```
Scene Collection
├── Shield (the merged sphere mesh)
└── Impacts (collection)
    ├── Impact.001
    ├── Impact.002
    └── Impact.003
```

### Phase 2: Geometry Nodes Modifier

**File:** `shield_ripple_effect.py` — `create_geometry_nodes()` function

**Node group:** `"ShieldRippleEffect"` (GeometryNodeTree, `is_modifier = True`)

#### Group Interface (Inputs)

| Name | Socket Type | Default | Min | Max | Description |
|------|------------|---------|-----|-----|-------------|
| Geometry | `NodeSocketGeometry` | — | — | — | Shield mesh |
| Impact Collection | `NodeSocketCollection` | `"Impacts"` | — | — | Collection of impact marker objects |
| Wave Speed | `NodeSocketInt` | 5 | 1 | 15 | Blur Attribute iterations per frame (higher = faster propagation). >15 causes blur artifacts. |
| Decay Rate | `NodeSocketFloat` | 0.05 | 0.0 | 1.0 | Energy removed per frame: `energy *= (1 - decay_rate)`. Use `NodeSocketFloat` with min_value=0.0, max_value=1.0 (not `NodeSocketFloatFactor` which is undocumented). |
| Injection Radius | `NodeSocketFloat` | 0.3 | 0.01 | 5.0 | Max distance from marker for energy injection |

**Hardcoded parameters** (removed from interface for simplicity — edit in the node tree if needed):
- **Injection Strength:** 1.0 (users control intensity via activation duration)
- **Displacement Strength:** 0.05 (subtle enough to avoid self-intersection)
- **Noise Scale:** 5.0 (medium-frequency organic distortion)

#### Group Interface (Outputs)

| Name | Socket Type | Description |
|------|------------|-------------|
| Geometry | `NodeSocketGeometry` | Modified shield mesh with energy attribute |

#### API Notes for Group Interface

```python
# Blender 4.0+ uses interface.new_socket() — NOT inputs.new()
socket = node_group.interface.new_socket(name="Wave Speed", in_out='INPUT', socket_type='NodeSocketInt')
socket.default_value = 5
socket.min_value = 1
socket.max_value = 15

# For Decay Rate — use NodeSocketFloat with min/max (not NodeSocketFloatFactor)
socket = node_group.interface.new_socket(name="Decay Rate", in_out='INPUT', socket_type='NodeSocketFloat')
socket.default_value = 0.05
socket.min_value = 0.0
socket.max_value = 1.0
```

#### Node Tree Architecture

```
Group Input
  │
  ├─[Geometry]──────────────────────────────────────────────┐
  │                                                          │
  │  ┌─────────── SIMULATION ZONE ──────────────────────┐   │
  │  │                                                    │   │
  │  │  sim_input                                         │   │
  │  │    ├── Delta Time                                  │   │
  │  │    ├── Geometry (shield mesh, prev frame)          │   │
  │  │    └── Energy (float per vertex, prev frame)       │   │
  │  │                                                    │   │
  │  │  STEP 1: INJECTION                                 │   │
  │  │    Collection Info ──► Instances                    │   │
  │  │    Instance Scale ──► Vector Math (Length)          │   │
  │  │      ──► Compare (> 0.01) ──► is_active            │   │
  │  │    Capture Attribute (is_active on Instance domain) │   │
  │  │    Realize Instances ──► point cloud                │   │
  │  │    Delete Geometry (where !is_active)               │   │
  │  │      ──► active impact points                      │   │
  │  │                                                    │   │
  │  │    Geometry Proximity                              │   │
  │  │      (target=active_points, element=POINTS)        │   │
  │  │      ──► distance per shield vertex                │   │
  │  │    Map Range (distance: 0→inj_radius → 1.0→0.0)   │   │
  │  │      ──► injection_falloff (smooth linear)         │   │
  │  │    Math: Multiply (injection_falloff × inj_strength)│   │
  │  │      ──► new_energy                                │   │
  │  │                                                    │   │
  │  │  STEP 2: ACCUMULATE                                │   │
  │  │    Math: Add (prev_energy + new_energy)            │   │
  │  │    Math: Minimum (sum, 1.0) ──► clamped_energy     │   │
  │  │                                                    │   │
  │  │  STEP 3: DIFFUSION                                 │   │
  │  │    Blur Attribute                                  │   │
  │  │      (value=clamped_energy, iterations=wave_speed) │   │
  │  │      ──► diffused_energy                           │   │
  │  │                                                    │   │
  │  │  STEP 4: DECAY                                     │   │
  │  │    Math: Subtract (1.0 - decay_rate)               │   │
  │  │    Math: Multiply (diffused × (1-decay))           │   │
  │  │      ──► decayed_energy                            │   │
  │  │    Math: Maximum (decayed, 0.0) ──► floor at zero  │   │
  │  │                                                    │   │
  │  │  sim_output                                        │   │
  │  │    ├── Geometry (pass through)                     │   │
  │  │    └── Energy = decayed_energy                     │   │
  │  │                                                    │   │
  │  └────────────────────────────────────────────────────┘   │
  │                                                          │
  │  STEP 5: DISPLACEMENT (post-sim)                         │
  │    Noise Texture (position, scale=noise_scale)           │
  │      ──► noise_value                                     │
  │    Math: Multiply (energy × disp_strength × noise_value) │
  │      ──► offset_magnitude                                │
  │    Normal ──► vertex_normal                               │
  │    Vector Math: Scale (normal × offset_magnitude)         │
  │      ──► offset_vector                                    │
  │    Set Position (offset = offset_vector)                  │
  │                                                          │
  │  STEP 6: STORE ATTRIBUTE (post-sim)                      │
  │    Store Named Attribute                                  │
  │      name = "shield_energy", domain = POINT              │
  │      value = energy (from sim output)                     │
  │                                                          │
  └──────────────────► Group Output [Geometry]               │
```

#### Critical Implementation Details

**Activation detection (resolving the Empty/scale problem):**
Impact markers are single-vertex mesh objects. Scale-based activation requires reading instance scale *before* realizing instances (realization loses scale info for a vertex at origin). The simplified pattern (7 nodes, deletes before realizing to skip Capture Attribute):
1. `Collection Info` → Instances. **Critical:** Set `collection_info.transform_space = 'RELATIVE'` (property, not socket) — without this, all marker positions appear at local origins (0,0,0).
2. `Instance Scale` node (`GeometryNodeInputInstanceScale`) → scale vector on instance domain
3. `Vector Math` (Length) → scalar magnitude
4. `Compare` (Greater Than, threshold=0.01) → boolean `is_active`
5. `Delete Geometry` (Selection = NOT is_active, domain=Instance) → remove inactive markers **before** realizing (attribute is still available on instance domain)
6. `Realize Instances` → point cloud (only active markers remain)
7. These filtered points become the target for `Geometry Proximity`

**Empty-target guard:**
If the Impacts collection is empty or all markers are inactive (all deleted after filtering), Geometry Proximity has no target and returns distance=0 for all vertices, causing full-surface injection. To prevent this, add a `Domain Size` check on the filtered points and use a `Switch` node: if point count = 0, bypass injection entirely (new_energy = 0).

**Injection profile (smooth linear falloff):**
`Map Range` node maps proximity distance from `[0, injection_radius]` to `[1.0, 0.0]` with clamping enabled. This creates a smooth falloff: full energy at the impact point, linearly decreasing to zero at `injection_radius`.

**Continuous injection while active:**
Energy is injected every frame while a marker's scale > 0. For a single clean ripple, the user should keyframe scale from `(0,0,0)` → `(1,1,1)` for 1-3 frames, then back to `(0,0,0)`. Use `CONSTANT` keyframe interpolation to avoid Bezier easing through intermediate scale values. The test animation should demonstrate this.

**Order of operations inside the Simulation Zone:**
1. Inject new energy (into previous frame's field)
2. Accumulate and clamp
3. Diffuse (blur)
4. Decay

Diffusion happens AFTER injection so that newly injected energy starts spreading immediately. Decay happens last so that diffused energy begins decaying from the moment it spreads.

**Energy floor:**
After decay, clamp energy to minimum 0.0 via a single `Math: Maximum(energy, 0.0)`. The shader's Color Ramp maps near-zero values to fully transparent, so no separate threshold check is needed.

#### Simulation Zone API Notes

```python
# Create Simulation Zone (Blender 4.0+)
sim_input = node_group.nodes.new('GeometryNodeSimulationInput')
sim_output = node_group.nodes.new('GeometryNodeSimulationOutput')
sim_input.pair_with_output(sim_output)  # Critical: must pair before adding state items

# Add Energy state item (FLOAT, per-vertex)
sim_output.state_items.new('FLOAT', 'Energy')
# This creates matching sockets on both sim_input and sim_output

# Capture Attribute API (Blender 4.0+) — if used:
# capture.capture_items.new('BOOLEAN', 'is_active')
# NOT the old capture.inputs.new() API
```

#### Vector Math SCALE Socket Note

```python
# Vector Math node in SCALE mode — the Scale float input is at socket index 3, NOT index 1
# Inputs: [0] Vector, [1] Vector (unused in SCALE), [2] Vector (unused), [3] Scale (float)
vec_math.operation = 'SCALE'
links.new(some_output, vec_math.inputs[0])   # vector to scale
links.new(scale_value, vec_math.inputs[3])   # scale factor — INDEX 3
```

#### Simulation Zone State Items

| Socket Type | Name | Description |
|------------|------|-------------|
| `GEOMETRY` | Geometry | The shield mesh (passed through unchanged) |
| `FLOAT` | Energy | Per-vertex energy attribute (0.0 to 1.0) |

### Phase 3: Shield Shader

**File:** `shield_ripple_effect.py` — `create_shield_material()` function

**Material:** `"ShieldMaterial"` with `use_nodes = True`

#### Shader Node Tree

```
Attribute ("shield_energy", GEOMETRY)
  └─► [Fac] ──────────────────────────────────────────────┐
                                                           │
Layer Weight (Blend=0.1)                                   │
  └─► [Fresnel] ──► Math: Multiply (× 0.05)              │
                       └─► fresnel_subtle                  │
                                                           │
Math: Maximum (energy_fac, fresnel_subtle)                 │
  └─► combined_factor ─────────────────────────────┐      │
                                                    │      │
Color Ramp (combined_factor):                       │      │
  pos 0.0: (0, 0, 0, 0) transparent black          │      │
  pos 0.15: (0.1, 0.4, 0.8, 0.5) faint blue        │      │
  pos 0.5: (0.3, 0.7, 1.0, 1.0) bright cyan        │      │
  pos 1.0: (0.8, 0.95, 1.0, 1.0) white-hot          │      │
  └─► emission_color                                │      │
                                                    │      │
Transparent BSDF ─────────────────────────────┐    │      │
                                               │    │      │
Emission (color=emission_color, strength=5.0)  │    │      │
  └─► emission_shader                          │    │      │
                                               │    │      │
Mix Shader                                     │    │      │
  Fac ◄────────────────────────────────────────┼────┘      │
  Shader 1 ◄── Transparent BSDF ──────────────┘           │
  Shader 2 ◄── Emission                                    │
  └─► Material Output [Surface]                            │
```

#### Transparency Configuration

```python
# Both Cycles and EEVEE compatibility
mat.use_backface_culling = False  # visible from inside the shield

# EEVEE Next (Blender 4.x+) specific — use hasattr guard for version safety:
if hasattr(mat, 'surface_render_method'):
    mat.surface_render_method = 'BLENDED'
if hasattr(mat, 'use_transparency_overlap'):
    mat.use_transparency_overlap = True
if hasattr(mat, 'show_transparent_back'):
    mat.show_transparent_back = True
```

#### Bloom / Glow Configuration

The shield effect needs a glow halo to look like sci-fi energy. Without bloom, the emission looks flat.

**EEVEE:** Enable bloom in render settings:
```python
if hasattr(bpy.context.scene.eevee, 'use_bloom'):
    bpy.context.scene.eevee.use_bloom = True
    bpy.context.scene.eevee.bloom_threshold = 0.8
    bpy.context.scene.eevee.bloom_intensity = 0.5
    bpy.context.scene.eevee.bloom_radius = 6.5
```

**Cycles:** Use compositor Glare node (added by the script):
```python
bpy.context.scene.use_nodes = True  # Enable compositor
tree = bpy.context.scene.node_tree
# Add Glare node between Render Layers and Composite
glare = tree.nodes.new('CompositorNodeGlare')
glare.glare_type = 'FOG_GLOW'
glare.quality = 'HIGH'
glare.threshold = 0.8
glare.size = 7
# Link: Render Layers → Glare → Composite
```

#### Shader Behavior

- **Energy = 0, no Fresnel angle:** Fully transparent (invisible)
- **Energy = 0, grazing angle:** Very subtle Fresnel edge shimmer (factor ~0.05)
- **Energy = 0.5:** Bright cyan emission glow with partial transparency
- **Energy = 1.0:** Near-white emission at full strength, fully opaque
- **Multiple overlapping impacts:** Energy clamped at 1.0, so no over-bright artifacts

### Phase 4: Test Animation

**File:** `shield_ripple_effect.py` — `setup_test_animation()` function

**Timeline:** 1-120 frames at 24 FPS (5 seconds)

**Test sequence:**
| Frame | Impact.001 | Impact.002 | Impact.003 | Expected Visual |
|-------|-----------|-----------|-----------|-----------------|
| 1-9 | scale=0 | scale=0 | scale=0 | Shield invisible (Fresnel hint only) |
| 10-12 | scale=1 | scale=0 | scale=0 | Ripple starts at marker 1 |
| 13+ | scale=0 | scale=0 | scale=0 | Ripple propagates and fades from marker 1 |
| 40-42 | scale=0 | scale=1 | scale=0 | Ripple starts at marker 2 |
| 43+ | scale=0 | scale=0 | scale=0 | Second ripple propagates (first may still be fading) |
| 60-62 | scale=1 | scale=0 | scale=1 | TWO simultaneous ripples (markers 1 and 3) |
| 63+ | scale=0 | scale=0 | scale=0 | Both ripples propagate, may overlap and combine additively |

**Keyframe interpolation:** `CONSTANT` for all scale keyframes — prevents Bezier easing through intermediate values. Bezier handles on scale `(0,0,0)` can produce negative overshoot.

**Keyframe API pattern:**
```python
# Insert keyframe
obj.scale = (0, 0, 0)
obj.keyframe_insert(data_path="scale", frame=1)

# Set CONSTANT interpolation on all keyframes after insertion
if obj.animation_data and obj.animation_data.action:
    for fcurve in obj.animation_data.action.fcurves:
        if fcurve.data_path == "scale":
            for kp in fcurve.keyframe_points:
                kp.interpolation = 'CONSTANT'
            fcurve.update()
```

**Always insert explicit frame-1 rest keyframe** at scale `(0,0,0)` for every marker — ensures clean initial state.

**Marker positions (on the test shield surface):**
- Impact.001: top of Sphere A (0, 0, 1.0)
- Impact.002: side of Sphere B (1.8, 0, 0)
- Impact.003: junction area between spheres (0.6, 0.6, 0)

Impact.003 at the concave junction specifically tests that waves propagate correctly around non-convex geometry.

## Acceptance Criteria

### Functional Requirements

- [ ] Script runs without errors in Blender 4.2+ (Python console or Text Editor)
- [ ] Creates complete scene: shield mesh, impact markers, GeoNodes modifier, shader
- [ ] Shield is invisible when no impacts are active (energy = 0 everywhere)
- [ ] Subtle Fresnel edge visible at grazing angles even when shield is at rest
- [ ] Activating a marker (scale 0→1) injects energy at the nearest shield surface point
- [ ] Energy propagates outward along the mesh surface (not through empty space)
- [ ] Energy decays exponentially — bright near impact, fading with distance
- [ ] Wave propagation is diffuse/soft (no sharp wavefront)
- [ ] Multiple simultaneous impacts produce overlapping ripples that combine additively (clamped at 1.0)
- [ ] Deactivating a marker (scale 1→0) stops energy injection; existing ripple continues to propagate and fade
- [ ] Vertex displacement along normals creates visible surface deformation at impact points
- [ ] Noise texture modulates displacement for organic quality
- [ ] Shield renders correctly in both Cycles and EEVEE
- [ ] All exposed parameters (Wave Speed, Decay Rate, Injection Radius) are adjustable on the modifier panel
- [ ] Empty collection or all-inactive markers produce no visual artifacts (empty-target guard works)
- [ ] Bloom/glow halo visible around emission areas (EEVEE bloom or Cycles compositor Glare)

### Test Cases

- [ ] **Single impact:** One marker active for 2 frames → clean ripple propagates and fully dissipates
- [ ] **Overlapping impacts:** Two markers active simultaneously → additive glow at overlap, clamped at 1.0
- [ ] **Concave junction:** Impact at the sphere intersection → wave wraps around both sides correctly
- [ ] **Zero impacts:** All markers at scale 0 for entire timeline → shield stays invisible (no artifacts)
- [ ] **All markers active:** All 3 markers active simultaneously → shield lights up, energy doesn't exceed 1.0
- [ ] **Rapid toggle:** Marker activated for 1 frame, deactivated, then re-activated 10 frames later → two separate ripples

## Technical Considerations

### Performance

- **Blur Attribute iterations:** Each iteration is O(E) where E = edge count (E ≈ 3V for typical triangle meshes). At 50k verts and 5 iterations (default Wave Speed), that's ~750k edge evaluations per frame. At 15k verts (recommended voxel 0.035), ~225k evaluations. Expect 12-20 FPS at 50k verts / 5 iterations, 20-30 FPS at 15k verts.
- **Wave Speed >15:** Diminishing returns — energy diffuses fully across the mesh rather than propagating faster. Cap at 15.
- **Geometry Proximity:** O(V * P) where P = active impact point count. With single-vertex markers, P is small (typically < 10). Cost: ~2-5ms per frame.
- **Injection pipeline total:** ~10-25% of per-frame cost (Collection Info → filter → Proximity → Map Range).
- **Noise Texture:** ~1-3ms per frame. Could be precomputed as a static attribute for performance, but not necessary at this scale.
- **Store Named Attribute:** Negligible overhead.
- **Memory per cached frame:** ~200KB at 50k verts (float energy + position data). A 120-frame simulation: ~24MB.
- **Mitigation:** Start with voxel 0.035 (~15k verts) for viewport work. Use voxel 0.025 (~30k+ verts) for final hero renders only.

### Blender Version Compatibility

- **Minimum:** Blender 4.0 (Simulation Zones stable, `interface.new_socket()` API, `capture_items.new()` API)
- **Recommended:** Blender 4.2+ LTS
- **EEVEE transparency:** Uses `surface_render_method = 'BLENDED'` (Blender 4.x+ API). Blender 3.x used `blend_method` which is incompatible. Script uses `hasattr` guards.
- **Version guard:** Script checks `bpy.app.version >= (4, 0, 0)` at startup and raises `RuntimeError` with a clear message if too old. Do not use `sys.exit()` (crashes Blender).

### Known Limitations

1. **Wave speed varies with vertex density.** Blur Attribute propagates per-edge, so denser mesh areas propagate faster. The voxel remesh mitigates this for the test geometry, but user-provided meshes may have non-uniform density.
2. **No backward timeline scrubbing.** Simulation Zones require sequential frame evaluation. Scrubbing backward triggers re-simulation from the start frame.
3. **Baking required for final renders.** Sim zone must be baked via the modifier panel before rendering.
4. **Continuous injection while active.** Energy injects every frame a marker's scale > 0. For a clean single ripple, keep the activation window to 1-3 frames with CONSTANT keyframe interpolation.
5. **Geometry Proximity uses nearest point only.** Each shield vertex gets injection from its single nearest active marker. With very close markers, one may "shadow" the other for nearby vertices. In practice this is negligible since energy diffuses outward from injection points.
6. **Diffusion produces blobs, not rings.** Blur Attribute implements heat equation diffusion, producing expanding Gaussian blobs. This matches the "diffuse/soft" design choice. Ring wavefronts would require a Two-State Wave Equation (upgrade path documented in Enhancement Summary).
7. **Frame-rate dependent decay.** Decay is per-frame, not time-based. Changing FPS changes the apparent decay speed. Could use Simulation Zone's Delta Time output for frame-rate independence, but adds complexity beyond current scope.

## Script Structure

```
shield_ripple_effect.py
│
├── CONSTANTS
│     ENERGY_ATTR_NAME = "shield_energy"
│     IMPACT_COLLECTION_NAME = "Impacts"
│     MATERIAL_NAME = "ShieldMaterial"
│     NODE_GROUP_NAME = "ShieldRippleEffect"
│     MIN_BLENDER_VERSION = (4, 0, 0)
│
├── _add_node(nodes, type_str, label, location) → node
│     Helper: create node, set label, set location
│
├── _add_math_node(nodes, operation, label, location) → node
│     Helper: create ShaderNodeMath with operation preset
│
├── _link(links, from_socket, to_socket)
│     Helper: create link (thin wrapper for readability)
│
├── clear_scene()
│     Remove all objects (list() wrap for safe iteration),
│     then meshes, materials, node groups, collections
│     Use bpy.data.objects.remove() loop (not bpy.ops)
│
├── create_test_shield() → shield_obj
│     Create 2 UV Spheres, Boolean Union, Voxel Remesh (0.035)
│     Apply modifiers, smooth shade via mesh.shade_smooth()
│     Add post-Boolean mesh validation (check vert count > 0)
│
├── create_impact_collection(count=3) → (impact_objects, collection)
│     Create single-vertex mesh objects via bmesh API
│     bm = bmesh.new(); bm.verts.new((0,0,0)); bm.to_mesh(mesh); bm.free()
│     Set display type to PLAIN_AXES, hide from render
│     Position near shield surface, initial scale (0,0,0)
│
├── create_geometry_nodes(shield_obj, impact_collection) → modifier
│     Build full GeoNodes tree, split into sub-builders:
│     ├── _build_injection_pipeline(nodes, links, ...) → new_energy_socket
│     │     Collection Info (transform_space='RELATIVE')
│     │     Instance Scale → Length → Compare → Delete → Realize
│     │     Domain Size + Switch guard for empty target
│     │     Geometry Proximity → Map Range → injection falloff
│     ├── _build_accumulation(nodes, links, ...) → clamped_energy_socket
│     │     Math Add + Math Minimum (clamp to 1.0)
│     ├── _build_diffusion_decay(nodes, links, ...) → decayed_energy_socket
│     │     Blur Attribute (iterations = wave_speed)
│     │     Math Subtract + Multiply (exponential decay)
│     │     Math Maximum (floor at 0.0)
│     └── _build_post_sim(nodes, links, ...) → geometry_socket
│           Noise Texture + displacement along normals
│           Store Named Attribute "shield_energy"
│     Attach modifier to shield, set collection input via identifier
│
├── create_shield_material(shield_obj) → material
│     Build shader tree:
│       - Attribute node reading "shield_energy"
│       - Layer Weight Fresnel (subtle)
│       - Color Ramp for energy-to-color mapping
│       - Transparent + Emission mix
│       - EEVEE transparency settings (hasattr guards)
│     Assign to shield
│
├── setup_bloom_glow()
│     EEVEE: enable bloom (threshold=0.8, intensity=0.5, radius=6.5)
│     Cycles: compositor Glare node (FOG_GLOW, threshold=0.8)
│
├── setup_demo_scene()
│     Add camera positioned to frame the shield
│     Add area light for basic illumination
│     Set frame range 1-120, fps=24
│
├── setup_test_animation(impact_objects)
│     Keyframe scale for 3 impacts across 120 frames
│     Set CONSTANT interpolation: kp.interpolation = 'CONSTANT'
│     Insert explicit frame-1 rest keyframes (scale=0)
│
├── main()
│     Version check: if bpy.app.version < MIN_BLENDER_VERSION: raise RuntimeError(...)
│     Orchestrate all functions in order
│     Print progress logging between steps
│
└── if __name__ == "__main__":
      main()
```

## Parameter Defaults Reference

### Exposed on Modifier Panel

| Parameter | Default | Range | Rationale |
|-----------|---------|-------|-----------|
| Wave Speed | 5 | 1-15 | 5 blur iterations/frame gives visible propagation at 24fps on a ~1m radius shield. >15 causes blur artifacts. |
| Decay Rate | 0.05 | 0.0-1.0 | `0.95^24 ≈ 0.29` — energy at impact point drops to ~30% after 1 second |
| Injection Radius | 0.3 | 0.01-5.0 | ~30% of sphere radius — visible but localized injection zone |

### Hardcoded in Node Tree

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Injection Strength | 1.0 | User controls intensity via activation duration; exposing this adds confusion |
| Displacement Strength | 0.05 | 5% of unit scale — visible at close range without self-intersection |
| Noise Scale | 5.0 | Medium-frequency organic distortion |
| Emission Strength | 5.0 | Bright enough for both Cycles and EEVEE |
| Voxel Size (remesh) | 0.035 | Produces ~15k-20k verts on the test geometry — good balance of quality and performance |

## References

- Brainstorm: `docs/brainstorms/2026-02-13-shield-ripple-effect-brainstorm.md`
- Blender Geometry Nodes API: `bpy.types.GeometryNodeSimulationInput`, `GeometryNodeBlurAttribute`, `GeometryNodeProximity`
- Blender Shader API: `ShaderNodeAttribute`, `ShaderNodeMixShader`, `ShaderNodeEmission`
- EEVEE Next transparency: `material.surface_render_method = 'BLENDED'` (Blender 4.x+, replaces `blend_method`)
- Geometry Nodes Python API: `interface.new_socket()` (4.0+), `state_items.new()`, `pair_with_output()`
- Compositor Glare: `CompositorNodeGlare` with `glare_type='FOG_GLOW'`
- bmesh API: `bmesh.new()`, `bm.verts.new()`, `bm.to_mesh()`, `bm.free()`
- Keyframe API: `obj.keyframe_insert()`, `fcurve.keyframe_points[].interpolation = 'CONSTANT'`
