# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A scratchbook of Blender experiments — animations, shaders, geometry nodes, and VFX — built with AI assistance. Each experiment lives in its own subfolder. Requires **Blender 4.0+**. No external dependencies beyond Blender's built-in `bpy` and `bmesh`.

## Running Scripts

```bash
# In Blender's text editor, or from command line:
blender --python <experiment>/script.py

# On an existing .blend file:
blender myfile.blend --python <experiment>/script.py
```

## Working with Blender MCP

When a Blender MCP server is connected, build and modify node trees directly via `mcp__blender__execute_blender_code`. Keep code chunks small — Blender MCP has execution timeouts. Always re-fetch node/link references after removing nodes (Python references invalidate on deletion).

## Repository Structure

```
<experiment-name>/
  *.py                — Blender Python scripts
  docs/
    brainstorms/      — Initial design exploration and decisions
    plans/            — Detailed technical implementation plans
    learnings/        — Post-implementation notes, deviations, API gotchas
```

Learnings docs are the most valuable reference — they capture what actually worked vs. what was planned, plus replication guides.

## Critical Blender API Gotchas

These are hard-won lessons — do not retry these failed approaches:

- **Instance Scale on Collection Info instances always returns (1,1,1).** Use a separate modifier on source objects to write a named attribute instead.
- **Empties produce no geometry after Realize Instances.** Actuators must be single-vertex mesh objects (create via bmesh).
- **Capture Attribute anonymous attributes don't survive Realize Instances.** Use `Store Named Attribute` with explicit string names.
- **Blur Attribute has no Geometry input** in Blender 4.x. It operates on context geometry implicitly.
- **Group Input values propagate correctly to nodes OUTSIDE the Simulation Zone** but **NOT to nodes INSIDE the Simulation Zone**. Interior nodes receive the interface default, not the modifier override. **Workaround:** use pass-through state items: `Group Input → sim_in state input → sim_in state output → consuming node + sim_out state input`. This routes the modifier override value through the sim zone's state system. For nodes outside the sim zone, Group Input connections work directly.
- **Object Info nodes inside Simulation Zones DO work correctly** when the object reference is set directly on the node socket (not through Group Input) and `transform_space = 'ORIGINAL'`. Earlier reports of incorrect values were caused by corrupted node trees or Group Input routing. Object Info reads live scene positions each frame.
- **Scene Time works correctly inside Simulation Zones** — use it for frame-based activation instead of Object Info or Named Attributes.
- **Simulation Zone geometry freezes after frame 1.** Named Attributes on the geometry (set by external modifiers) do not update inside the sim zone on subsequent frames. External per-frame data cannot enter the sim zone through geometry attributes.
- **`ShaderNodeMath` has no `GREATER_EQUAL` operation.** Use `GREATER_THAN` with threshold adjusted by -0.5 for integer comparisons.
- **Set Position required after Simulation Zone.** Position state items track values mathematically but do not move geometry vertices. Add an explicit Set Position node after sim zone output.
- **Blender 4.x uses layered actions.** Keyframe access: `action.layers[].strips[].channelbags[].fcurves`, not `action.fcurves`.
- **Node link removal invalidates Python references.** Always iterate over `list(ng.links)` copies and re-fetch node references after removal.
- **`display_type = 'PLAIN_AXES'` is invalid for mesh objects.** Use `'WIRE'` instead.
- **Vector Math SCALE float input is at socket index 3**, not index 1.
- **ShaderNodeMix socket indices vary by `data_type`**: FLOAT=(A:2, B:3, Result:0), VECTOR=(A:4, B:5, Result:1), RGBA=(A:6, B:7, Result:2), ROTATION=(A:8, B:9, Result:3). Object Info Rotation is type ROTATION, not VECTOR.
- **`to_mesh()` cannot realize instances.** InstanceOnPoints output always returns 0 verts from `to_mesh()`. Bypass Instance/Delete nodes for debugging.
- **Simulation Zone state items use `'VECTOR'`**, not `'FLOAT_VECTOR'`, in `state_items.new()`.
- **GeoNodes visibility control**: Use Delete Geometry nodes to output empty geometry instead of `hide_viewport`/`hide_render` flags. For always-hidden objects, apply a GeoNodes modifier that deletes all points.

## Conventions

- Each experiment gets its own subfolder with docs
- Keyframe animations use **CONSTANT interpolation** unless smooth easing is explicitly needed
- Document learnings after each experiment, especially deviations from the plan and API surprises

## Commit Messages

Use **Conventional Commits** format with a **gitmoji** prefix:

```
<emoji> <type>: <description>
```

Examples:
- `✨ feat: add shield ripple wave equation`
- `🐛 fix: correct velocity decay inside sim zone`
- `📝 docs: document Blender API gotchas`
- `♻️ refactor: extract injection pipeline into helper`
- `🚚 chore: move files to subfolder`
- `🎉 feat: initial project setup`

Common gitmoji: ✨ feat, 🐛 fix, 📝 docs, ♻️ refactor, 🚚 chore, 🎨 style, ⚡ perf, 🔧 config, 🗑️ remove
