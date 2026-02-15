---
date: 2026-02-15
topic: torpedo-effect
status: resolved
---

# Torpedo Effect — Guided Projectile with Glow & Avoidance

## What We're Building

A Geometry Nodes system that creates glowing, guided torpedo projectiles (Star Trek photon/quantum torpedo style). The system handles both the **visual effect** (bright oscillating light, optional fading trail, point light) and the **trajectory physics** (target-seeking with obstacle avoidance via repulsors) in a single Simulation Zone.

The torpedo is a bright point of light with emission shader + bloom, randomly oscillating brightness for visual interest, and an optional luminous trail. It flies from a start point toward a target with smooth acceleration, curving naturally around repulsor objects rather than passing through them.

## Why This Approach

### Approaches Considered

| Approach                    | Description                                         | Verdict                                                                     |
| --------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------- |
| **A: Pure Geometry Nodes**  | All physics + visuals in one Simulation Zone        | **Chosen** — persistent, self-contained, no Python                          |
| B: Python + GeoNodes hybrid | Python handler for trajectory, GeoNodes for visuals | Rejected — handler is session-only, fragile (learned from shield-animation) |

Pure Geometry Nodes was chosen because:

- Fully persistent in the .blend file (no Python handlers to lose)
- Single modifier contains all logic — easy to duplicate for multiple setups
- Lesson from shield-animation: Python frame-change handlers are fragile and non-persistent

### Visual Approach

Emission shader on a small sphere mesh + parented point light for casting light on surroundings. Bloom/glare in post for the characteristic sci-fi energy halo. Not volumetric (too expensive, Cycles-heavy).

## Key Decisions

1. **Pure Geometry Nodes** — trajectory physics and visuals in one Simulation Zone, no Python handlers
2. **Shader + point light** — emission mesh for the glow, actual light object for illuminating surroundings
3. **Collection-based** — a Torpedoes collection holds multiple torpedo objects, each animated independently by one modifier
4. **Smooth avoidance** — repulsors create gradual deflection (gravitational slingshot feel), not sharp turns
5. **Optional trail** — trail length as a modifier parameter (0 = no trail), fading luminous streak behind the torpedo
6. **Built via Blender MCP** — work directly in an open Blender scene, iterating live

## Torpedo Visual

- Small sphere mesh (or icosphere) with emission shader
- Randomly oscillating brightness (noise-driven, subtle variation)
- Color: configurable, default bright blue-white (quantum torpedo) or red-orange (photon torpedo)
- Point light parented to torpedo for environmental illumination
- Optional fading trail: sequence of points behind torpedo with decreasing emission

## Trajectory Physics

### Forces Acting on Torpedo

1. **Target attraction** — acceleration vector toward target object, magnitude controlled by parameter
2. **Repulsor avoidance** — inverse-square (or inverse-cube) force pushing away from repulsor objects when within their influence radius
3. **Velocity damping / max speed** — clamp velocity magnitude to prevent runaway acceleration
4. **Initial conditions** — start position, initial direction vector, initial speed

### Simulation Zone State Items

- **Position** (VECTOR) — current torpedo position
- **Velocity** (VECTOR) — current torpedo velocity

### Per-Frame Update (inside Simulation Zone)

```
# Target attraction
to_target = normalize(target_pos - position) × attraction_strength

# Repulsor avoidance (for each repulsor)
to_repulsor = position - repulsor_pos
dist = length(to_repulsor)
repulse_force = normalize(to_repulsor) × repulsor_strength / (dist × dist)

# Update velocity
velocity += (to_target + sum(repulse_forces)) × delta_time
velocity = clamp_length(velocity, max_speed)

# Update position
position += velocity × delta_time
```

### Modifier Parameters (exposed)

| Parameter            | Type       | Default         | Description                             |
| -------------------- | ---------- | --------------- | --------------------------------------- |
| Torpedoes            | Collection | —               | Collection of torpedo start objects     |
| Target               | Object     | —               | Target object to seek                   |
| Repulsors            | Collection | —               | Collection of obstacle objects to avoid |
| Attraction           | Float      | 5.0             | Target-seeking force strength           |
| Max Speed            | Float      | 10.0            | Maximum velocity magnitude              |
| Initial Speed        | Float      | 2.0             | Launch speed along initial direction    |
| Repulsor Strength    | Float      | 50.0            | Avoidance force multiplier              |
| Repulsor Radius      | Float      | 5.0             | Influence distance of repulsors         |
| Trail Length         | Int        | 0               | Number of trail points (0 = no trail)   |
| Brightness Variation | Float      | 0.1             | Random oscillation amplitude            |
| Torpedo Color        | Color      | (0.5, 0.7, 1.0) | Base emission color                     |

## Open Questions

- **Arrival behavior:** What happens when the torpedo reaches the target? Disappear? Explode? Stop? (Can defer — just stop for now)
- **Trail implementation:** Store previous positions as instance points, or use a curve? Instances are simpler in GeoNodes.
- **Multiple targets:** Should different torpedoes in the collection seek different targets, or all seek the same one?
- **Activation timing:** Should torpedoes launch at different times (like shield torpedoes with scale keyframes), or all at once?

## Next Steps

Run `/workflows:plan` for detailed implementation — node tree architecture, shader setup, parameter routing through the Simulation Zone.
