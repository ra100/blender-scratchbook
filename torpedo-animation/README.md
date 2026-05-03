# Torpedo Animation

Collection-driven torpedo simulation built entirely in Geometry Nodes. Add
launchpads, targets, and repulsors to Blender collections — the node tree
adapts at evaluation time. No per-frame Python handlers; everything runs
inside a Simulation Zone.

## Quick start

```bash
blender torpedo_001.blend --python torpedo_physics_handler.py
```

Or open `torpedo_001.blend` and run the script from the Text Editor. The
script:

1. Builds the `TorpedoEffect` node group and applies it as a modifier on
   the `TorpedoController` object.
2. Builds the `RepulsorTag` node group and ensures every object in the
   `Repulsors` collection has it attached.
3. Creates a demo scene (3 launchpads, 10 targets, 3 repulsors) if no
   collections exist yet.
4. Extends the scene frame range so the Simulation Zone has room to run.

Press `Alt+A` to play.

## Scene inputs

| Collection    | Role                                                          |
|---------------|---------------------------------------------------------------|
| `Launchpads`  | Arrow empties. Each launchpad fires up to `SLOTS_PER_LP` (default 8) torpedoes — one per rising edge of the launchpad's `scale` property (`<0.5 → >0.5`). Arrow `+Z` direction = launch heading. |
| `Targets`     | Empties or meshes the torpedoes home in on. May exceed launchpad count; only the first N targets (by launch order) are used. |
| `Repulsors`   | Obstacles with per-rep avoidance settings (see `RepulsorTag` below). |

### Launch mechanics

- Each launchpad has `SLOTS_PER_LP` pre-allocated torpedo slots.
- Slot `j` fires on the launchpad's `(j+1)`th scale rising edge.
- To fire N torpedoes from one launchpad, keyframe `scale` up/down/up/down
  with `CONSTANT` interpolation N times.
- Targets are assigned by global launch order: the Nth torpedo to launch
  (across all launchpads) claims the Nth target in the `Targets` collection.

## Physics model

Inside the Simulation Zone, each active torpedo runs this pipeline every
frame:

1. **Launch detection** — rising edge on its launchpad's scale fires the
   initial impulse along the arrow heading.
2. **Coast phase** — for the first `Coast Frames`, the torpedo flies
   straight without steering corrections. Gives a clean silhouette on launch.
3. **Repulsor steering** — tangent-steering + brake-cap around every
   repulsor that affects this torpedo (see `RepulsorTag`).
4. **Attraction** — unit vector toward assigned target, combined with
   steering vector and normalized into a single `desired_dir`.
5. **Turn-rate limit** — `new_dir = slerp(current_dir, desired_dir,
   max_turn_rate * dt)`. Produces curved maneuvers, no sharp angles.
6. **Speed clamp** — `speed = min(Max Speed, brake_cap_from_repulsors)`.
   Brake engages only when torpedo is radially approaching the repulsor
   (cos(angle) > 0.3).
7. **Position integration** — `pos += new_dir * speed * dt`.
8. **Arrival detection** — snap to target when within `Arrival Distance`
   or when next-frame travel would overshoot. Velocity zeroed, sphere hidden.

### Per-repulsor control: `RepulsorTag` modifier

Each repulsor object carries the `RepulsorTag` Geometry Nodes modifier
with three slots:

| Slot        | Meaning                                                   |
|-------------|-----------------------------------------------------------|
| `Min Index` | Lowest torpedo index this repulsor affects (inclusive)    |
| `Max Index` | Highest torpedo index this repulsor affects (inclusive)   |
| `Radius`    | Envelope radius (hard shell — torpedo cannot penetrate)   |

Gate: `affects = (torpedo_idx >= Min Index) AND (torpedo_idx <= Max Index)`.

Common presets:

- `Min=0, Max=9999` — affect all torpedoes (default).
- `Min=N, Max=N` — affect only the Nth-launched torpedo.
- `Min=A, Max=B` — affect a range.
- `Min=1, Max=0` — passthrough (no torpedo affected; impossible range).

The modifier works by joining a 1-vertex "marker point" to the repulsor's
visual mesh. The marker carries `rep_marker=1.0` plus the per-rep
attributes; visual verts carry `rep_marker=0.0`. The main tree realizes
the `Repulsors` collection and filters to marker verts, giving exactly one
sample point per repulsor with attributes attached.

### `TorpedoEffect` modifier parameters

Tunable on the modifier panel of `TorpedoController`:

| Param                       | Default | Role                                              |
|-----------------------------|---------|---------------------------------------------------|
| `Exit Velocity`             | 400     | Initial speed at launch                           |
| `Attraction`                | 1.0     | Weight of target attraction in `desired_dir`      |
| `Max Speed`                 | 400     | Velocity cap (per-second)                         |
| `Repulsor Strength`         | 1200    | Per-rep steering magnitude                        |
| `Repulsor Radius`           | 80      | Unused — per-rep Radius on `RepulsorTag` replaces it |
| `Repulsor Safety Margin`    | 40      | Brake engages within `envelope + this` of a rep   |
| `Repulsor Steering Range`   | 200     | Steering engages within `envelope + margin + this` — controls how early torpedoes detour |
| `Repulsor Brake Gain`       | 8       | Speed brake stiffness near envelope               |
| `Approach Radius`           | 50      | Below this distance to target, repulsors near the target disengage so arrival is guaranteed |
| `Max Turn Rate`             | 2π      | Radians per second — larger = sharper maneuver   |
| `Arrival Distance`          | 25      | Snap to target when within this                  |
| `Torpedo Radius`            | 10      | Visual sphere radius                              |
| `Coast Frames`              | 3       | Frames of straight flight after launch            |

## Tuning guide

| Symptom                                         | Adjustment                                        |
|-------------------------------------------------|---------------------------------------------------|
| Torpedoes pass too close to repulsors           | Increase `Repulsor Steering Range` or `Strength`  |
| Detour too wide / visually over-cautious        | Decrease `Repulsor Steering Range`                |
| Torpedoes enter envelope at high speed          | Increase `Repulsor Brake Gain` or `Safety Margin` |
| Torpedoes orbit target forever without landing  | Decrease `Arrival Distance` (tighter) or increase `Max Turn Rate` |
| Sharp-angle maneuvers look robotic              | Lower `Max Turn Rate`                             |
| Torpedo misses when repulsor near target        | Increase `Approach Radius` so rep disengages sooner |
| One launchpad fires many times fine, extras ignored | Increase `SLOTS_PER_LP` constant in script, re-run |

## Authoring workflow

1. Add launchpad empty (Arrow type) to `Launchpads` collection. Orient the
   arrow — arrow's `+Z` axis is the launch heading.
2. Add target empty/mesh to `Targets` collection.
3. Add repulsor mesh to `Repulsors` collection. Run the script once to
   auto-attach `RepulsorTag`; then tune `Min/Max/Radius` on the modifier.
4. Keyframe the launchpad's `scale` with `CONSTANT` interpolation: rising
   from `<0.5` to `>0.5` fires a torpedo. Falling back to 0 arms the next
   slot for another rising edge.
5. Re-run the script any time you rename the node group or want to regenerate
   the tree. Adding/removing objects to/from the collections does NOT
   require a re-run — the tree tracks collection size at eval time.

## Files

- `torpedo_physics_handler.py` — main script. Builds both node groups + demo scene.
- `torpedo_001.blend` — saved scene snapshot with current node tree and demo setup.
- `docs/brainstorms/` — design exploration notes.
- `docs/plans/` — implementation plans (most recent: `2026-05-03-rework-torpedo-physics-plan.md`).
- `docs/learnings/` — post-implementation notes and API gotchas.

## Known gotchas

- **Sim Zone caches to scene end frame** — if torpedoes appear to freeze
  near the end, extend `scene.frame_end`. Script sets it to at least 300
  in `main()`.
- **Attribute eval inside Repeat Zone** — `Input Named Attribute → Sample
  Index` may yield stale values. We use `Capture Attribute` to bind
  attributes to the marker geometry before the Repeat Zone.
- **Blender 5.x layered actions** — keyframe access path is
  `action.layers[].strips[].channelbags[].fcurves`, not `action.fcurves`.
- **Sim cache invalidation** — changing non-interface parameters on a rep's
  `RepulsorTag` modifier may not invalidate the cache. If you change per-rep
  settings, scrub frame 1 once to reset.
