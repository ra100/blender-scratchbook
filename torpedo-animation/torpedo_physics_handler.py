"""
Torpedo Effect — Collection-Driven Scene Setup Script (v2 physics)
==================================================================
Creates the scene objects and GeoNodes node tree for the torpedo animation.
Node tree reads Launchpads, Targets, Repulsors via NodeSocketCollection
inputs + Collection Info + Sample Index, so torpedo count tracks collection
size at evaluation time — no script re-run on add/remove.

Physics model (2026-05-03 rework):
  - Newton-style integration with TURN-RATE LIMIT (slerp on velocity
    direction). "Quick maneuver, no sharp angles."
  - Repulsor avoidance via TANGENT STEERING (fly around envelope) +
    per-repulsor SPEED BRAKE (can't breach envelope at high speed).
  - APPROACH CORRIDOR: repulsors near the target disengage on final
    approach so arrival is guaranteed.
  - OVERSHOOT GUARD: force-snap to target when next-frame position would
    pass through target.

No Python handlers are used during simulation — everything runs inside
Geometry Nodes with a Simulation Zone.

Usage:
    blender torpedo_001.blend --python torpedo_physics_handler.py
"""

import bpy
import bmesh
from math import radians, pi
from mathutils import Vector


# ============================================================
# Constants
# ============================================================

NODE_GROUP_NAME = "TorpedoEffect"
LAUNCHPAD_COLLECTION = "Launchpads"
TARGET_COLLECTION = "Targets"
REPULSOR_COLLECTION = "Repulsors"
CONTROLLER_NAME = "TorpedoController"
MATERIAL_NAME = "TorpedoEmission"

ACTIVATION_THRESHOLD = 0.5
BRAKE_CAP_SENTINEL = 1.0e6  # "no threat" brake cap — any real max_speed is smaller
SLOTS_PER_LP = 8  # torpedo slots per launchpad — LP can fire this many times


# ============================================================
# Low-level helpers
# ============================================================

def _add_node(nodes, type_str, label, location):
    node = nodes.new(type_str)
    node.label = label
    node.name = label
    node.location = location
    return node


def _add_math_node(nodes, operation, label, location):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.label = label
    node.name = label
    node.location = location
    return node


def _add_vmath_node(nodes, operation, label, location):
    node = nodes.new("ShaderNodeVectorMath")
    node.operation = operation
    node.label = label
    node.name = label
    node.location = location
    return node


def _link(links, from_socket, to_socket):
    links.new(from_socket, to_socket)


# ============================================================
# Validation
# ============================================================

def _resolve_collections():
    found = {}
    for name in (LAUNCHPAD_COLLECTION, TARGET_COLLECTION, REPULSOR_COLLECTION):
        col = bpy.data.collections.get(name)
        found[name] = col
        if col is None:
            print(f"WARNING: collection '{name}' not found. Modifier socket will be empty.")
        elif len(col.objects) == 0:
            print(f"WARNING: collection '{name}' is empty.")
    return found


# ============================================================
# Node Helpers
# ============================================================

def _build_collection_info(nodes, links, collection_socket, label, location):
    ci = _add_node(nodes, 'GeometryNodeCollectionInfo', label, location)
    ci.transform_space = 'ORIGINAL'
    _link(links, collection_socket, ci.inputs['Collection'])
    ci.inputs['Separate Children'].default_value = True
    ci.inputs['Reset Children'].default_value = False
    return ci.outputs['Instances'], ci


def _instance_count(nodes, links, instances_socket, label, location):
    ads = _add_node(nodes, 'GeometryNodeAttributeDomainSize', label, location)
    ads.component = 'INSTANCES'
    _link(links, instances_socket, ads.inputs['Geometry'])
    return ads.outputs['Instance Count']


def _sample_per_point(nodes, links, instances_socket, index_socket,
                      value_socket, data_type, label, location):
    si = _add_node(nodes, 'GeometryNodeSampleIndex', label, location)
    si.data_type = data_type
    si.domain = 'INSTANCE'
    _link(links, instances_socket, si.inputs['Geometry'])
    _link(links, value_socket, si.inputs['Value'])
    _link(links, index_socket, si.inputs['Index'])
    return si.outputs['Value']


def _build_latch(nodes, links, check_socket, prev_socket, label, location):
    latch = _add_math_node(nodes, 'MAXIMUM', label, location)
    _link(links, prev_socket, latch.inputs[0])
    _link(links, check_socket, latch.inputs[1])
    return latch.outputs[0]


def _safe_normalize(nodes, links, vec_socket, label, location):
    """Normalize with a small epsilon fallback — if length ≈ 0, returns (0,0,0)."""
    n = _add_vmath_node(nodes, 'NORMALIZE', label, location)
    _link(links, vec_socket, n.inputs[0])
    return n.outputs['Vector']


def _build_slerp_turn(nodes, links, cur_dir_socket, desired_dir_socket,
                      max_turn_socket, dt_socket, x_offset):
    """Rotate cur_dir toward desired_dir, limited to max_turn * dt radians.

    Both inputs must be unit vectors (or (0,0,0); handled gracefully).
    Returns new_dir unit vector socket.

    Implementation:
      dot = clamp(dot(cur,desired), -1, 1)
      angle = acos(dot)
      step = min(angle, max_turn * dt)
      axis = normalize(cross(cur, desired))        [fallback: cross(cur,+Z), then +X]
      rot  = axis_angle_to_rotation(axis, step)
      new  = rotate_vector(cur, rot)

    When cur and desired are nearly identical, step ≈ 0 → rotation is
    identity; axis degeneracy harmless. When cur ≈ -desired (opposite),
    cross degenerates; fallback axis picks an arbitrary perpendicular so
    the torpedo peels off to one side instead of stalling.
    """
    x = x_offset

    # dot + clamp
    dot = _add_vmath_node(nodes, 'DOT_PRODUCT', "Slerp_Dot", (x, 0))
    _link(links, cur_dir_socket, dot.inputs[0])
    _link(links, desired_dir_socket, dot.inputs[1])

    dot_clamp_lo = _add_math_node(nodes, 'MAXIMUM', "Slerp_DotLo", (x + 200, 0))
    dot_clamp_lo.inputs[1].default_value = -1.0
    _link(links, dot.outputs['Value'], dot_clamp_lo.inputs[0])

    dot_clamp_hi = _add_math_node(nodes, 'MINIMUM', "Slerp_DotHi", (x + 400, 0))
    dot_clamp_hi.inputs[1].default_value = 1.0
    _link(links, dot_clamp_lo.outputs[0], dot_clamp_hi.inputs[0])

    # angle = acos(dot)
    angle = _add_math_node(nodes, 'ARCCOSINE', "Slerp_Angle", (x + 600, 0))
    _link(links, dot_clamp_hi.outputs[0], angle.inputs[0])

    # max_step = max_turn * dt
    max_step = _add_math_node(nodes, 'MULTIPLY', "Slerp_MaxStep", (x + 600, -200))
    _link(links, max_turn_socket, max_step.inputs[0])
    _link(links, dt_socket, max_step.inputs[1])

    # step = min(angle, max_step)
    step = _add_math_node(nodes, 'MINIMUM', "Slerp_Step", (x + 800, 0))
    _link(links, angle.outputs[0], step.inputs[0])
    _link(links, max_step.outputs[0], step.inputs[1])

    # axis = normalize(cross(cur, desired))
    cross = _add_vmath_node(nodes, 'CROSS_PRODUCT', "Slerp_Cross", (x, -400))
    _link(links, cur_dir_socket, cross.inputs[0])
    _link(links, desired_dir_socket, cross.inputs[1])

    cross_len = _add_vmath_node(nodes, 'LENGTH', "Slerp_CrossLen", (x + 200, -400))
    _link(links, cross.outputs['Vector'], cross_len.inputs[0])

    # Fallback axis 1: cross(cur, +Z)
    fb1 = _add_vmath_node(nodes, 'CROSS_PRODUCT', "Slerp_FB1", (x, -600))
    fb1.inputs[1].default_value = (0.0, 0.0, 1.0)
    _link(links, cur_dir_socket, fb1.inputs[0])

    fb1_len = _add_vmath_node(nodes, 'LENGTH', "Slerp_FB1Len", (x + 200, -600))
    _link(links, fb1.outputs['Vector'], fb1_len.inputs[0])

    # Fallback axis 2: cross(cur, +X)
    fb2 = _add_vmath_node(nodes, 'CROSS_PRODUCT', "Slerp_FB2", (x, -800))
    fb2.inputs[1].default_value = (1.0, 0.0, 0.0)
    _link(links, cur_dir_socket, fb2.inputs[0])

    # Choose fallback: fb1 if |fb1| > eps, else fb2
    fb1_degen = _add_math_node(nodes, 'LESS_THAN', "Slerp_FB1Degen", (x + 400, -600))
    fb1_degen.inputs[1].default_value = 0.0001
    _link(links, fb1_len.outputs['Value'], fb1_degen.inputs[0])

    fb_pick = _add_node(nodes, 'ShaderNodeMix', "Slerp_FBPick", (x + 600, -700))
    fb_pick.data_type = 'VECTOR'
    fb_pick.clamp_factor = True
    _link(links, fb1_degen.outputs[0], fb_pick.inputs['Factor'])
    _link(links, fb1.outputs['Vector'], fb_pick.inputs[4])   # factor=0 → A = fb1
    _link(links, fb2.outputs['Vector'], fb_pick.inputs[5])   # factor=1 → B = fb2

    # Main axis vs fallback
    cross_degen = _add_math_node(nodes, 'LESS_THAN', "Slerp_CrossDegen", (x + 400, -400))
    cross_degen.inputs[1].default_value = 0.0001
    _link(links, cross_len.outputs['Value'], cross_degen.inputs[0])

    axis_pick = _add_node(nodes, 'ShaderNodeMix', "Slerp_AxisPick", (x + 800, -500))
    axis_pick.data_type = 'VECTOR'
    axis_pick.clamp_factor = True
    _link(links, cross_degen.outputs[0], axis_pick.inputs['Factor'])
    _link(links, cross.outputs['Vector'], axis_pick.inputs[4])     # factor=0 → cross
    _link(links, fb_pick.outputs[1], axis_pick.inputs[5])          # factor=1 → fallback

    axis_norm = _safe_normalize(nodes, links, axis_pick.outputs[1],
                                "Slerp_AxisNorm", (x + 1000, -500))

    # rotation from axis + angle
    aa = _add_node(nodes, 'FunctionNodeAxisAngleToRotation',
                   "Slerp_AxisAngle", (x + 1200, -200))
    _link(links, axis_norm, aa.inputs['Axis'])
    _link(links, step.outputs[0], aa.inputs['Angle'])

    # rotate cur_dir by rotation
    rv = _add_node(nodes, 'FunctionNodeRotateVector',
                   "Slerp_Rotate", (x + 1400, 0))
    _link(links, cur_dir_socket, rv.inputs['Vector'])
    _link(links, aa.outputs['Rotation'], rv.inputs['Rotation'])

    return rv.outputs['Vector']


# ============================================================
# Sub-builders
# ============================================================

def _build_launch(nodes, links, lp_scale_socket, lp_rot_socket, lp_pos_socket,
                  prev_lp_scale_socket, slot_idx_socket, lp_fire_count_socket,
                  exit_vel_socket,
                  prev_active_socket, prev_velocity_socket, prev_position_socket,
                  x_offset):
    """Per-slot activation for multi-launch launchpads.

    Each launchpad has SLOTS_PER_LP torpedo slots. Slot j fires on the LP's
    (j+1)th rising edge (scale crosses threshold upward). Rising edge detection
    uses the slot's own PrevLpScale state:

      lp_hot          = |lp_scale| > threshold
      prev_hot        = prev_lp_scale > threshold                  (per-slot state)
      rising_edge     = lp_hot AND NOT prev_hot
      my_turn         = rising_edge AND (lp_fire_count == slot_idx)
      active          = latch(my_turn OR prev_active)              (sticky)
      launch_mask     = active AND NOT prev_active                 (first-frame mask)

    lp_fire_count is the shared count of rising edges this LP has seen so far,
    supplied externally (aggregated across slots sharing that LP).

    Returns (active, launch_mask, initial_velocity, spawn_pos, lp_hot).
    lp_hot is used externally to compute lp_fire_count updates and next
    PrevLpScale state.
    """
    x = x_offset

    scale_len = _add_vmath_node(nodes, 'LENGTH', "ScaleLength", (x + 600, -200))
    _link(links, lp_scale_socket, scale_len.inputs[0])

    lp_hot = _add_math_node(nodes, 'GREATER_THAN', "LpHot", (x + 800, -200))
    lp_hot.inputs[1].default_value = ACTIVATION_THRESHOLD
    _link(links, scale_len.outputs['Value'], lp_hot.inputs[0])

    prev_hot = _add_math_node(nodes, 'GREATER_THAN', "PrevHot", (x + 800, -280))
    prev_hot.inputs[1].default_value = ACTIVATION_THRESHOLD
    _link(links, prev_lp_scale_socket, prev_hot.inputs[0])

    # rising_edge = lp_hot * (1 - prev_hot)
    one_minus_prev = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusPrevHot", (x + 1000, -280),
    )
    one_minus_prev.inputs[0].default_value = 1.0
    _link(links, prev_hot.outputs[0], one_minus_prev.inputs[1])

    rising_edge = _add_math_node(
        nodes, 'MULTIPLY', "RisingEdge", (x + 1200, -240),
    )
    _link(links, lp_hot.outputs[0], rising_edge.inputs[0])
    _link(links, one_minus_prev.outputs[0], rising_edge.inputs[1])

    # my_turn = rising_edge AND (lp_fire_count == slot_idx)
    # Use COMPARE (|a-b|<eps) — ShaderNodeMath has no equality, fake with
    # LESS_THAN(|diff|, 0.5).
    slot_diff = _add_math_node(
        nodes, 'SUBTRACT', "SlotDiff", (x + 1000, -100),
    )
    _link(links, lp_fire_count_socket, slot_diff.inputs[0])
    _link(links, slot_idx_socket, slot_diff.inputs[1])

    slot_diff_abs = _add_math_node(
        nodes, 'ABSOLUTE', "SlotDiffAbs", (x + 1200, -100),
    )
    _link(links, slot_diff.outputs[0], slot_diff_abs.inputs[0])

    slot_match = _add_math_node(
        nodes, 'LESS_THAN', "SlotMatch", (x + 1400, -100),
    )
    slot_match.inputs[1].default_value = 0.5
    _link(links, slot_diff_abs.outputs[0], slot_match.inputs[0])

    my_turn = _add_math_node(
        nodes, 'MULTIPLY', "MyTurn", (x + 1600, -200),
    )
    _link(links, rising_edge.outputs[0], my_turn.inputs[0])
    _link(links, slot_match.outputs[0], my_turn.inputs[1])

    # active latch = max(prev_active, my_turn)
    active_socket = _build_latch(
        nodes, links,
        my_turn.outputs[0], prev_active_socket,
        "ActiveLatch", (x + 1800, -200),
    )

    # launch_mask = active - prev_active (1 on launch frame)
    launch_mask = _add_math_node(
        nodes, 'SUBTRACT', "LaunchMask", (x + 2000, -200),
    )
    _link(links, active_socket, launch_mask.inputs[0])
    _link(links, prev_active_socket, launch_mask.inputs[1])

    # Velocity impulse
    rotate_vec = _add_node(
        nodes, 'FunctionNodeRotateVector', "RotateForward", (x + 600, -600),
    )
    rotate_vec.inputs['Vector'].default_value = (0.0, 0.0, 1.0)
    _link(links, lp_rot_socket, rotate_vec.inputs['Rotation'])

    impulse_scale = _add_vmath_node(nodes, 'SCALE', "ImpulseScale", (x + 800, -600))
    _link(links, rotate_vec.outputs['Vector'], impulse_scale.inputs[0])
    _link(links, exit_vel_socket, impulse_scale.inputs[3])

    impulse_masked = _add_vmath_node(
        nodes, 'SCALE', "ImpulseMasked", (x + 1000, -600),
    )
    _link(links, impulse_scale.outputs['Vector'], impulse_masked.inputs[0])
    _link(links, launch_mask.outputs[0], impulse_masked.inputs[3])

    initial_velocity = _add_vmath_node(
        nodes, 'ADD', "InitialVelocity", (x + 1200, -600),
    )
    _link(links, prev_velocity_socket, initial_velocity.inputs[0])
    _link(links, impulse_masked.outputs['Vector'], initial_velocity.inputs[1])

    spawn_pos = _add_node(
        nodes, 'ShaderNodeMix', "SpawnPos", (x + 1200, -1400),
    )
    spawn_pos.data_type = 'VECTOR'
    spawn_pos.clamp_factor = True
    _link(links, launch_mask.outputs[0], spawn_pos.inputs['Factor'])
    _link(links, prev_position_socket, spawn_pos.inputs[4])
    _link(links, lp_pos_socket, spawn_pos.inputs[5])

    return (active_socket, launch_mask.outputs[0],
            initial_velocity.outputs['Vector'], spawn_pos.outputs[1],
            lp_hot.outputs[0], rising_edge.outputs[0])


def _build_repulsor_forces(nodes, links, position_socket, velocity_socket,
                           target_pos_socket, dist_to_target_socket,
                           rep_instances_socket, rep_count_socket,
                           rep_strength_socket, rep_radius_socket,
                           safety_margin_socket, brake_gain_socket,
                           approach_radius_socket, x_offset):
    """Tangent-steering + brake-cap repulsor avoidance via Repeat Zone.

    For each repulsor i:
      to_rep    = rep_pos - pos
      dist      = |to_rep|
      envelope  = rep_radius
      safe      = envelope + safety_margin
      approach  = dot(vel_dir, to_rep_norm)            # >0 = heading toward rep
      rep_gate  = (dist_rep_target > approach_radius) OR (dist_to_target > approach_radius)
      active    = approach > 0 AND dist < safe AND rep_gate

      tangent   = vel - dot(vel, to_rep_norm) * to_rep_norm
                  (fallback to cross(to_rep_norm, +Z) if vel || to_rep)
      blend     = saturate((safe - dist) / safety_margin)
      weight    = active * blend * rep_strength / max(dist, 1)
      steering += normalize(tangent) * weight

      brake_cap_i = active ? max(0, dist - envelope) * brake_gain : INF
      brake_cap   = min_i(brake_cap_i)

    Empty collection → zero iterations → steering = (0,0,0), brake_cap = INF.

    Returns (steering_dir_socket, brake_cap_socket).
    """
    x = x_offset
    y = -1000

    ri = _add_node(nodes, 'GeometryNodeRepeatInput', "RepLoopIn", (x, y))
    ro = _add_node(nodes, 'GeometryNodeRepeatOutput', "RepLoopOut", (x + 2000, y))
    ri.pair_with_output(ro)
    ro.repeat_items.new('VECTOR', 'Steering')
    ro.repeat_items.new('FLOAT', 'BrakeCap')

    _link(links, rep_count_socket, ri.inputs['Iterations'])

    # Seed BrakeCap to sentinel (large). Repeat Zone inputs default from socket default.
    ri.inputs['BrakeCap'].default_value = BRAKE_CAP_SENTINEL
    # Steering seed is (0,0,0) by default.

    # Dummy 1-vert geometry required by Repeat Zone.
    dummy = _add_node(nodes, 'GeometryNodeMeshLine', "RepDummy", (x - 200, y - 200))
    dummy.inputs['Count'].default_value = 1
    _link(links, dummy.outputs['Mesh'], ri.inputs['Geometry'])
    _link(links, ri.outputs['Geometry'], ro.inputs['Geometry'])

    # Per-iter repulsor position
    rep_pos_in = _add_node(nodes, 'GeometryNodeInputPosition',
                           "RepPosIn", (x + 200, y - 400))
    rep_pos = _sample_per_point(
        nodes, links, rep_instances_socket, ri.outputs['Iteration'],
        rep_pos_in.outputs['Position'], 'FLOAT_VECTOR',
        "SampleRepPos", (x + 400, y - 400),
    )

    # to_rep, dist, to_rep_norm
    to_rep = _add_vmath_node(nodes, 'SUBTRACT', "ToRep", (x + 600, y))
    _link(links, rep_pos, to_rep.inputs[0])
    _link(links, position_socket, to_rep.inputs[1])

    dist_rep = _add_vmath_node(nodes, 'LENGTH', "DistRep", (x + 800, y - 100))
    _link(links, to_rep.outputs['Vector'], dist_rep.inputs[0])

    to_rep_norm = _safe_normalize(
        nodes, links, to_rep.outputs['Vector'],
        "ToRepNorm", (x + 800, y),
    )

    # vel_dir (may be zero if stationary; treat as not-approaching)
    vel_dir = _safe_normalize(nodes, links, velocity_socket,
                              "VelDir", (x + 600, y + 200))

    # approach = dot(vel_dir, to_rep_norm)
    approach = _add_vmath_node(nodes, 'DOT_PRODUCT', "Approach", (x + 1000, y))
    _link(links, vel_dir, approach.inputs[0])
    _link(links, to_rep_norm, approach.inputs[1])

    # Steering engages when even slightly heading at rep (approach > 0).
    approaching = _add_math_node(nodes, 'GREATER_THAN', "Approaching", (x + 1200, y))
    approaching.inputs[1].default_value = 0.0
    _link(links, approach.outputs['Value'], approaching.inputs[0])

    # Brake engages only for meaningful radial approach (> 0.3 cos).
    brake_approaching = _add_math_node(
        nodes, 'GREATER_THAN', "BrakeApproaching", (x + 1200, y + 50),
    )
    brake_approaching.inputs[1].default_value = 0.3
    _link(links, approach.outputs['Value'], brake_approaching.inputs[0])

    # safe = envelope + safety_margin
    safe_dist = _add_math_node(nodes, 'ADD', "SafeDist", (x + 800, y + 200))
    _link(links, rep_radius_socket, safe_dist.inputs[0])
    _link(links, safety_margin_socket, safe_dist.inputs[1])

    in_range = _add_math_node(nodes, 'LESS_THAN', "InRange", (x + 1000, y + 100))
    _link(links, dist_rep.outputs['Value'], in_range.inputs[0])
    _link(links, safe_dist.outputs[0], in_range.inputs[1])

    # rep_gate: rep not in approach corridor of target (either rep is far from target
    # OR torpedo still far from target)
    rep_to_target = _add_vmath_node(nodes, 'SUBTRACT',
                                    "RepToTarget", (x + 600, y - 500))
    _link(links, target_pos_socket, rep_to_target.inputs[0])
    _link(links, rep_pos, rep_to_target.inputs[1])

    dist_rep_target = _add_vmath_node(nodes, 'LENGTH',
                                      "DistRepTarget", (x + 800, y - 500))
    _link(links, rep_to_target.outputs['Vector'], dist_rep_target.inputs[0])

    rep_far_from_tgt = _add_math_node(
        nodes, 'GREATER_THAN', "RepFarFromTgt", (x + 1000, y - 500),
    )
    _link(links, dist_rep_target.outputs['Value'], rep_far_from_tgt.inputs[0])
    _link(links, approach_radius_socket, rep_far_from_tgt.inputs[1])

    torp_far_from_tgt = _add_math_node(
        nodes, 'GREATER_THAN', "TorpFarFromTgt", (x + 1000, y - 600),
    )
    _link(links, dist_to_target_socket, torp_far_from_tgt.inputs[0])
    _link(links, approach_radius_socket, torp_far_from_tgt.inputs[1])

    rep_gate = _add_math_node(
        nodes, 'MAXIMUM', "RepGate", (x + 1200, y - 550),
    )
    _link(links, rep_far_from_tgt.outputs[0], rep_gate.inputs[0])
    _link(links, torp_far_from_tgt.outputs[0], rep_gate.inputs[1])

    # active = approaching * in_range * rep_gate
    act_ab = _add_math_node(nodes, 'MULTIPLY', "ActAB", (x + 1400, y + 50))
    _link(links, approaching.outputs[0], act_ab.inputs[0])
    _link(links, in_range.outputs[0], act_ab.inputs[1])

    active = _add_math_node(nodes, 'MULTIPLY', "ActFull", (x + 1400, y - 50))
    _link(links, act_ab.outputs[0], active.inputs[0])
    _link(links, rep_gate.outputs[0], active.inputs[1])

    # tangent = vel - dot(vel, to_rep_norm) * to_rep_norm
    vel_dot_n = _add_vmath_node(nodes, 'DOT_PRODUCT', "VelDotN", (x + 1000, y + 300))
    _link(links, velocity_socket, vel_dot_n.inputs[0])
    _link(links, to_rep_norm, vel_dot_n.inputs[1])

    proj = _add_vmath_node(nodes, 'SCALE', "Proj", (x + 1200, y + 300))
    _link(links, to_rep_norm, proj.inputs[0])
    _link(links, vel_dot_n.outputs['Value'], proj.inputs[3])

    tangent = _add_vmath_node(nodes, 'SUBTRACT', "Tangent", (x + 1400, y + 300))
    _link(links, velocity_socket, tangent.inputs[0])
    _link(links, proj.outputs['Vector'], tangent.inputs[1])

    tangent_len = _add_vmath_node(nodes, 'LENGTH', "TangentLen", (x + 1600, y + 400))
    _link(links, tangent.outputs['Vector'], tangent_len.inputs[0])

    # Fallback tangent when vel || to_rep: cross(to_rep_norm, +Z)
    tan_fb = _add_vmath_node(nodes, 'CROSS_PRODUCT', "TanFB", (x + 1400, y + 500))
    tan_fb.inputs[1].default_value = (0.0, 0.0, 1.0)
    _link(links, to_rep_norm, tan_fb.inputs[0])

    tan_degen = _add_math_node(nodes, 'LESS_THAN', "TanDegen", (x + 1600, y + 500))
    tan_degen.inputs[1].default_value = 0.0001
    _link(links, tangent_len.outputs['Value'], tan_degen.inputs[0])

    tan_pick = _add_node(nodes, 'ShaderNodeMix', "TanPick", (x + 1700, y + 350))
    tan_pick.data_type = 'VECTOR'
    tan_pick.clamp_factor = True
    _link(links, tan_degen.outputs[0], tan_pick.inputs['Factor'])
    _link(links, tangent.outputs['Vector'], tan_pick.inputs[4])  # factor=0 → main
    _link(links, tan_fb.outputs['Vector'], tan_pick.inputs[5])    # factor=1 → fallback

    tan_norm = _safe_normalize(nodes, links, tan_pick.outputs[1],
                               "TanNorm", (x + 1800, y + 350))

    # blend = saturate((safe - dist) / safety_margin)
    safe_minus_dist = _add_math_node(nodes, 'SUBTRACT', "SafeMinusDist", (x + 1200, y + 100))
    _link(links, safe_dist.outputs[0], safe_minus_dist.inputs[0])
    _link(links, dist_rep.outputs['Value'], safe_minus_dist.inputs[1])

    blend_raw = _add_math_node(nodes, 'DIVIDE', "BlendRaw", (x + 1400, y + 100))
    _link(links, safe_minus_dist.outputs[0], blend_raw.inputs[0])
    _link(links, safety_margin_socket, blend_raw.inputs[1])

    blend_lo = _add_math_node(nodes, 'MAXIMUM', "BlendLo", (x + 1600, y + 100))
    blend_lo.inputs[1].default_value = 0.0
    _link(links, blend_raw.outputs[0], blend_lo.inputs[0])

    blend = _add_math_node(nodes, 'MINIMUM', "Blend", (x + 1700, y + 100))
    blend.inputs[1].default_value = 1.0
    _link(links, blend_lo.outputs[0], blend.inputs[0])

    # weight = active * blend * rep_strength / max(dist, 1)
    dist_safe = _add_math_node(nodes, 'MAXIMUM', "DistSafe", (x + 1600, y - 100))
    dist_safe.inputs[1].default_value = 1.0
    _link(links, dist_rep.outputs['Value'], dist_safe.inputs[0])

    w1 = _add_math_node(nodes, 'MULTIPLY', "W1", (x + 1800, y - 50))
    _link(links, active.outputs[0], w1.inputs[0])
    _link(links, blend.outputs[0], w1.inputs[1])

    w2 = _add_math_node(nodes, 'MULTIPLY', "W2", (x + 1900, y - 50))
    _link(links, w1.outputs[0], w2.inputs[0])
    _link(links, rep_strength_socket, w2.inputs[1])

    weight = _add_math_node(nodes, 'DIVIDE', "Weight", (x + 2000, y - 50))
    _link(links, w2.outputs[0], weight.inputs[0])
    _link(links, dist_safe.outputs[0], weight.inputs[1])

    # weighted_tan = tan_norm * weight
    weighted_tan = _add_vmath_node(nodes, 'SCALE', "WeightedTan", (x + 2100, y + 200))
    _link(links, tan_norm, weighted_tan.inputs[0])
    _link(links, weight.outputs[0], weighted_tan.inputs[3])

    # Steering accum += weighted_tan
    steer_add = _add_vmath_node(nodes, 'ADD', "SteerAdd", (x + 2200, y + 100))
    _link(links, ri.outputs['Steering'], steer_add.inputs[0])
    _link(links, weighted_tan.outputs['Vector'], steer_add.inputs[1])
    _link(links, steer_add.outputs['Vector'], ro.inputs['Steering'])

    # brake_cap_i: active ? max(0, dist - envelope) * brake_gain : SENTINEL
    dist_minus_env = _add_math_node(
        nodes, 'SUBTRACT', "DistMinusEnv", (x + 1200, y - 300),
    )
    _link(links, dist_rep.outputs['Value'], dist_minus_env.inputs[0])
    _link(links, rep_radius_socket, dist_minus_env.inputs[1])

    dist_minus_env_clamp = _add_math_node(
        nodes, 'MAXIMUM', "DistMinusEnvClamp", (x + 1400, y - 300),
    )
    dist_minus_env_clamp.inputs[1].default_value = 0.0
    _link(links, dist_minus_env.outputs[0], dist_minus_env_clamp.inputs[0])

    brake_raw = _add_math_node(nodes, 'MULTIPLY', "BrakeRaw", (x + 1600, y - 300))
    _link(links, dist_minus_env_clamp.outputs[0], brake_raw.inputs[0])
    _link(links, brake_gain_socket, brake_raw.inputs[1])

    # brake_cap_i = brake_fully_active ? brake_raw : SENTINEL
    # brake_fully_active = brake_approaching * in_range * rep_gate
    brake_ab = _add_math_node(
        nodes, 'MULTIPLY', "BrakeAB", (x + 1400, y - 350),
    )
    _link(links, brake_approaching.outputs[0], brake_ab.inputs[0])
    _link(links, in_range.outputs[0], brake_ab.inputs[1])

    brake_full = _add_math_node(
        nodes, 'MULTIPLY', "BrakeFull", (x + 1500, y - 400),
    )
    _link(links, brake_ab.outputs[0], brake_full.inputs[0])
    _link(links, rep_gate.outputs[0], brake_full.inputs[1])

    brake_active = _add_math_node(
        nodes, 'GREATER_THAN', "BrakeActive", (x + 1600, y - 400),
    )
    brake_active.inputs[1].default_value = 0.5
    _link(links, brake_full.outputs[0], brake_active.inputs[0])

    # Manual mix: cap = brake_raw*active + SENTINEL*(1-active)
    mix_a = _add_math_node(nodes, 'MULTIPLY', "BrakeActMul", (x + 1800, y - 300))
    _link(links, brake_raw.outputs[0], mix_a.inputs[0])
    _link(links, brake_active.outputs[0], mix_a.inputs[1])

    one_minus_active = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusActive", (x + 1800, y - 400),
    )
    one_minus_active.inputs[0].default_value = 1.0
    _link(links, brake_active.outputs[0], one_minus_active.inputs[1])

    mix_b = _add_math_node(nodes, 'MULTIPLY', "BrakeInactMul", (x + 2000, y - 400))
    mix_b.inputs[1].default_value = BRAKE_CAP_SENTINEL
    _link(links, one_minus_active.outputs[0], mix_b.inputs[0])

    brake_cap_i = _add_math_node(nodes, 'ADD', "BrakeCapI", (x + 2100, y - 350))
    _link(links, mix_a.outputs[0], brake_cap_i.inputs[0])
    _link(links, mix_b.outputs[0], brake_cap_i.inputs[1])

    # brake_cap = min(prev, brake_cap_i)
    brake_min = _add_math_node(nodes, 'MINIMUM', "BrakeMin", (x + 2200, y - 350))
    _link(links, ri.outputs['BrakeCap'], brake_min.inputs[0])
    _link(links, brake_cap_i.outputs[0], brake_min.inputs[1])
    _link(links, brake_min.outputs[0], ro.inputs['BrakeCap'])

    return ro.outputs['Steering'], ro.outputs['BrakeCap']


def _build_velocity_integration(nodes, links, velocity_socket, position_socket,
                                target_pos_socket, attraction_socket,
                                steering_dir_socket, brake_cap_socket,
                                launch_mask_socket, impulse_vel_socket,
                                active_socket, arrived_socket,
                                max_speed_socket, max_turn_socket,
                                delta_time_socket, coast_gate_socket, x_offset):
    """Direction-based integration with turn-rate limit.

      to_target        = target - pos
      dist_to_target   = |to_target|
      attraction_dir   = normalize(to_target)
      desired_raw      = attraction_dir * attraction + steering_dir
      desired_dir      = normalize(desired_raw)
                         during coast: desired_dir = vel_dir (no steering)
      cur_dir          = normalize(velocity)   [or impulse direction on launch frame]
      new_dir          = slerp_turn(cur_dir, desired_dir, max_turn, dt)
      effective_max    = min(max_speed, brake_cap)
      cur_speed        = |velocity|  [or |impulse| on launch]
      new_speed        = min(effective_max, cur_speed + effective_max)   # ramp to cap
                         simpler: new_speed = effective_max (instant hit)
      new_velocity     = new_dir * new_speed
      on launch frame: new_velocity = impulse_vel (bypass slerp)
      masked by active AND !arrived.

    Returns (new_velocity_socket, new_position_socket, dist_to_target_socket).
    """
    x = x_offset

    # to_target, dist, attraction_dir
    to_target = _add_vmath_node(nodes, 'SUBTRACT', "ToTarget", (x, -500))
    _link(links, target_pos_socket, to_target.inputs[0])
    _link(links, position_socket, to_target.inputs[1])

    dist_to_target = _add_vmath_node(nodes, 'LENGTH', "DistToTarget", (x + 200, -500))
    _link(links, to_target.outputs['Vector'], dist_to_target.inputs[0])

    attraction_dir = _safe_normalize(
        nodes, links, to_target.outputs['Vector'],
        "AttractionDir", (x + 200, -600),
    )

    # attraction_dir_weighted = attraction_dir * attraction
    attr_weighted = _add_vmath_node(nodes, 'SCALE', "AttrWeighted", (x + 400, -600))
    _link(links, attraction_dir, attr_weighted.inputs[0])
    _link(links, attraction_socket, attr_weighted.inputs[3])

    # desired_raw = attraction_weighted + steering_dir
    desired_raw = _add_vmath_node(nodes, 'ADD', "DesiredRaw", (x + 600, -600))
    _link(links, attr_weighted.outputs['Vector'], desired_raw.inputs[0])
    _link(links, steering_dir_socket, desired_raw.inputs[1])

    desired_dir = _safe_normalize(
        nodes, links, desired_raw.outputs['Vector'],
        "DesiredDir", (x + 800, -600),
    )

    # cur_dir: prefer velocity; if velocity ≈ 0 (stationary / inactive),
    # fall back to attraction_dir so slerp never sees zero-vector input.
    # (NaN from slerp would propagate through Mix even when gated by mask —
    #  IEEE: NaN * 0 = NaN.)
    cur_dir_raw = _safe_normalize(
        nodes, links, velocity_socket,
        "CurDirRaw", (x + 200, -300),
    )
    cur_len = _add_vmath_node(nodes, 'LENGTH', "CurLen", (x + 200, -200))
    _link(links, velocity_socket, cur_len.inputs[0])

    cur_stationary = _add_math_node(
        nodes, 'LESS_THAN', "CurStationary", (x + 400, -200),
    )
    cur_stationary.inputs[1].default_value = 0.0001
    _link(links, cur_len.outputs['Value'], cur_stationary.inputs[0])

    cur_dir_pick = _add_node(
        nodes, 'ShaderNodeMix', "CurDirPick", (x + 600, -300),
    )
    cur_dir_pick.data_type = 'VECTOR'
    cur_dir_pick.clamp_factor = True
    _link(links, cur_stationary.outputs[0], cur_dir_pick.inputs['Factor'])
    _link(links, cur_dir_raw, cur_dir_pick.inputs[4])        # factor=0 → velocity dir
    _link(links, attraction_dir, cur_dir_pick.inputs[5])     # factor=1 → fallback
    cur_dir = cur_dir_pick.outputs[1]

    # Coast-phase override: desired = cur_dir (no steering during coast)
    desired_gated = _add_node(nodes, 'ShaderNodeMix',
                              "DesiredGated", (x + 1000, -500))
    desired_gated.data_type = 'VECTOR'
    desired_gated.clamp_factor = True
    _link(links, coast_gate_socket, desired_gated.inputs['Factor'])
    _link(links, cur_dir, desired_gated.inputs[4])          # factor=0 (coasting) → cur_dir
    _link(links, desired_dir, desired_gated.inputs[5])      # factor=1 (tracking) → desired

    # Slerp turn-rate limit
    new_dir = _build_slerp_turn(
        nodes, links,
        cur_dir_socket=cur_dir,
        desired_dir_socket=desired_gated.outputs[1],
        max_turn_socket=max_turn_socket,
        dt_socket=delta_time_socket,
        x_offset=x + 1200,
    )

    # effective_max = min(max_speed, brake_cap)
    eff_max = _add_math_node(nodes, 'MINIMUM', "EffMax", (x + 1200, -900))
    _link(links, max_speed_socket, eff_max.inputs[0])
    _link(links, brake_cap_socket, eff_max.inputs[1])

    # cur_speed = |velocity|
    cur_speed = _add_vmath_node(nodes, 'LENGTH', "CurSpeed", (x + 400, -300))
    _link(links, velocity_socket, cur_speed.inputs[0])

    # target_speed = min(effective_max, max_speed)   (effective_max already ≤ max_speed)
    # new_speed: snap to effective_max (high max_accel ~= instant).
    # For smoother feel, could ramp. Minimal model: new_speed = effective_max.
    new_speed = eff_max.outputs[0]

    # new_velocity_dir = new_dir * new_speed
    new_vel_tracked = _add_vmath_node(nodes, 'SCALE', "NewVelTracked", (x + 3000, -500))
    _link(links, new_dir, new_vel_tracked.inputs[0])
    _link(links, new_speed, new_vel_tracked.inputs[3])

    # Launch override: on launch frame use impulse_vel directly
    launch_vel = _add_node(nodes, 'ShaderNodeMix',
                           "LaunchVelSelect", (x + 3200, -500))
    launch_vel.data_type = 'VECTOR'
    launch_vel.clamp_factor = True
    _link(links, launch_mask_socket, launch_vel.inputs['Factor'])
    _link(links, new_vel_tracked.outputs['Vector'], launch_vel.inputs[4])  # factor=0 → tracked
    _link(links, impulse_vel_socket, launch_vel.inputs[5])                 # factor=1 → impulse

    # Active/Arrived masking on velocity
    one_minus_arrived = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusArrived", (x + 3200, -700),
    )
    one_minus_arrived.inputs[0].default_value = 1.0
    _link(links, arrived_socket, one_minus_arrived.inputs[1])

    active_mask = _add_math_node(
        nodes, 'MULTIPLY', "ActiveMask", (x + 3400, -700),
    )
    _link(links, active_socket, active_mask.inputs[0])
    _link(links, one_minus_arrived.outputs[0], active_mask.inputs[1])

    masked_vel = _add_vmath_node(nodes, 'SCALE', "MaskedVel", (x + 3600, -500))
    _link(links, launch_vel.outputs[1], masked_vel.inputs[0])
    _link(links, active_mask.outputs[0], masked_vel.inputs[3])

    # Position update: pos + vel * dt
    vel_dt = _add_vmath_node(nodes, 'SCALE', "VelDt", (x + 3800, -500))
    _link(links, masked_vel.outputs['Vector'], vel_dt.inputs[0])
    _link(links, delta_time_socket, vel_dt.inputs[3])

    new_pos = _add_vmath_node(nodes, 'ADD', "NewPos", (x + 4000, -500))
    _link(links, position_socket, new_pos.inputs[0])
    _link(links, vel_dt.outputs['Vector'], new_pos.inputs[1])

    start_pos = _add_node(
        nodes, 'GeometryNodeInputPosition',
        "StartPosition", (x + 4000, -700),
    )

    pos_select = _add_node(
        nodes, 'ShaderNodeMix', "PosSelect", (x + 4200, -500),
    )
    pos_select.data_type = 'VECTOR'
    pos_select.clamp_factor = True
    _link(links, active_socket, pos_select.inputs['Factor'])
    _link(links, start_pos.outputs['Position'], pos_select.inputs[4])
    _link(links, new_pos.outputs['Vector'], pos_select.inputs[5])

    return (
        masked_vel.outputs['Vector'],
        pos_select.outputs[1],
        dist_to_target.outputs['Value'],
    )


def _build_arrival_detection(nodes, links, position_socket, target_pos_socket,
                             velocity_socket, dist_to_target_socket,
                             arrival_dist_socket, delta_time_socket,
                             prev_arrived_socket, x_offset):
    """Arrival detection + overshoot guard.

    Arrive when:
      - dist < arrival_dist, OR
      - dist < arrival_dist * 3 AND speed * dt > dist   (would overshoot next frame)

    On arrival: snap position to target, zero velocity.
    """
    x = x_offset

    # Primary arrival check
    arrival_check = _add_math_node(
        nodes, 'LESS_THAN', "ArrivalCheck", (x, -400),
    )
    _link(links, dist_to_target_socket, arrival_check.inputs[0])
    _link(links, arrival_dist_socket, arrival_check.inputs[1])

    # Overshoot guard: speed*dt > dist AND dist < arrival_dist*3
    speed = _add_vmath_node(nodes, 'LENGTH', "ArrSpeed", (x, -550))
    _link(links, velocity_socket, speed.inputs[0])

    travel = _add_math_node(nodes, 'MULTIPLY', "Travel", (x + 200, -550))
    _link(links, speed.outputs['Value'], travel.inputs[0])
    _link(links, delta_time_socket, travel.inputs[1])

    would_overshoot = _add_math_node(
        nodes, 'GREATER_THAN', "WouldOvershoot", (x + 400, -550),
    )
    _link(links, travel.outputs[0], would_overshoot.inputs[0])
    _link(links, dist_to_target_socket, would_overshoot.inputs[1])

    arr_x3 = _add_math_node(nodes, 'MULTIPLY', "ArrX3", (x + 200, -700))
    arr_x3.inputs[1].default_value = 3.0
    _link(links, arrival_dist_socket, arr_x3.inputs[0])

    near = _add_math_node(nodes, 'LESS_THAN', "Near", (x + 400, -700))
    _link(links, dist_to_target_socket, near.inputs[0])
    _link(links, arr_x3.outputs[0], near.inputs[1])

    overshoot_snap = _add_math_node(
        nodes, 'MULTIPLY', "OvershootSnap", (x + 600, -600),
    )
    _link(links, would_overshoot.outputs[0], overshoot_snap.inputs[0])
    _link(links, near.outputs[0], overshoot_snap.inputs[1])

    # arrival_combined = max(arrival_check, overshoot_snap)
    arrival_combined = _add_math_node(
        nodes, 'MAXIMUM', "ArrivalCombined", (x + 800, -500),
    )
    _link(links, arrival_check.outputs[0], arrival_combined.inputs[0])
    _link(links, overshoot_snap.outputs[0], arrival_combined.inputs[1])

    # Latch
    arrived_socket = _build_latch(
        nodes, links,
        arrival_combined.outputs[0], prev_arrived_socket,
        "ArrivedLatch", (x + 1000, -500),
    )

    first_arrival = _add_math_node(
        nodes, 'SUBTRACT', "FirstArrival", (x + 1200, -500),
    )
    _link(links, arrived_socket, first_arrival.inputs[0])
    _link(links, prev_arrived_socket, first_arrival.inputs[1])

    # Position snap: on first-arrival frame, jump to target position
    pos_snap = _add_node(nodes, 'ShaderNodeMix', "PosSnap", (x + 1400, -500))
    pos_snap.data_type = 'VECTOR'
    pos_snap.clamp_factor = True
    _link(links, first_arrival.outputs[0], pos_snap.inputs['Factor'])
    _link(links, position_socket, pos_snap.inputs[4])
    _link(links, target_pos_socket, pos_snap.inputs[5])

    # Velocity zero on arrival
    vel_zero = _add_node(nodes, 'ShaderNodeMix', "VelZero", (x + 1400, -700))
    vel_zero.data_type = 'VECTOR'
    vel_zero.clamp_factor = True
    _link(links, arrived_socket, vel_zero.inputs['Factor'])
    _link(links, velocity_socket, vel_zero.inputs[4])
    vel_zero.inputs[5].default_value = (0.0, 0.0, 0.0)

    return arrived_socket, pos_snap.outputs[1], vel_zero.outputs[1]


def _build_visual_output(nodes, links, geo_socket, position_socket,
                         active_socket, arrived_socket, torpedo_radius_socket,
                         material, x_offset):
    """Post-sim visual pipeline. Unchanged from v1."""
    x = x_offset

    set_pos = _add_node(
        nodes, 'GeometryNodeSetPosition', "SetPosition", (x, 0),
    )
    _link(links, geo_socket, set_pos.inputs['Geometry'])
    _link(links, position_socket, set_pos.inputs['Position'])

    one_minus_arrived = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusArrivedPost", (x, -100),
    )
    one_minus_arrived.inputs[0].default_value = 1.0
    _link(links, arrived_socket, one_minus_arrived.inputs[1])

    vis_mask = _add_math_node(
        nodes, 'MULTIPLY', "VisMask", (x + 200, -100),
    )
    _link(links, active_socket, vis_mask.inputs[0])
    _link(links, one_minus_arrived.outputs[0], vis_mask.inputs[1])

    vis_invert = _add_math_node(
        nodes, 'SUBTRACT', "VisInvert", (x + 200, 0),
    )
    vis_invert.inputs[0].default_value = 1.0
    _link(links, vis_mask.outputs[0], vis_invert.inputs[1])

    vis_bool = _add_math_node(
        nodes, 'GREATER_THAN', "VisBool", (x + 400, 0),
    )
    vis_bool.inputs[1].default_value = 0.5
    _link(links, vis_invert.outputs[0], vis_bool.inputs[0])

    delete = _add_node(
        nodes, 'GeometryNodeDeleteGeometry',
        "DeleteInvisible", (x + 400, 100),
    )
    delete.domain = 'POINT'
    _link(links, set_pos.outputs['Geometry'], delete.inputs['Geometry'])
    _link(links, vis_bool.outputs[0], delete.inputs['Selection'])

    uv_sphere = _add_node(
        nodes, 'GeometryNodeMeshUVSphere',
        "TorpedoSphere", (x + 400, 300),
    )
    uv_sphere.inputs['Segments'].default_value = 16
    uv_sphere.inputs['Rings'].default_value = 8
    _link(links, torpedo_radius_socket, uv_sphere.inputs['Radius'])

    set_mat = _add_node(
        nodes, 'GeometryNodeSetMaterial', "SetMaterial", (x + 600, 300),
    )
    set_mat.inputs['Material'].default_value = material
    _link(links, uv_sphere.outputs['Mesh'], set_mat.inputs['Geometry'])

    instance_pts = _add_node(
        nodes, 'GeometryNodeInstanceOnPoints',
        "InstanceOnPoints", (x + 600, 100),
    )
    _link(links, delete.outputs['Geometry'], instance_pts.inputs['Points'])
    _link(links, set_mat.outputs['Geometry'], instance_pts.inputs['Instance'])

    realize = _add_node(
        nodes, 'GeometryNodeRealizeInstances',
        "RealizeFinal", (x + 800, 100),
    )
    _link(links, instance_pts.outputs['Instances'], realize.inputs['Geometry'])

    return realize.outputs['Geometry']


# ============================================================
# Scene Functions
# ============================================================

def _ensure_clean_node_group(name):
    old = bpy.data.node_groups.get(name)
    if old:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng.is_modifier = True
    return ng


def _create_controller_mesh():
    obj = bpy.data.objects.get(CONTROLLER_NAME)
    mesh = bpy.data.meshes.get(CONTROLLER_NAME)
    if mesh is None:
        mesh = bpy.data.meshes.new(CONTROLLER_NAME)
    bm = bmesh.new()
    bm.verts.new((0, 0, 0))
    bm.to_mesh(mesh)
    bm.free()
    if obj is None:
        obj = bpy.data.objects.new(CONTROLLER_NAME, mesh)
        bpy.context.scene.collection.objects.link(obj)
    else:
        obj.data = mesh
    obj.display_type = 'WIRE'
    return obj


def _create_torpedo_material():
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if not mat:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex_coord = nodes.new('ShaderNodeTexCoord')
    tex_coord.location = (-600, 0)

    mapping = nodes.new('ShaderNodeMapping')
    mapping.location = (-400, 0)
    links.new(tex_coord.outputs['Object'], mapping.inputs['Vector'])

    drv = mapping.inputs['Location'].driver_add('default_value', 2)
    drv.driver.type = 'SCRIPTED'
    drv.driver.expression = 'frame * 0.3'

    noise = nodes.new('ShaderNodeTexNoise')
    noise.location = (-200, 0)
    noise.inputs['Scale'].default_value = 20.0
    noise.inputs['Detail'].default_value = 2.0
    noise.inputs['Roughness'].default_value = 0.7
    links.new(mapping.outputs['Vector'], noise.inputs['Vector'])

    map_range = nodes.new('ShaderNodeMapRange')
    map_range.location = (0, 0)
    map_range.inputs['From Min'].default_value = 0.0
    map_range.inputs['From Max'].default_value = 1.0
    map_range.inputs['To Min'].default_value = 8.0
    map_range.inputs['To Max'].default_value = 22.0
    links.new(noise.outputs['Fac'], map_range.inputs['Value'])

    emission = nodes.new('ShaderNodeEmission')
    emission.location = (200, 0)
    emission.inputs['Color'].default_value = (0.5, 0.7, 1.0, 1.0)
    links.new(map_range.outputs['Result'], emission.inputs['Strength'])

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (400, 0)
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return mat


# ============================================================
# Main Builder
# ============================================================

def build_torpedo_effect():
    """Build the TorpedoEffect GeoNodes tree with v2 physics."""
    ng = _ensure_clean_node_group(NODE_GROUP_NAME)
    nodes = ng.nodes
    links = ng.links

    # --- Group Interface ---
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    ng.interface.new_socket('Launchpads', in_out='INPUT', socket_type='NodeSocketCollection')
    ng.interface.new_socket('Targets',    in_out='INPUT', socket_type='NodeSocketCollection')
    ng.interface.new_socket('Repulsors',  in_out='INPUT', socket_type='NodeSocketCollection')

    param_defs = [
        ("Exit Velocity",          'NodeSocketFloat', 400.0),
        ("Attraction",             'NodeSocketFloat', 1.0),
        ("Max Speed",              'NodeSocketFloat', 400.0),   # ~16 u/frame @ 24fps
        ("Repulsor Strength",      'NodeSocketFloat', 600.0),   # stronger steering
        ("Repulsor Radius",        'NodeSocketFloat', 80.0),    # tighter envelope
        ("Repulsor Safety Margin", 'NodeSocketFloat', 60.0),
        ("Repulsor Brake Gain",    'NodeSocketFloat', 8.0),
        ("Approach Radius",        'NodeSocketFloat', 50.0),
        ("Max Turn Rate",          'NodeSocketFloat', pi * 2),  # 2π rad/s — fast turn
        ("Arrival Distance",       'NodeSocketFloat', 25.0),
        ("Torpedo Radius",         'NodeSocketFloat', 10.0),
        ("Coast Frames",           'NodeSocketFloat', 3.0),
    ]
    for name, sock_type, default in param_defs:
        sock = ng.interface.new_socket(name, in_out='INPUT', socket_type=sock_type)
        sock.default_value = default

    # --- Group I/O nodes ---
    group_in = _add_node(nodes, 'NodeGroupInput', "GroupInput", (-1400, 0))
    group_out = _add_node(nodes, 'NodeGroupOutput', "GroupOutput", (9000, 0))

    # --- Simulation Zone ---
    sim_in = _add_node(nodes, 'GeometryNodeSimulationInput', "SimInput", (-800, 0))
    sim_out = _add_node(nodes, 'GeometryNodeSimulationOutput', "SimOutput", (7000, 0))
    sim_in.pair_with_output(sim_out)

    sim_out.state_items.new('VECTOR', "Position")
    sim_out.state_items.new('VECTOR', "Velocity")
    sim_out.state_items.new('FLOAT', "Active")
    sim_out.state_items.new('FLOAT', "Arrived")
    sim_out.state_items.new('FLOAT', "Age")
    sim_out.state_items.new('FLOAT', "TargetIdx")       # -1 until launch, then rank
    sim_out.state_items.new('FLOAT', "PrevLpScale")     # per-slot previous LP scale length (for rising edge)

    # Pass-through parameter state items (GroupInput → sim zone workaround)
    param_state_names = [
        "ExitVelParam", "AttrParam", "MaxSpeedParam",
        "RepStrParam", "RepRadParam", "SafeMarginParam", "BrakeGainParam",
        "ApproachRadParam", "MaxTurnParam", "ArrDistParam", "CoastParam",
    ]
    for name in param_state_names:
        sim_out.state_items.new('FLOAT', name)

    # --- Collection Info ---
    lp_instances, _ = _build_collection_info(
        nodes, links, group_in.outputs['Launchpads'], "LP_CI", (-1200, -400),
    )
    tgt_instances, _ = _build_collection_info(
        nodes, links, group_in.outputs['Targets'], "TGT_CI", (-1200, -800),
    )
    rep_instances, _ = _build_collection_info(
        nodes, links, group_in.outputs['Repulsors'], "REP_CI", (-1200, -1200),
    )

    lp_count = _instance_count(nodes, links, lp_instances, "LP_Count", (-1000, -400))
    rep_count = _instance_count(nodes, links, rep_instances, "REP_Count", (-1000, -1200))
    tgt_count = _instance_count(nodes, links, tgt_instances, "TGT_Count", (-1000, -800))

    # Torpedo point count = lp_count * SLOTS_PER_LP. Each LP gets SLOTS_PER_LP
    # pre-allocated slots; slot j fires on the LP's (j+1)th scale rising edge.
    total_slots = _add_math_node(nodes, 'MULTIPLY', "TotalSlots", (-900, -400))
    total_slots.inputs[1].default_value = float(SLOTS_PER_LP)
    _link(links, lp_count, total_slots.inputs[0])

    mesh_line = _add_node(nodes, 'GeometryNodeMeshLine', "TorpedoPoints", (-800, -400))
    _link(links, total_slots.outputs[0], mesh_line.inputs['Count'])
    _link(links, mesh_line.outputs['Mesh'], sim_in.inputs['Geometry'])

    # Wire Group Input params → sim state inputs
    gi_to_state = [
        ("Exit Velocity",          "ExitVelParam"),
        ("Attraction",             "AttrParam"),
        ("Max Speed",              "MaxSpeedParam"),
        ("Repulsor Strength",      "RepStrParam"),
        ("Repulsor Radius",        "RepRadParam"),
        ("Repulsor Safety Margin", "SafeMarginParam"),
        ("Repulsor Brake Gain",    "BrakeGainParam"),
        ("Approach Radius",        "ApproachRadParam"),
        ("Max Turn Rate",          "MaxTurnParam"),
        ("Arrival Distance",       "ArrDistParam"),
        ("Coast Frames",           "CoastParam"),
    ]
    for gi_name, state_name in gi_to_state:
        _link(links, group_in.outputs[gi_name], sim_in.inputs[state_name])

    # Pass-through sim_in → sim_out (persist each frame)
    _link(links, sim_in.outputs['Geometry'], sim_out.inputs['Geometry'])
    for state_name in param_state_names:
        _link(links, sim_in.outputs[state_name], sim_out.inputs[state_name])

    # --- Per-point sampling: launchpad + target per torpedo ---
    point_index = _add_node(nodes, 'GeometryNodeInputIndex', "PointIdx", (-600, -300))
    pos_field = _add_node(nodes, 'GeometryNodeInputPosition', "PosField", (-600, -400))
    rot_field = _add_node(nodes, 'GeometryNodeInputInstanceRotation', "RotField", (-600, -500))
    scl_field = _add_node(nodes, 'GeometryNodeInputInstanceScale', "SclField", (-600, -600))

    # lp_idx = point_idx / SLOTS_PER_LP  (integer floor)
    lp_idx_float = _add_math_node(nodes, 'DIVIDE', "LpIdxFloat", (-500, -200))
    lp_idx_float.inputs[1].default_value = float(SLOTS_PER_LP)
    _link(links, point_index.outputs['Index'], lp_idx_float.inputs[0])

    lp_idx_floor = _add_math_node(nodes, 'FLOOR', "LpIdxFloor", (-350, -200))
    _link(links, lp_idx_float.outputs[0], lp_idx_floor.inputs[0])

    lp_idx_int = _add_node(
        nodes, 'FunctionNodeFloatToInt', "LpIdxInt", (-200, -200),
    )
    lp_idx_int.rounding_mode = 'FLOOR'
    _link(links, lp_idx_floor.outputs[0], lp_idx_int.inputs[0])

    # slot_idx = point_idx - lp_idx * SLOTS_PER_LP
    lp_idx_times_slots = _add_math_node(
        nodes, 'MULTIPLY', "LpIdxTimesSlots", (-350, -100),
    )
    lp_idx_times_slots.inputs[1].default_value = float(SLOTS_PER_LP)
    _link(links, lp_idx_floor.outputs[0], lp_idx_times_slots.inputs[0])

    slot_idx_socket = _add_math_node(
        nodes, 'SUBTRACT', "SlotIdx", (-200, -100),
    )
    # Note: SUBTRACT on ints via floats is fine since point indices < 2^24.
    # Convert point_index to float implicitly via math node.
    _link(links, point_index.outputs['Index'], slot_idx_socket.inputs[0])
    _link(links, lp_idx_times_slots.outputs[0], slot_idx_socket.inputs[1])

    lp_pos_socket = _sample_per_point(
        nodes, links, lp_instances, lp_idx_int.outputs[0],
        pos_field.outputs['Position'], 'FLOAT_VECTOR', "LP_Pos", (-400, -400),
    )
    lp_rot_socket = _sample_per_point(
        nodes, links, lp_instances, lp_idx_int.outputs[0],
        rot_field.outputs['Rotation'], 'QUATERNION', "LP_Rot", (-400, -500),
    )
    lp_scale_socket = _sample_per_point(
        nodes, links, lp_instances, lp_idx_int.outputs[0],
        scl_field.outputs['Scale'], 'FLOAT_VECTOR', "LP_Scale", (-400, -600),
    )

    # lp_fire_count: how many slots of my LP already launched before me.
    # = Σ prev_active[p] for p in same lp_idx group, p.slot_idx < my slot_idx.
    # Use Accumulate Field with Group ID = lp_idx; Trailing output gives
    # exclusive prefix over group (sum of entries before me).
    fire_count_accum = _add_node(
        nodes, 'GeometryNodeAccumulateField', "FireCountAccum", (0, -400),
    )
    fire_count_accum.data_type = 'FLOAT'
    fire_count_accum.domain = 'POINT'
    _link(links, sim_in.outputs['Active'], fire_count_accum.inputs[0])
    _link(links, lp_idx_int.outputs[0], fire_count_accum.inputs['Group Index'])
    lp_fire_count_socket = fire_count_accum.outputs['Trailing']

    # --- Launch ---
    (active_socket, launch_mask_socket, impulse_vel_socket, spawn_pos_socket,
     lp_hot_socket, _rising_edge_socket) = _build_launch(
        nodes, links,
        lp_scale_socket=lp_scale_socket,
        lp_rot_socket=lp_rot_socket,
        lp_pos_socket=lp_pos_socket,
        prev_lp_scale_socket=sim_in.outputs['PrevLpScale'],
        slot_idx_socket=slot_idx_socket.outputs[0],
        lp_fire_count_socket=lp_fire_count_socket,
        exit_vel_socket=sim_in.outputs['ExitVelParam'],
        prev_active_socket=sim_in.outputs['Active'],
        prev_velocity_socket=sim_in.outputs['Velocity'],
        prev_position_socket=sim_in.outputs['Position'],
        x_offset=600,
    )

    # Next-frame PrevLpScale = current lp_scale length (store scalar).
    lp_scale_len_cur = _add_vmath_node(
        nodes, 'LENGTH', "LpScaleLenCur", (800, -1800),
    )
    _link(links, lp_scale_socket, lp_scale_len_cur.inputs[0])

    # --- Target index assignment (launch-time order, global) ---
    # idx_on_launch = (prev_active count over all points) + (launch_mask rank
    #                  among same-frame launchers, leading).
    # Persist via TargetIdx state item: -1 initial, set on launch, kept after.
    prev_active_accum = _add_node(
        nodes, 'GeometryNodeAccumulateField', "PrevActiveAccum", (100, -1400),
    )
    prev_active_accum.data_type = 'FLOAT'
    prev_active_accum.domain = 'POINT'
    _link(links, sim_in.outputs['Active'], prev_active_accum.inputs[0])
    # 'Total' output = sum across whole domain.

    launch_mask_accum = _add_node(
        nodes, 'GeometryNodeAccumulateField', "LaunchMaskAccum", (100, -1500),
    )
    launch_mask_accum.data_type = 'FLOAT'
    launch_mask_accum.domain = 'POINT'
    _link(links, launch_mask_socket, launch_mask_accum.inputs[0])
    # 'Leading' in Blender 4.x is inclusive prefix sum (includes self). To get
    # 0-based rank among same-frame launchers: subtract self's launch_mask.

    leading_minus_self = _add_math_node(
        nodes, 'SUBTRACT', "LeadingMinusSelf", (250, -1500),
    )
    _link(links, launch_mask_accum.outputs['Leading'], leading_minus_self.inputs[0])
    _link(links, launch_mask_socket, leading_minus_self.inputs[1])

    idx_on_launch = _add_math_node(
        nodes, 'ADD', "IdxOnLaunch", (400, -1450),
    )
    _link(links, prev_active_accum.outputs['Total'], idx_on_launch.inputs[0])
    _link(links, leading_minus_self.outputs[0], idx_on_launch.inputs[1])

    # Manual mix: target_idx = (1 - launch_mask) * prev + launch_mask * new
    one_minus_lm = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusLM", (400, -1500),
    )
    one_minus_lm.inputs[0].default_value = 1.0
    _link(links, launch_mask_socket, one_minus_lm.inputs[1])

    keep_prev = _add_math_node(nodes, 'MULTIPLY', "KeepPrev", (550, -1500))
    _link(links, one_minus_lm.outputs[0], keep_prev.inputs[0])
    _link(links, sim_in.outputs['TargetIdx'], keep_prev.inputs[1])

    take_new = _add_math_node(nodes, 'MULTIPLY', "TakeNew", (550, -1400))
    _link(links, launch_mask_socket, take_new.inputs[0])
    _link(links, idx_on_launch.outputs[0], take_new.inputs[1])

    target_idx_socket = _add_math_node(
        nodes, 'ADD', "TargetIdx", (700, -1450),
    )
    _link(links, keep_prev.outputs[0], target_idx_socket.inputs[0])
    _link(links, take_new.outputs[0], target_idx_socket.inputs[1])

    # Clamp idx to [0, tgt_count-1] for safe sampling.
    tgt_count_minus_1 = _add_math_node(
        nodes, 'SUBTRACT', "TgtCountM1", (500, -1700),
    )
    tgt_count_minus_1.inputs[1].default_value = 1.0
    _link(links, tgt_count, tgt_count_minus_1.inputs[0])

    # max(idx, 0)
    idx_clamp_lo = _add_math_node(
        nodes, 'MAXIMUM', "IdxClampLo", (700, -1700),
    )
    idx_clamp_lo.inputs[1].default_value = 0.0
    _link(links, target_idx_socket.outputs[0], idx_clamp_lo.inputs[0])

    # min(idx, tgt_count-1)
    idx_clamped = _add_math_node(
        nodes, 'MINIMUM', "IdxClamped", (900, -1700),
    )
    _link(links, idx_clamp_lo.outputs[0], idx_clamped.inputs[0])
    _link(links, tgt_count_minus_1.outputs[0], idx_clamped.inputs[1])

    # Cast float → int for Sample Index.
    idx_int = _add_node(
        nodes, 'FunctionNodeFloatToInt', "IdxInt", (1100, -1700),
    )
    idx_int.rounding_mode = 'FLOOR'
    _link(links, idx_clamped.outputs[0], idx_int.inputs[0])

    target_pos_socket = _sample_per_point(
        nodes, links, tgt_instances, idx_int.outputs[0],
        pos_field.outputs['Position'], 'FLOAT_VECTOR', "TGT_Pos", (1300, -1700),
    )

    # --- Age + coast gate ---
    new_age = _add_math_node(nodes, 'ADD', "AgeIncrement", (1800, -200))
    _link(links, sim_in.outputs['Age'], new_age.inputs[0])
    _link(links, active_socket, new_age.inputs[1])

    coast_check = _add_math_node(nodes, 'GREATER_THAN', "CoastCheck", (2000, -200))
    _link(links, new_age.outputs[0], coast_check.inputs[0])
    _link(links, sim_in.outputs['CoastParam'], coast_check.inputs[1])

    # --- Repulsor forces (needs current position, velocity, dist_to_target) ---
    # Use spawn_pos (current frame) and impulse_vel (initial vel) for repulsor compute.
    # dist_to_target pre-computed here for repulsor gate.
    to_target_pre = _add_vmath_node(
        nodes, 'SUBTRACT', "ToTargetPre", (600, -1800),
    )
    _link(links, target_pos_socket, to_target_pre.inputs[0])
    _link(links, spawn_pos_socket, to_target_pre.inputs[1])

    dist_to_target_pre = _add_vmath_node(
        nodes, 'LENGTH', "DistToTargetPre", (800, -1800),
    )
    _link(links, to_target_pre.outputs['Vector'], dist_to_target_pre.inputs[0])

    steering_socket, brake_cap_socket = _build_repulsor_forces(
        nodes, links,
        position_socket=spawn_pos_socket,
        velocity_socket=impulse_vel_socket,
        target_pos_socket=target_pos_socket,
        dist_to_target_socket=dist_to_target_pre.outputs['Value'],
        rep_instances_socket=rep_instances,
        rep_count_socket=rep_count,
        rep_strength_socket=sim_in.outputs['RepStrParam'],
        rep_radius_socket=sim_in.outputs['RepRadParam'],
        safety_margin_socket=sim_in.outputs['SafeMarginParam'],
        brake_gain_socket=sim_in.outputs['BrakeGainParam'],
        approach_radius_socket=sim_in.outputs['ApproachRadParam'],
        x_offset=1000,
    )

    # --- Velocity integration ---
    vel_socket, pos_socket, dist_socket = _build_velocity_integration(
        nodes, links,
        velocity_socket=impulse_vel_socket,
        position_socket=spawn_pos_socket,
        target_pos_socket=target_pos_socket,
        attraction_socket=sim_in.outputs['AttrParam'],
        steering_dir_socket=steering_socket,
        brake_cap_socket=brake_cap_socket,
        launch_mask_socket=launch_mask_socket,
        impulse_vel_socket=impulse_vel_socket,
        active_socket=active_socket,
        arrived_socket=sim_in.outputs['Arrived'],
        max_speed_socket=sim_in.outputs['MaxSpeedParam'],
        max_turn_socket=sim_in.outputs['MaxTurnParam'],
        delta_time_socket=sim_in.outputs['Delta Time'],
        coast_gate_socket=coast_check.outputs[0],
        x_offset=3200,
    )

    # --- Arrival detection + overshoot guard ---
    arrived_socket, final_pos_socket, final_vel_socket = _build_arrival_detection(
        nodes, links,
        position_socket=pos_socket,
        target_pos_socket=target_pos_socket,
        velocity_socket=vel_socket,
        dist_to_target_socket=dist_socket,
        arrival_dist_socket=sim_in.outputs['ArrDistParam'],
        delta_time_socket=sim_in.outputs['Delta Time'],
        prev_arrived_socket=sim_in.outputs['Arrived'],
        x_offset=7600,
    )

    # --- Wire to Sim Zone output ---
    _link(links, final_pos_socket, sim_out.inputs['Position'])
    _link(links, final_vel_socket, sim_out.inputs['Velocity'])
    _link(links, active_socket, sim_out.inputs['Active'])
    _link(links, arrived_socket, sim_out.inputs['Arrived'])
    _link(links, new_age.outputs[0], sim_out.inputs['Age'])
    _link(links, target_idx_socket.outputs[0], sim_out.inputs['TargetIdx'])
    _link(links, lp_scale_len_cur.outputs['Value'], sim_out.inputs['PrevLpScale'])

    # --- Post-sim visual output ---
    mat = _create_torpedo_material()
    final_geo = _build_visual_output(
        nodes, links,
        geo_socket=sim_out.outputs['Geometry'],
        position_socket=sim_out.outputs['Position'],
        active_socket=sim_out.outputs['Active'],
        arrived_socket=sim_out.outputs['Arrived'],
        torpedo_radius_socket=group_in.outputs['Torpedo Radius'],
        material=mat,
        x_offset=7400,
    )

    _link(links, final_geo, group_out.inputs['Geometry'])

    print("  Built TorpedoEffect v2 (slerp + tangent-steering + brake-cap + approach-corridor).")
    return ng


# ============================================================
# Test Scene Setup
# ============================================================

def setup_test_scene(num_launchpads=3, num_targets=10):
    """Test scene. Targets may outnumber launchpads; the Nth torpedo to launch
    claims the Nth target (launch-time pairing). Each launchpad fires exactly
    once — so torpedo_count == num_launchpads.

    Repulsor layout for v2 physics validation:
      - 1 repulsor mid-flight (tests tangent-steering + brake)
      - 1 repulsor near early targets (tests approach corridor)
    """
    for col_name in (LAUNCHPAD_COLLECTION, TARGET_COLLECTION, REPULSOR_COLLECTION):
        if col_name not in bpy.data.collections:
            col = bpy.data.collections.new(col_name)
            bpy.context.scene.collection.children.link(col)

    lp_col = bpy.data.collections[LAUNCHPAD_COLLECTION]
    tgt_col = bpy.data.collections[TARGET_COLLECTION]
    rep_col = bpy.data.collections[REPULSOR_COLLECTION]

    for col in (lp_col, tgt_col, rep_col):
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Launchpads (arrow empties). Spread around origin at different headings
    # so torpedoes must curve toward targets on the right — exercises slerp
    # turn-rate limit and attraction steering.
    lp_layouts = [
        # (location, rotation_euler) — arrow +Z points in firing direction
        ((-300, -200, 0),  (0,             radians(90),  0)),              # fire +X
        ((-100, -300, 0),  (radians(-90),  0,            0)),              # fire +Y (up)
        ((-400,  200, 0),  (0,             radians(90),  radians(-45))),   # fire +X-Y diag
    ]
    for i in range(num_launchpads):
        loc, rot = lp_layouts[i % len(lp_layouts)]
        empty = bpy.data.objects.new(f"LP.{i+1:03d}", None)
        empty.empty_display_type = 'SINGLE_ARROW'
        empty.empty_display_size = 40.0
        empty.location = loc
        empty.rotation_euler = rot
        empty.scale = (0, 0, 0)
        lp_col.objects.link(empty)

        activate_frame = 10 + i * 8
        empty.keyframe_insert(data_path="scale", frame=1)
        empty.scale = (1, 1, 1)
        empty.keyframe_insert(data_path="scale", frame=activate_frame)

        if empty.animation_data and empty.animation_data.action:
            action = empty.animation_data.action
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for fcurve in bag.fcurves:
                            for kp in fcurve.keyframe_points:
                                kp.interpolation = 'CONSTANT'

    # Targets (empties, right side). May exceed launchpad count — only the
    # first num_launchpads (by launch order) will be hit.
    for i in range(num_targets):
        empty = bpy.data.objects.new(f"TGT.{i+1:03d}", None)
        empty.empty_display_type = 'SPHERE'
        empty.empty_display_size = 20.0
        empty.location = (500, -250 + i * 55, 40)
        tgt_col.objects.link(empty)

    # Repulsors: placed on direct torpedo→target lines so flight paths
    # actually intersect envelopes. Envelope radius = Repulsor Radius (80).
    rep_layouts = [
        ((100, -200, 0),  "mid-path torpedo 0 → TGT.001"),
        ((200,    0, 0),  "mid-path torpedo 1 → TGT.~"),
        ((100,  200, 20), "mid-path torpedo 2 → TGT.~"),
    ]
    for i, (loc, note) in enumerate(rep_layouts):
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=80.0)
        mesh = bpy.data.meshes.new(f"REP.{i+1:03d}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"REP.{i+1:03d}", mesh)
        obj.location = loc
        obj.display_type = 'WIRE'
        rep_col.objects.link(obj)
        print(f"  Repulsor REP.{i+1:03d} @ {loc} — {note}")

    print(f"  Test scene: {num_launchpads} launchpads, {num_targets} targets, {len(rep_layouts)} repulsors.")


# ============================================================
# Entry Point
# ============================================================

def _assign_modifier_collections(mod, ng, found_collections):
    name_to_col = {
        'Launchpads': found_collections.get(LAUNCHPAD_COLLECTION),
        'Targets':    found_collections.get(TARGET_COLLECTION),
        'Repulsors':  found_collections.get(REPULSOR_COLLECTION),
    }
    for sock in ng.interface.items_tree:
        if getattr(sock, 'in_out', None) != 'INPUT':
            continue
        col = name_to_col.get(sock.name)
        if col is not None:
            mod[sock.identifier] = col


def main():
    found = _resolve_collections()

    ctrl = _create_controller_mesh()
    ng = build_torpedo_effect()

    for mod in list(ctrl.modifiers):
        ctrl.modifiers.remove(mod)
    mod = ctrl.modifiers.new(NODE_GROUP_NAME, 'NODES')
    mod.node_group = ng

    _assign_modifier_collections(mod, ng, found)
    ctrl.update_tag()

    # Extend scene range so sim zone has room to evaluate through arrival.
    scene = bpy.context.scene
    if scene.frame_end < 300:
        scene.frame_end = 300

    present = ", ".join(k for k, v in found.items() if v is not None) or "(none)"
    print(f"\nTorpedoEffect v2 applied to {CONTROLLER_NAME}.")
    print(f"Collections wired: {present}")
    print(f"Scene range: {scene.frame_start}–{scene.frame_end}")
    print("Physics: slerp turn-limit + tangent-steering + brake-cap + approach-corridor.")


if __name__ == "__main__":
    if LAUNCHPAD_COLLECTION not in bpy.data.collections:
        print("No collections found — creating test scene...")
        setup_test_scene(num_launchpads=4)

    main()
