# Shield Ripple Effect — Brainstorm

**Date:** 2026-02-13
**Status:** Ready for planning

## What We're Building

An animated sci-fi spaceship shield effect in Blender using Geometry Nodes. When the shield is hit, an organic, fluid-like shockwave ripples outward from the impact point along the shield surface and dissipates. The shield is invisible until impacted.

### Key Parameters

- **Shield shape:** Conformal hull (follows the ship's surface contour like a second skin)
- **Ripple style:** Organic distortion — wobbly, fluid-like wave that deforms and glows as it travels
- **Visibility:** Invisible until hit, becomes visible at impact and fades back out
- **Impact source:** Empty objects positioned at impact points, activation controlled by scale (scale > 0 = active)
- **Impact timing:** Empty scale keyframed from 0 to >0 triggers the impact; scale back to 0 deactivates
- **Impact intensity:** Uniform — all impacts are full strength (global parameter, not per-Empty)
- **Impact flash:** None — only the traveling ripple, no burst at the point of contact
- **Projectiles:** Not part of this setup — just the shield reaction
- **Multi-impact:** Multiple simultaneous impacts with overlapping ripples supported

## Why This Approach

**Chosen: Simulation Zone Wave Propagation**

Uses a Geometry Nodes Simulation Zone to propagate wave energy across the mesh surface step-by-step. Each frame, energy stored as a per-vertex attribute diffuses to neighboring vertices, creating true surface-following (geodesic-like) waves. Impact Empties inject energy at the nearest surface point on their designated start frame.

### Why not the alternatives?

- **Distance-Field Waves** — simpler but uses Euclidean distance, which can shortcut through concave geometry. Less physically convincing propagation.
- **Shader-Only** — no actual geometry displacement, weaker organic distortion feel at close range.

### Tradeoffs accepted

- Timeline must be played forward (no free scrubbing backward)
- Simulation must be baked for final renders
- More complex node tree than distance-field approach

## Key Decisions

1. **Conformal hull geometry** — shield mesh is a slightly offset duplicate of the ship hull
2. **Simulation Zone for wave propagation** — true surface-following wave behavior
3. **Per-vertex energy attribute** — stores wave intensity, diffuses each frame to neighbors
4. **Empty objects as impact triggers** — collection of Empties, scale > 0 = active impact
5. **Scale-based activation** — keyframe Empty scale to 0 (inactive) or >0 (active); intensity is global, not per-Empty
6. **Invisible until hit** — shader emission and alpha driven by the energy attribute
7. **Organic distortion via vertex displacement** — vertices displaced along normals proportional to energy, modulated by noise
8. **No impact flash** — only the traveling ripple, no burst effect at contact point
9. **Subtle Fresnel edge** — very faint glancing-angle shimmer even when shield is at rest
10. **High-poly shield mesh** — ~50k+ verts for smooth wave propagation in hero shots

## Wave Behavior

- **Speed:** Fast — ripple covers the full shield surface in ~1-2 seconds (high diffusion rate per frame)
- **Decay:** Exponential — energy drops off sharply with distance from impact; most intensity concentrated near the hit point
- **Overlap blending:** Additive, clamped to 1.0 — overlapping ripples reinforce each other but never exceed max
- **Wave shape:** Diffuse / soft — broad smooth gradient expanding outward, no sharp leading edge or hard wavefront
- **Wavefront width:** Wide and soft, not a thin ring — the organic distortion reinforces this diffuse quality

## Technical Sketch

### Geometry Nodes Structure

1. **Input:** Ship hull mesh (or offset duplicate)
2. **Simulation Zone:**
   - Read per-vertex `energy` attribute from previous frame
   - For each impact Empty in collection: if Empty scale > 0, inject energy at nearest surface point
   - Diffuse energy to neighboring vertices (weighted by edge connectivity)
   - Apply decay factor (energy *= 0.95 or similar per frame)
   - Clamp energy to [0, 1]
3. **Post-simulation:**
   - Displace vertices along normals by `energy * displacement_strength * noise_factor`
   - Output `energy` attribute for shader use
4. **Shader:**
   - Mix transparent + emission based on `energy` attribute
   - Color ramp for energy-to-color mapping (e.g., bright cyan at peak, fading to transparent)
   - Noise texture modulation on emission for organic flickering

### Inputs to Expose

- `Impact Collection` — Object Info collection of Empties
- `Wave Speed` — how many neighbor-hops per frame
- `Decay Rate` — how quickly the wave fades (0-1)
- `Displacement Strength` — max vertex displacement along normals
- `Noise Scale` — organic distortion frequency
- `Shield Offset` — distance from ship hull to shield surface
- `Emission Color` — base color of the shield glow
- `Emission Strength` — peak brightness

## Resolved Questions

- **Mesh resolution:** High (~50k+ verts) for smooth wave propagation and close-up hero shots. Performance cost accepted.
- **Fresnel edge:** Very subtle Fresnel shimmer at glancing angles even when shield is "invisible" — hints at its presence.
- **Ship model:** User has their own shield model. For development, use 2 merged spheres (boolean union) as a test shape — this provides a non-convex surface to verify waves propagate correctly around the concave junction.

## Open Questions

(None remaining)
