# Torpedo Animation Rethink — Brainstorm

**Date:** 2026-02-17
**Status:** Design captured, ready for planning

## What We're Building

A redesigned torpedo animation system that replaces the hardcoded 2-torpedo architecture with a **dynamic, collection-driven spawn-and-track system**. The node tree topology is fixed — adding or removing torpedoes and targets requires only adding/removing objects in Blender collections, not modifying the script or node tree.

### Core Concept

- **Launchpads collection** — arrow empties in a "Launchpads" collection. The arrow orientation defines the initial torpedo heading direction. Each represents a firing position and direction.
- **Targets collection** — empties or single-vertex meshes in a "Targets" collection. Each represents a destination.
- **Repulsors collection** — objects in a "Repulsors" collection. Each is an avoidance obstacle.
- **Activation by property** — a launchpad activates when its scale becomes 1 (keyframeable by the animator). One activation = one torpedo spawned that frame.
- **Initial velocity from arrow direction** — at spawn, the torpedo's initial velocity vector = launchpad arrow's forward direction * exit velocity. The **exit velocity** is a global GeoNodes Group Input float (same for all torpedoes, tunable in modifier UI).
- **Target pairing by launch order** — the Nth torpedo launched is assigned the Nth target in the Targets collection (by index).
- **Persistent target_index attribute** — each torpedo instance stores its assigned target index as a Sim Zone state attribute, so it always knows which target to track.

### Per-Frame Simulation Loop (Inside Sim Zone)

1. **Detect new activations** — check launchpad scales via Collection Info, spawn torpedo instances at activated launchpad positions.
2. **Read target positions** — use Collection Info on Targets collection + Sample Index with each torpedo's `target_index` to get the assigned target position.
3. **Compute forces** — attraction toward target, repulsion from all repulsors (iterated via Collection Info on Repulsors collection).
4. **Update velocity and position** — apply forces, clamp speed, update position state.
5. **Arrival detection** — if torpedo would overshoot target, snap to target position and mark as destroyed.
6. **Post-sim** — Set Position from state, Delete Geometry for destroyed torpedoes, Instance on Points for visual mesh, Set Material.

## Why This Approach

### Problems with current implementation

- Hardcoded 2-torpedo architecture using Index comparisons and binary Mix nodes
- Individual Object Info nodes per target, manually wired
- Launch frames baked into Mix node default values
- Controller mesh vertex count must match torpedo count
- Single monolithic 550-line build function with no helpers
- Single hardcoded repulsor

### What the new design solves

- **Fixed node tree** — topology never changes regardless of torpedo/target count
- **Collection-driven** — add objects to collections, not code to scripts
- **Animator-friendly** — launch timing controlled by keyframing scale, not config dicts
- **Consistent with shield pattern** — follows the collection-based approach already proven in shield_ripple_effect.py

## Key Decisions

1. **Collections over config dicts** — Launchpads, Targets, and Repulsors are all Blender collections. No Python config dict needed for torpedo definitions.
2. **Activation via scale property** — scale=1 triggers launch. Keyframeable in Blender timeline. One activation = one torpedo.
3. **Target assignment by launch order** — Nth launched torpedo targets Nth target. Simple parallel indexing.
4. **Collection Info + Sample Index for target lookup** — inside the Sim Zone, read all target positions via Collection Info, then use Sample Index with the torpedo's stored `target_index` to get its specific target position.
5. **Global physics parameters** — all torpedoes share the same attraction, max speed, repulsor sensitivity. Tunable via modifier UI.
6. **Exit velocity as Group Input** — a single float on the modifier controls the initial speed magnitude. Direction comes from the launchpad arrow empty's orientation.
7. **Repulsors as a collection** — any object in the Repulsors collection acts as an obstacle. Scales naturally.
8. **Launchpads are arrow empties** — arrow display type gives visual direction in viewport. Object Info reads both position and rotation to derive the initial heading.
9. **Sub-builder functions** — follow the shield script pattern with focused helper functions instead of one monolithic builder.

## Open Questions

1. **Does Collection Info update live inside a Simulation Zone?** — Object Info is confirmed to work. Collection Info needs testing. If it doesn't, we may need to pass target geometry through the Sim Zone boundary each frame.
2. **Spawn mechanics in GeoNodes** — how exactly to "create" a new torpedo instance inside a running Sim Zone when a launchpad activates. May need to merge new points into the sim geometry using Join Geometry at the sim zone input.
3. **Maximum torpedo count** — is there a practical limit to how many instances the Sim Zone can track before performance degrades?
4. **Target index persistence** — confirm that integer Named Attributes survive across Sim Zone frames as state items.
5. **Repulsor iteration** — how to compute avoidance forces against all repulsors in the collection (may need Geometry Proximity node on the repulsors collection geometry).
