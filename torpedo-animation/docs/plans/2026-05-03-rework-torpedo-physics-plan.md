# Torpedo Simulation Rework Plan

**Date:** 2026-05-03
**Status:** cc:TODO
**Scope:** rewrite physics model in `torpedo_physics_handler.py` GeoNodes tree. Keep collection-driven I/O (Launchpads / Targets / Repulsors), keep scale-triggered launch, keep index-based pairing.

## Goals

1. Newton integration with **turn-rate limit** (quick maneuver, no sharp angles).
2. **Hard repulsor envelope** — torpedo never enters envelope, steers around it (flow-around, not bounce-off).
3. **Guaranteed target hit** — arrival snap on spot even with repulsors near target.

## Non-Goals

- No change to I/O contract (collection sockets stay same).
- No GPU/particle system swap — stay in GeoNodes Simulation Zone.
- No multi-target retargeting — index pairing fixed.

## Design Decisions

### D1. Turn-rate via quaternion slerp on velocity direction

Instead of clamping force magnitude, clamp **angle delta** of velocity vector per frame.

- decompose current velocity → `speed` + `dir_unit`
- compute desired direction from (attraction + repulsor steering) → `desired_dir`
- angle = `acos(dot(dir_unit, desired_dir))`
- clamp angle to `max_turn_rad * dt`
- new `dir_unit` = slerp(`dir_unit`, `desired_dir`, `clamped_angle / angle`)
- new velocity = `dir_unit * new_speed` (speed updated by accel along desired dir)

New Group Input: `Max Turn Rate (rad/s)` default ≈ `π` (180°/s — tune).

### D2. Repulsor envelope via tangent steering + speed brake

Current radial push lets torpedo penetrate at high speed. New rule:

- Per repulsor:
  - `to_rep = rep_pos - torpedo_pos`
  - `dist = length(to_rep)`
  - `envelope = rep_radius` (hard shell)
  - `safe = rep_radius + safety_margin`
  - if `dist < safe` and velocity toward repulsor (`dot(vel_dir, to_rep_norm) > 0`):
    - project velocity onto plane perpendicular to `to_rep` → `tangent_dir`
    - steer desired_dir toward `tangent_dir` (blend by `(safe - dist) / safety_margin`)
    - also cap speed: `speed = min(speed, dist_to_envelope * brake_gain)` so torpedo slows as it approaches envelope, can't overshoot
- Accumulate steering across repulsors via Repeat Zone (already there).

New Group Inputs:
- `Repulsor Safety Margin` default 50
- `Repulsor Brake Gain` default 2.0

Drop current `Repulsor Strength` radial push (replaced by tangent steering).

### D3. Arrival near repulsor — approach corridor

When `dist_to_target < approach_radius`, disable repulsor steering on the repulsor closest to the target (so torpedo can dock). Gate the per-repulsor force by:

`rep_active = (dist_rep_to_target > approach_radius) OR (torpedo not yet close to target)`

New Group Input: `Approach Radius` default 80 (should be ≥ arrival_dist, ≤ rep_radius).

### D4. Coast phase kept as-is

Launch → coast N frames (no steering) → tracking on. Works today.

## Plans.md

## Phase 1: scaffolding + turn-rate core

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Add Group Input sockets: `Max Turn Rate`, `Repulsor Safety Margin`, `Repulsor Brake Gain`, `Approach Radius`. Wire as state pass-through. | Sockets visible on modifier; defaults set per D1/D2/D3 | - | cc:TODO |
| 1.2 | Replace accel-clamp block in `_build_velocity_integration` with **desired_dir computation** (attraction_dir + steering_dir normalized). | Node graph emits unit `desired_dir`; old `ClampedForce`/`AccelCap` nodes removed | 1.1 | cc:TODO |
| 1.3 | Add **slerp turn-rate limit** sub-builder `_build_turn_limited_velocity(dir_cur, desired_dir, max_turn_rad, dt, speed) → new_vel`. Use `acos(dot)`, `min(angle, max*dt)`, and quaternion rotate-vector around axis=cross(cur, desired). | Torpedo trajectory curves smoothly; no angle step > `max_turn*dt` in sampled frames | 1.2 | cc:TODO |
| 1.4 | Smoke test: open `torpedo_001.blend`, run script, scrub 1→200. All 4 torpedoes reach targets with straight line (no repulsors yet, by temporarily moving repulsors out of range). | Viewport: 4 hits, no stalls | 1.3 | cc:TODO |

## Phase 2: envelope-safe repulsor steering

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Rewrite `_build_repulsor_forces`: per-iter output `steering_dir` (unit) + `brake_speed_cap` (float, min across repulsors). | Repeat Zone emits 2 accum items; empty collection → (0,0,0) and +∞ | 1.3 | cc:TODO |
| 2.2 | Inside loop: compute `tangent_dir = normalize(vel - dot(vel, to_rep_norm) * to_rep_norm)`; blend factor `saturate((safe - dist) / safety_margin)` gated by `dot(vel_dir, to_rep_norm) > 0` (approaching). Sum weighted tangent_dir. | Torpedo curves around single repulsor without entering envelope (visually) | 2.1 | cc:TODO |
| 2.3 | Inside loop: compute per-repulsor speed cap = `max(0, dist - envelope) * brake_gain`; take min across all. Wire cap into velocity magnitude clamp (replaces current `Max Speed` sole clamp — now `min(Max Speed, brake_cap)`). | Torpedo slows as it nears repulsor; can't breach envelope even at high Max Speed | 2.1 | cc:TODO |
| 2.4 | Combine steering into `desired_dir`: `desired_dir = normalize(attraction_dir + steering_dir * steering_weight)`. | Single unit vector fed into slerp; attraction dominates far field, steering dominates near repulsor | 2.2 | cc:TODO |

## Phase 3: arrival guarantee

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Add approach corridor: per-repulsor `rep_active = (dist_rep_target > approach_radius) OR (dist_to_target > approach_radius)`. Multiply steering + brake contributions by `rep_active`. | Repulsor adjacent to target doesn't block arrival | 2.4 | cc:TODO |
| 3.2 | Arrival snap: keep existing `_build_arrival_detection` but verify snap still fires when speed was braked to near-zero near target. Adjust arrival_dist if needed. | `cc:完了` when viewport shows torpedo landing on target.spot within 1 frame of arrival threshold | 3.1 | cc:TODO |
| 3.3 | Overshoot guard: if `speed * dt > dist_to_target` and `dist < arrival_dist * 3`, force snap. | No torpedo flies past target | 3.2 | cc:TODO |

## Phase 4: validation + cleanup

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | Test scene update in `setup_test_scene`: add 1 repulsor directly on launch-target line; add 1 repulsor near target. | Script run shows: all 4 torpedoes curve around blocker, none enter envelope, all snap-hit target | Phase 3 | cc:TODO |
| 4.2 | Remove dead nodes (old `EffectiveAttraction` boost, `ClampedForce`, old radial `RepForce`/`StrFalloff`). | Node count decreases; no orphan links | 4.1 | cc:TODO |
| 4.3 | Update docstrings of `_build_velocity_integration` and `_build_repulsor_forces` to reflect new model. | Docstrings match implementation | 4.2 | cc:TODO |
| 4.4 | Write learnings doc `docs/learnings/2026-05-03-torpedo-physics-rework-learnings.md`: what slerp pattern worked, tangent steering gotchas, brake_gain tuning. | File exists, ≥ 3 concrete API/design notes | 4.3 | cc:TODO |

## Risks

- **R1. Slerp in GeoNodes has no native node.** Must build via acos/cross/rotate-vector. Mitigation: write helper, unit-test by scrubbing single torpedo with known inputs.
- **R2. Tangent direction ambiguous when `vel || to_rep`.** `cross` degenerates. Mitigation: fallback to arbitrary perpendicular (e.g., cross with world Z) when cross-length < ε.
- **R3. Multi-repulsor steering vectors may cancel.** Mitigation: weight by inverse distance so nearest dominates, not simple sum.
- **R4. `Max Turn Rate` too tight → torpedo orbits target and never arrives.** Mitigation: expose as tunable; default ≈ π rad/s; increase arrival_dist proportionally.

## Out of Scope (future)

- Predictive look-ahead (raycast against repulsor geometry, not just sphere envelope).
- Per-torpedo params (everyone shares globals today).
- Target re-assignment / multi-target engagement.
