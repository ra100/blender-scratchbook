"""
Torpedo Effect — Collection-Driven Scene Setup Script
======================================================
Creates the scene objects and GeoNodes node tree for the torpedo animation.
Reads Launchpads, Targets, and Repulsors collections to generate a dynamic
node tree supporting N torpedoes. Re-run after adding/removing objects.

No Python handlers are used during simulation — everything runs inside
Geometry Nodes with a Simulation Zone.

Usage:
    blender torpedo_001.blend --python torpedo_physics_handler.py
"""

import bpy
import bmesh
from math import radians
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
ATTRACTION_REF_DISTANCE = 1000.0


# ============================================================
# Helpers (same pattern as shield_ripple_effect.py)
# ============================================================

def _add_node(nodes, type_str, label, location):
    """Create a node, set its label and location."""
    node = nodes.new(type_str)
    node.label = label
    node.name = label
    node.location = location
    return node


def _add_math_node(nodes, operation, label, location):
    """Create a ShaderNodeMath with a preset operation."""
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.label = label
    node.name = label
    node.location = location
    return node


def _link(links, from_socket, to_socket):
    """Create a node link."""
    links.new(from_socket, to_socket)


# ============================================================
# Validation
# ============================================================

def _validate_collections():
    """Read and validate Launchpads, Targets, Repulsors collections.

    Returns sorted (launchpads, targets, repulsors) lists.
    Raises RuntimeError if collections are missing or empty.
    """
    for name in (LAUNCHPAD_COLLECTION, TARGET_COLLECTION):
        col = bpy.data.collections.get(name)
        if col is None:
            raise RuntimeError(
                f"Collection '{name}' not found. Create it and add objects."
            )
        if len(col.objects) == 0:
            raise RuntimeError(
                f"Collection '{name}' is empty. Add at least one object."
            )

    launchpads = sorted(
        bpy.data.collections[LAUNCHPAD_COLLECTION].objects,
        key=lambda o: o.name,
    )
    targets = sorted(
        bpy.data.collections[TARGET_COLLECTION].objects,
        key=lambda o: o.name,
    )

    n = min(len(launchpads), len(targets))
    if len(launchpads) != len(targets):
        print(f"WARNING: {len(launchpads)} launchpads, {len(targets)} targets. "
              f"Using {n} torpedoes (min of both).")
    launchpads = launchpads[:n]
    targets = targets[:n]

    rep_col = bpy.data.collections.get(REPULSOR_COLLECTION)
    repulsors = []
    if rep_col and len(rep_col.objects) > 0:
        repulsors = sorted(rep_col.objects, key=lambda o: o.name)

    return launchpads, targets, repulsors


# ============================================================
# Node Helpers
# ============================================================

def _create_object_info_nodes(nodes, objects, label_prefix, x, y_start, y_step):
    """Create Object Info nodes for a list of scene objects.

    Returns list of Object Info nodes with direct refs and ORIGINAL space.
    """
    info_nodes = []
    for i, obj in enumerate(objects):
        info = _add_node(
            nodes, 'GeometryNodeObjectInfo',
            f"{label_prefix}_{obj.name}", (x, y_start - i * y_step),
        )
        info.inputs['Object'].default_value = obj
        info.transform_space = 'ORIGINAL'
        info_nodes.append(info)
    return info_nodes


def _build_cascading_mux(nodes, links, per_torpedo_sockets, data_type,
                         label_prefix, x_offset, y_offset=0):
    """Build a cascading Index+Compare+Mix chain for per-torpedo selection.

    per_torpedo_sockets: list of sockets, one per torpedo (e.g. target positions)
    data_type: 'VECTOR', 'FLOAT', or 'ROTATION'
    Returns the result socket that holds the correct value per point.
    """
    if len(per_torpedo_sockets) == 1:
        return per_torpedo_sockets[0]

    index_node = _add_node(
        nodes, 'GeometryNodeInputIndex',
        f"{label_prefix}_Index", (x_offset, y_offset),
    )

    result_socket = per_torpedo_sockets[0]
    for i in range(1, len(per_torpedo_sockets)):
        compare = _add_node(
            nodes, 'FunctionNodeCompare',
            f"{label_prefix}_Is{i}", (x_offset + 200, y_offset - i * 150),
        )
        compare.data_type = 'INT'
        compare.operation = 'EQUAL'
        compare.inputs[3].default_value = i  # B = i (INT B is socket index 3)
        _link(links, index_node.outputs['Index'], compare.inputs[2])  # A = Index (INT A is socket index 2)

        mix = _add_node(
            nodes, 'ShaderNodeMix',
            f"{label_prefix}_Mix{i}", (x_offset + 400, y_offset - i * 150),
        )
        mix.data_type = data_type
        mix.clamp_factor = True
        _link(links, compare.outputs['Result'], mix.inputs['Factor'])

        # ShaderNodeMix socket indices by data_type:
        #   FLOAT:    A=2, B=3, Result=0
        #   VECTOR:   A=4, B=5, Result=1
        #   RGBA:     A=6, B=7, Result=2
        #   ROTATION: A=8, B=9, Result=3
        ab_out = {'FLOAT': (2, 3, 0), 'VECTOR': (4, 5, 1),
                  'ROTATION': (8, 9, 3)}[data_type]
        _link(links, result_socket, mix.inputs[ab_out[0]])
        _link(links, per_torpedo_sockets[i], mix.inputs[ab_out[1]])
        result_socket = mix.outputs[ab_out[2]]

    return result_socket


def _build_latch(nodes, links, check_socket, prev_socket, label, location):
    """Build a MAXIMUM latch: once value reaches 1, stays 1 forever."""
    latch = _add_math_node(nodes, 'MAXIMUM', label, location)
    _link(links, prev_socket, latch.inputs[0])
    _link(links, check_socket, latch.inputs[1])
    return latch.outputs[0]


# ============================================================
# Sub-builders
# ============================================================

def _build_launch(nodes, links, lp_scale_sockets, lp_rotation_sockets,
                  lp_position_sockets, exit_vel_socket,
                  prev_active_socket, prev_velocity_socket, prev_position_socket,
                  x_offset):
    """Build activation detection, launch impulse, and spawn position.

    Returns (active_socket, launch_mask_socket, initial_velocity_socket,
             spawn_position_socket).
    """
    x = x_offset

    # --- Per-torpedo launchpad scale (via cascading mux, VECTOR type) ---
    lp_scale_socket = _build_cascading_mux(
        nodes, links, lp_scale_sockets, 'VECTOR',
        "LPScale", x, y_offset=-200,
    )

    # --- Per-torpedo launchpad rotation (via cascading mux, ROTATION type) ---
    lp_rot_socket = _build_cascading_mux(
        nodes, links, lp_rotation_sockets, 'ROTATION',
        "LPRot", x, y_offset=-800,
    )

    # --- Per-torpedo launchpad position (via cascading mux) ---
    lp_pos_socket = _build_cascading_mux(
        nodes, links, lp_position_sockets, 'VECTOR',
        "LPPos", x, y_offset=-1400,
    )

    # --- Activation: scale vector length > threshold ---
    scale_len = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ScaleLength", (x + 600, -200),
    )
    scale_len.operation = 'LENGTH'
    _link(links, lp_scale_socket, scale_len.inputs[0])

    activation_check = _add_math_node(
        nodes, 'GREATER_THAN', "ActivationCheck", (x + 800, -200),
    )
    activation_check.inputs[1].default_value = ACTIVATION_THRESHOLD
    _link(links, scale_len.outputs['Value'], activation_check.inputs[0])

    # --- Active latch (MAXIMUM) ---
    active_socket = _build_latch(
        nodes, links,
        activation_check.outputs[0], prev_active_socket,
        "ActiveLatch", (x + 1000, -200),
    )

    # --- Launch mask: Active_current - Active_previous = 1 on first frame ---
    launch_mask = _add_math_node(
        nodes, 'SUBTRACT', "LaunchMask", (x + 1200, -200),
    )
    _link(links, active_socket, launch_mask.inputs[0])
    _link(links, prev_active_socket, launch_mask.inputs[1])

    # --- Direction from rotation: Rotate (0,1,0) by launchpad rotation ---
    rotate_vec = _add_node(
        nodes, 'FunctionNodeRotateVector',
        "RotateForward", (x + 600, -600),
    )
    rotate_vec.inputs['Vector'].default_value = (0.0, 0.0, 1.0)  # +Z = SINGLE_ARROW forward
    _link(links, lp_rot_socket, rotate_vec.inputs['Rotation'])

    # --- Launch impulse: forward * exit_velocity * launch_mask ---
    impulse_scale = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ImpulseScale", (x + 800, -600),
    )
    impulse_scale.operation = 'SCALE'
    _link(links, rotate_vec.outputs['Vector'], impulse_scale.inputs[0])
    _link(links, exit_vel_socket, impulse_scale.inputs[3])

    impulse_masked = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ImpulseMasked", (x + 1000, -600),
    )
    impulse_masked.operation = 'SCALE'
    _link(links, impulse_scale.outputs['Vector'], impulse_masked.inputs[0])
    _link(links, launch_mask.outputs[0], impulse_masked.inputs[3])

    # --- Add impulse to previous velocity ---
    initial_velocity = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "InitialVelocity", (x + 1200, -600),
    )
    initial_velocity.operation = 'ADD'
    _link(links, prev_velocity_socket, initial_velocity.inputs[0])
    _link(links, impulse_masked.outputs['Vector'], initial_velocity.inputs[1])

    # --- Spawn position: on launch frame, snap to launchpad position ---
    spawn_pos = _add_node(
        nodes, 'ShaderNodeMix',
        "SpawnPos", (x + 1200, -1400),
    )
    spawn_pos.data_type = 'VECTOR'
    spawn_pos.clamp_factor = True
    _link(links, launch_mask.outputs[0], spawn_pos.inputs['Factor'])
    _link(links, prev_position_socket, spawn_pos.inputs[4])   # A: keep prev pos
    _link(links, lp_pos_socket, spawn_pos.inputs[5])           # B: launchpad pos

    return (active_socket, launch_mask.outputs[0],
            initial_velocity.outputs['Vector'], spawn_pos.outputs[1])


def _build_velocity_integration(nodes, links, velocity_socket, position_socket,
                                target_pos_socket, attraction_socket,
                                repulsor_force_socket, active_socket,
                                arrived_socket, max_speed_socket,
                                max_accel_socket, delta_time_socket,
                                coast_gate_socket, x_offset):
    """Build attraction force, velocity update, speed clamping, position update.

    coast_gate_socket: 0 during coast phase, 1 after. Gates attraction + repulsors.
    max_accel_socket: caps force magnitude per frame for smooth speed transitions.
    Returns (clamped_velocity_socket, new_position_socket, dist_to_target_socket).
    """
    x = x_offset

    # --- Direction to target ---
    to_target = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ToTarget", (x, -500),
    )
    to_target.operation = 'SUBTRACT'
    _link(links, target_pos_socket, to_target.inputs[0])
    _link(links, position_socket, to_target.inputs[1])

    dist_to_target = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "DistToTarget", (x + 200, -500),
    )
    dist_to_target.operation = 'LENGTH'
    _link(links, to_target.outputs['Vector'], dist_to_target.inputs[0])

    norm_to_target = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "NormToTarget", (x + 200, -600),
    )
    norm_to_target.operation = 'NORMALIZE'
    _link(links, to_target.outputs['Vector'], norm_to_target.inputs[0])

    # --- Attraction boost: attraction * (1 + RefDist / dist_to_target) ---
    ref_over_dist = _add_math_node(
        nodes, 'DIVIDE', "RefOverDist", (x + 400, -700),
    )
    ref_over_dist.inputs[0].default_value = ATTRACTION_REF_DISTANCE
    _link(links, dist_to_target.outputs['Value'], ref_over_dist.inputs[1])

    one_plus_boost = _add_math_node(
        nodes, 'ADD', "OnePlusBoost", (x + 400, -600),
    )
    one_plus_boost.inputs[0].default_value = 1.0
    _link(links, ref_over_dist.outputs[0], one_plus_boost.inputs[1])

    effective_attr = _add_math_node(
        nodes, 'MULTIPLY', "EffectiveAttraction", (x + 600, -600),
    )
    _link(links, attraction_socket, effective_attr.inputs[0])
    _link(links, one_plus_boost.outputs[0], effective_attr.inputs[1])

    # --- Attraction force vector ---
    attr_force = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "AttractionForce", (x + 600, -500),
    )
    attr_force.operation = 'SCALE'
    _link(links, norm_to_target.outputs['Vector'], attr_force.inputs[0])
    _link(links, effective_attr.outputs[0], attr_force.inputs[3])

    # --- Gate attraction + repulsors by coast phase ---
    gated_attr = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "GatedAttraction", (x + 700, -500),
    )
    gated_attr.operation = 'SCALE'
    _link(links, attr_force.outputs['Vector'], gated_attr.inputs[0])
    _link(links, coast_gate_socket, gated_attr.inputs[3])

    gated_rep = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "GatedRepulsor", (x + 700, -400),
    )
    gated_rep.operation = 'SCALE'
    _link(links, repulsor_force_socket, gated_rep.inputs[0])
    _link(links, coast_gate_socket, gated_rep.inputs[3])

    # --- Total force = attraction + repulsor (both gated by coast) ---
    total_force = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "TotalForce", (x + 800, -500),
    )
    total_force.operation = 'ADD'
    _link(links, gated_attr.outputs['Vector'], total_force.inputs[0])
    _link(links, gated_rep.outputs['Vector'], total_force.inputs[1])

    # --- Clamp force magnitude to max acceleration ---
    force_len = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ForceLen", (x + 900, -600),
    )
    force_len.operation = 'LENGTH'
    _link(links, total_force.outputs['Vector'], force_len.inputs[0])

    clamped_force_len = _add_math_node(
        nodes, 'MINIMUM', "ClampedForceLen", (x + 900, -700),
    )
    _link(links, force_len.outputs['Value'], clamped_force_len.inputs[0])
    _link(links, max_accel_socket, clamped_force_len.inputs[1])

    accel_scale = _add_math_node(
        nodes, 'DIVIDE', "AccelScale", (x + 900, -800),
    )
    _link(links, clamped_force_len.outputs[0], accel_scale.inputs[0])
    _link(links, force_len.outputs['Value'], accel_scale.inputs[1])

    accel_cap = _add_math_node(
        nodes, 'MINIMUM', "AccelCap", (x + 1000, -800),
    )
    accel_cap.inputs[1].default_value = 1.0
    _link(links, accel_scale.outputs[0], accel_cap.inputs[0])

    clamped_force = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ClampedForce", (x + 1000, -500),
    )
    clamped_force.operation = 'SCALE'
    _link(links, total_force.outputs['Vector'], clamped_force.inputs[0])
    _link(links, accel_cap.outputs[0], clamped_force.inputs[3])

    # --- Force * dt ---
    force_dt = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ForceDt", (x + 1200, -500),
    )
    force_dt.operation = 'SCALE'
    _link(links, clamped_force.outputs['Vector'], force_dt.inputs[0])
    _link(links, delta_time_socket, force_dt.inputs[3])

    # --- New velocity = old + force*dt ---
    new_vel = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "NewVel", (x + 1200, -500),
    )
    new_vel.operation = 'ADD'
    _link(links, velocity_socket, new_vel.inputs[0])
    _link(links, force_dt.outputs['Vector'], new_vel.inputs[1])

    # --- Speed clamping ---
    vel_len = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "VelLength", (x + 1200, -600),
    )
    vel_len.operation = 'LENGTH'
    _link(links, new_vel.outputs['Vector'], vel_len.inputs[0])

    clamped_len = _add_math_node(
        nodes, 'MINIMUM', "ClampedLen", (x + 1400, -600),
    )
    _link(links, vel_len.outputs['Value'], clamped_len.inputs[0])
    _link(links, max_speed_socket, clamped_len.inputs[1])

    scale_factor = _add_math_node(
        nodes, 'DIVIDE', "ScaleFactor", (x + 1400, -700),
    )
    _link(links, clamped_len.outputs[0], scale_factor.inputs[0])
    _link(links, vel_len.outputs['Value'], scale_factor.inputs[1])

    # Cap at 1.0 (DIVIDE returns 0 for 0/0 which is safe)
    cap = _add_math_node(nodes, 'MINIMUM', "Cap", (x + 1600, -700))
    cap.inputs[1].default_value = 1.0
    _link(links, scale_factor.outputs[0], cap.inputs[0])

    clamped_vel = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "ClampedVel", (x + 1600, -500),
    )
    clamped_vel.operation = 'SCALE'
    _link(links, new_vel.outputs['Vector'], clamped_vel.inputs[0])
    _link(links, cap.outputs[0], clamped_vel.inputs[3])

    # --- Active/Arrived masking on velocity ---
    one_minus_arrived = _add_math_node(
        nodes, 'SUBTRACT', "OneMinusArrived", (x + 1800, -600),
    )
    one_minus_arrived.inputs[0].default_value = 1.0
    _link(links, arrived_socket, one_minus_arrived.inputs[1])

    active_mask = _add_math_node(
        nodes, 'MULTIPLY', "ActiveMask", (x + 1800, -500),
    )
    _link(links, active_socket, active_mask.inputs[0])
    _link(links, one_minus_arrived.outputs[0], active_mask.inputs[1])

    masked_vel = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "MaskedVel", (x + 2000, -500),
    )
    masked_vel.operation = 'SCALE'
    _link(links, clamped_vel.outputs['Vector'], masked_vel.inputs[0])
    _link(links, active_mask.outputs[0], masked_vel.inputs[3])

    # --- Position update: pos + vel * dt ---
    vel_dt = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "VelDt", (x + 2200, -500),
    )
    vel_dt.operation = 'SCALE'
    _link(links, masked_vel.outputs['Vector'], vel_dt.inputs[0])
    _link(links, delta_time_socket, vel_dt.inputs[3])

    new_pos = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "NewPos", (x + 2400, -500),
    )
    new_pos.operation = 'ADD'
    _link(links, position_socket, new_pos.inputs[0])
    _link(links, vel_dt.outputs['Vector'], new_pos.inputs[1])

    # --- Gate position: only move if active ---
    start_pos = _add_node(
        nodes, 'GeometryNodeInputPosition',
        "StartPosition", (x + 2400, -700),
    )

    pos_select = _add_node(
        nodes, 'ShaderNodeMix',
        "PosSelect", (x + 2600, -500),
    )
    pos_select.data_type = 'VECTOR'
    pos_select.clamp_factor = True
    _link(links, active_socket, pos_select.inputs['Factor'])
    _link(links, start_pos.outputs['Position'], pos_select.inputs[4])  # A (inactive)
    _link(links, new_pos.outputs['Vector'], pos_select.inputs[5])  # B (active)

    return (
        masked_vel.outputs['Vector'],
        pos_select.outputs[1],
        dist_to_target.outputs['Value'],
    )


def _build_arrival_detection(nodes, links, position_socket, target_pos_socket,
                             velocity_socket, dist_to_target_socket,
                             arrival_dist_socket, prev_arrived_socket, x_offset):
    """Build arrival detection with position snap and velocity zero.

    Returns (arrived_socket, final_position_socket, final_velocity_socket).
    """
    x = x_offset

    # --- Arrival check: dist < arrival_distance ---
    arrival_check = _add_math_node(
        nodes, 'LESS_THAN', "ArrivalCheck", (x, -400),
    )
    _link(links, dist_to_target_socket, arrival_check.inputs[0])
    _link(links, arrival_dist_socket, arrival_check.inputs[1])

    # --- Arrived latch (MAXIMUM) ---
    arrived_socket = _build_latch(
        nodes, links,
        arrival_check.outputs[0], prev_arrived_socket,
        "ArrivedLatch", (x + 200, -400),
    )

    # --- First arrival frame: Arrived_now - Arrived_prev ---
    first_arrival = _add_math_node(
        nodes, 'SUBTRACT', "FirstArrival", (x + 400, -400),
    )
    _link(links, arrived_socket, first_arrival.inputs[0])
    _link(links, prev_arrived_socket, first_arrival.inputs[1])

    # --- Position snap: Mix(first_arrival, computed_pos, target_pos) ---
    pos_snap = _add_node(
        nodes, 'ShaderNodeMix',
        "PosSnap", (x + 600, -400),
    )
    pos_snap.data_type = 'VECTOR'
    pos_snap.clamp_factor = True
    _link(links, first_arrival.outputs[0], pos_snap.inputs['Factor'])
    _link(links, position_socket, pos_snap.inputs[4])  # A (not arriving)
    _link(links, target_pos_socket, pos_snap.inputs[5])  # B (snap to target)

    # --- Velocity zero on arrival ---
    # ShaderNodeMix: factor=0→A, factor=1→B
    # arrived=0 → A (computed vel), arrived=1 → B (zero)
    vel_zero = _add_node(
        nodes, 'ShaderNodeMix',
        "VelZero", (x + 600, -600),
    )
    vel_zero.data_type = 'VECTOR'
    vel_zero.clamp_factor = True
    _link(links, arrived_socket, vel_zero.inputs['Factor'])
    _link(links, velocity_socket, vel_zero.inputs[4])  # A = computed vel
    vel_zero.inputs[5].default_value = (0.0, 0.0, 0.0)  # B = zero

    return arrived_socket, pos_snap.outputs[1], vel_zero.outputs[1]


def _build_repulsor_forces(nodes, links, position_socket, target_pos_socket,
                           dist_to_target_socket, repulsor_info_nodes,
                           rep_strength_socket, rep_radius_socket, x_offset):
    """Build repulsor avoidance forces (per-repulsor linear falloff + pass-gate).

    Returns total_repulsor_force_socket.
    """
    if not repulsor_info_nodes:
        # No repulsors — return a zero vector constant
        zero_vec = _add_node(
            nodes, 'FunctionNodeInputVector',
            "ZeroRepForce", (x_offset, -1000),
        )
        zero_vec.vector = (0.0, 0.0, 0.0)
        return zero_vec.outputs[0]

    x = x_offset
    force_sockets = []

    for i, rep_info in enumerate(repulsor_info_nodes):
        y = -1000 - i * 400

        # away = torpedo_pos - repulsor_pos
        away = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"Away_R{i}", (x, y),
        )
        away.operation = 'SUBTRACT'
        _link(links, position_socket, away.inputs[0])
        _link(links, rep_info.outputs['Location'], away.inputs[1])

        dist_rep = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"DistRep_{i}", (x + 200, y),
        )
        dist_rep.operation = 'LENGTH'
        _link(links, away.outputs['Vector'], dist_rep.inputs[0])

        # falloff = max(0, 1 - dist/radius)
        dist_norm = _add_math_node(
            nodes, 'DIVIDE', f"RepDistNorm_{i}", (x + 200, y - 100),
        )
        _link(links, dist_rep.outputs['Value'], dist_norm.inputs[0])
        _link(links, rep_radius_socket, dist_norm.inputs[1])

        falloff_sub = _add_math_node(
            nodes, 'SUBTRACT', f"RepFalloffSub_{i}", (x + 400, y - 100),
        )
        falloff_sub.inputs[0].default_value = 1.0
        _link(links, dist_norm.outputs[0], falloff_sub.inputs[1])

        falloff_clamp = _add_math_node(
            nodes, 'MAXIMUM', f"RepFalloff_{i}", (x + 400, y),
        )
        falloff_clamp.inputs[1].default_value = 0.0
        _link(links, falloff_sub.outputs[0], falloff_clamp.inputs[0])

        norm_away = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"NormAway_{i}", (x + 400, y + 100),
        )
        norm_away.operation = 'NORMALIZE'
        _link(links, away.outputs['Vector'], norm_away.inputs[0])

        # strength * falloff
        strength_falloff = _add_math_node(
            nodes, 'MULTIPLY', f"StrFalloff_{i}", (x + 600, y),
        )
        _link(links, rep_strength_socket, strength_falloff.inputs[0])
        _link(links, falloff_clamp.outputs[0], strength_falloff.inputs[1])

        # rep_force = normalize(away) * strength * falloff
        rep_force = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"RepForce_{i}", (x + 600, y + 100),
        )
        rep_force.operation = 'SCALE'
        _link(links, norm_away.outputs['Vector'], rep_force.inputs[0])
        _link(links, strength_falloff.outputs[0], rep_force.inputs[3])

        # --- Pass-gate: only repulse if torpedo hasn't passed repulsor ---
        rep_to_target = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"RepToTarget_{i}", (x + 200, y - 200),
        )
        rep_to_target.operation = 'SUBTRACT'
        _link(links, target_pos_socket, rep_to_target.inputs[0])
        _link(links, rep_info.outputs['Location'], rep_to_target.inputs[1])

        dist_rep_target = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"DistRepTarget_{i}", (x + 400, y - 200),
        )
        dist_rep_target.operation = 'LENGTH'
        _link(links, rep_to_target.outputs['Vector'], dist_rep_target.inputs[0])

        rep_gate = _add_math_node(
            nodes, 'GREATER_THAN', f"RepGate_{i}", (x + 600, y - 200),
        )
        _link(links, dist_to_target_socket, rep_gate.inputs[0])
        _link(links, dist_rep_target.outputs['Value'], rep_gate.inputs[1])

        gated_rep = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"GatedRep_{i}", (x + 800, y),
        )
        gated_rep.operation = 'SCALE'
        _link(links, rep_force.outputs['Vector'], gated_rep.inputs[0])
        _link(links, rep_gate.outputs[0], gated_rep.inputs[3])

        force_sockets.append(gated_rep.outputs['Vector'])

    # Sum all repulsor forces
    if len(force_sockets) == 1:
        return force_sockets[0]

    result = force_sockets[0]
    for i in range(1, len(force_sockets)):
        add_forces = _add_node(
            nodes, 'ShaderNodeVectorMath',
            f"SumRep_{i}", (x + 1000, -1000 - i * 200),
        )
        add_forces.operation = 'ADD'
        _link(links, result, add_forces.inputs[0])
        _link(links, force_sockets[i], add_forces.inputs[1])
        result = add_forces.outputs['Vector']

    return result


def _build_visual_output(nodes, links, geo_socket, position_socket,
                         active_socket, arrived_socket, torpedo_radius_socket,
                         material, x_offset):
    """Build post-sim visual pipeline: Set Position → Delete → Instance → Material.

    Returns final_geometry_socket.
    """
    x = x_offset

    # --- Set Position ---
    set_pos = _add_node(
        nodes, 'GeometryNodeSetPosition',
        "SetPosition", (x, 0),
    )
    _link(links, geo_socket, set_pos.inputs['Geometry'])
    _link(links, position_socket, set_pos.inputs['Position'])

    # --- Visibility: active AND NOT arrived ---
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

    # Invert for deletion selection (delete where NOT visible)
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

    # --- UV Sphere with material (set material BEFORE instancing) ---
    uv_sphere = _add_node(
        nodes, 'GeometryNodeMeshUVSphere',
        "TorpedoSphere", (x + 400, 300),
    )
    uv_sphere.inputs['Segments'].default_value = 16
    uv_sphere.inputs['Rings'].default_value = 8
    _link(links, torpedo_radius_socket, uv_sphere.inputs['Radius'])

    set_mat = _add_node(
        nodes, 'GeometryNodeSetMaterial',
        "SetMaterial", (x + 600, 300),
    )
    set_mat.inputs['Material'].default_value = material
    _link(links, uv_sphere.outputs['Mesh'], set_mat.inputs['Geometry'])

    # --- Instance on Points (NO Realize Instances) ---
    instance_pts = _add_node(
        nodes, 'GeometryNodeInstanceOnPoints',
        "InstanceOnPoints", (x + 600, 100),
    )
    _link(links, delete.outputs['Geometry'], instance_pts.inputs['Points'])
    _link(links, set_mat.outputs['Geometry'], instance_pts.inputs['Instance'])

    return instance_pts.outputs['Instances']


# ============================================================
# Scene Functions
# ============================================================

def _ensure_clean_node_group(name):
    """Create a fresh node group, removing any existing one with this name."""
    old = bpy.data.node_groups.get(name)
    if old:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng.is_modifier = True
    return ng


def _create_controller_mesh(num_vertices):
    """Create or update the TorpedoController mesh with N vertices.

    Returns the controller object.
    """
    obj = bpy.data.objects.get(CONTROLLER_NAME)
    mesh = bpy.data.meshes.get(CONTROLLER_NAME)

    if mesh is None:
        mesh = bpy.data.meshes.new(CONTROLLER_NAME)

    # Build N-vertex mesh via bmesh (in-place replace)
    bm = bmesh.new()
    for i in range(num_vertices):
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
    """Create or update the TorpedoEmission material."""
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if not mat:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (0.5, 0.7, 1.0, 1.0)
    emission.inputs['Strength'].default_value = 15.0
    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return mat


# ============================================================
# Main Builder
# ============================================================

def build_torpedo_effect(launchpads, targets, repulsors):
    """Build the TorpedoEffect GeoNodes tree from collections.

    Creates node group with Group Interface, Simulation Zone, all sub-builders.
    Returns the node group.
    """
    n_torpedoes = len(launchpads)
    ng = _ensure_clean_node_group(NODE_GROUP_NAME)
    nodes = ng.nodes
    links = ng.links

    # --- Group Interface ---
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    param_defs = [
        ("Exit Velocity",      'NodeSocketFloat', 50.0),
        ("Attraction",         'NodeSocketFloat', 200.0),
        ("Max Speed",          'NodeSocketFloat', 150.0),
        ("Repulsor Strength",  'NodeSocketFloat', 100.0),
        ("Repulsor Radius",    'NodeSocketFloat', 150.0),
        ("Arrival Distance",   'NodeSocketFloat', 20.0),
        ("Torpedo Radius",     'NodeSocketFloat', 10.0),
        ("Coast Frames",       'NodeSocketFloat', 5.0),
        ("Max Acceleration",   'NodeSocketFloat', 50.0),
    ]
    for name, sock_type, default in param_defs:
        sock = ng.interface.new_socket(name, in_out='INPUT', socket_type=sock_type)
        sock.default_value = default

    # --- Group I/O nodes ---
    group_in = _add_node(nodes, 'NodeGroupInput', "GroupInput", (-1400, 0))
    group_out = _add_node(nodes, 'NodeGroupOutput', "GroupOutput", (6000, 0))

    # --- Simulation Zone ---
    sim_in = _add_node(nodes, 'GeometryNodeSimulationInput', "SimInput", (-800, 0))
    sim_out = _add_node(nodes, 'GeometryNodeSimulationOutput', "SimOutput", (4000, 0))
    sim_in.pair_with_output(sim_out)

    # State items (on sim_out, then appear on sim_in)
    sim_out.state_items.new('VECTOR', "Position")
    sim_out.state_items.new('VECTOR', "Velocity")
    sim_out.state_items.new('FLOAT', "Active")
    sim_out.state_items.new('FLOAT', "Arrived")
    sim_out.state_items.new('FLOAT', "Age")

    # Pass-through state items for parameters
    param_state_names = [
        "ExitVelParam", "AttrParam", "MaxSpeedParam",
        "RepStrParam", "RepRadParam", "ArrDistParam",
        "CoastParam", "MaxAccelParam",
    ]
    for name in param_state_names:
        sim_out.state_items.new('FLOAT', name)

    # Wire Group Input → Sim Zone
    _link(links, group_in.outputs['Geometry'], sim_in.inputs['Geometry'])

    gi_to_state = [
        ("Exit Velocity",     "ExitVelParam"),
        ("Attraction",        "AttrParam"),
        ("Max Speed",         "MaxSpeedParam"),
        ("Repulsor Strength", "RepStrParam"),
        ("Repulsor Radius",   "RepRadParam"),
        ("Arrival Distance",  "ArrDistParam"),
        ("Coast Frames",      "CoastParam"),
        ("Max Acceleration",  "MaxAccelParam"),
    ]
    for gi_name, state_name in gi_to_state:
        _link(links, group_in.outputs[gi_name], sim_in.inputs[state_name])

    # Pass-through: sim_in → sim_out (persist each frame)
    _link(links, sim_in.outputs['Geometry'], sim_out.inputs['Geometry'])
    for state_name in param_state_names:
        _link(links, sim_in.outputs[state_name], sim_out.inputs[state_name])

    # --- Object Info nodes inside Sim Zone ---
    lp_infos = _create_object_info_nodes(
        nodes, launchpads, "LP", -600, -400, 200,
    )
    tgt_infos = _create_object_info_nodes(
        nodes, targets, "TGT", -600, -400 - len(launchpads) * 200 - 200, 200,
    )
    rep_infos = _create_object_info_nodes(
        nodes, repulsors, "REP", -600,
        -400 - (len(launchpads) + len(targets)) * 200 - 400, 200,
    )

    # Collect per-torpedo sockets for mux chains
    lp_scale_sockets = [info.outputs['Scale'] for info in lp_infos]
    lp_rotation_sockets = [info.outputs['Rotation'] for info in lp_infos]
    lp_position_sockets = [info.outputs['Location'] for info in lp_infos]
    tgt_pos_sockets = [info.outputs['Location'] for info in tgt_infos]

    # --- Build per-torpedo target position mux ---
    target_pos_socket = _build_cascading_mux(
        nodes, links, tgt_pos_sockets, 'VECTOR',
        "TgtPos", 0, y_offset=-1400,
    )

    # --- Sub-builder: Launch ---
    active_socket, launch_mask_socket, initial_vel_socket, spawn_pos_socket = (
        _build_launch(
            nodes, links,
            lp_scale_sockets=lp_scale_sockets,
            lp_rotation_sockets=lp_rotation_sockets,
            lp_position_sockets=lp_position_sockets,
            exit_vel_socket=sim_in.outputs['ExitVelParam'],
            prev_active_socket=sim_in.outputs['Active'],
            prev_velocity_socket=sim_in.outputs['Velocity'],
            prev_position_socket=sim_in.outputs['Position'],
            x_offset=600,
        )
    )

    # --- Age counter: frames since activation ---
    new_age = _add_math_node(
        nodes, 'ADD', "AgeIncrement", (1800, -200),
    )
    _link(links, sim_in.outputs['Age'], new_age.inputs[0])
    _link(links, active_socket, new_age.inputs[1])

    # Coast gate: tracking starts after coast frames elapsed
    coast_check = _add_math_node(
        nodes, 'GREATER_THAN', "CoastCheck", (2000, -200),
    )
    _link(links, new_age.outputs[0], coast_check.inputs[0])
    _link(links, sim_in.outputs['CoastParam'], coast_check.inputs[1])

    # --- Sub-builder: Repulsors ---
    # Need dist_to_target for pass-gate, but velocity_integration computes it.
    # Build repulsors with a temporary dist_to_target computation.
    # Actually, repulsor forces need position and target_pos, and dist_to_target.
    # We compute dist_to_target here for repulsor gating.
    temp_to_target = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "TempToTarget", (600, -1800),
    )
    temp_to_target.operation = 'SUBTRACT'
    _link(links, target_pos_socket, temp_to_target.inputs[0])
    _link(links, spawn_pos_socket, temp_to_target.inputs[1])

    temp_dist = _add_node(
        nodes, 'ShaderNodeVectorMath',
        "TempDist", (800, -1800),
    )
    temp_dist.operation = 'LENGTH'
    _link(links, temp_to_target.outputs['Vector'], temp_dist.inputs[0])

    repulsor_force_socket = _build_repulsor_forces(
        nodes, links,
        position_socket=spawn_pos_socket,
        target_pos_socket=target_pos_socket,
        dist_to_target_socket=temp_dist.outputs['Value'],
        repulsor_info_nodes=rep_infos,
        rep_strength_socket=sim_in.outputs['RepStrParam'],
        rep_radius_socket=sim_in.outputs['RepRadParam'],
        x_offset=1000,
    )

    # --- Sub-builder: Velocity Integration ---
    vel_socket, pos_socket, dist_socket = _build_velocity_integration(
        nodes, links,
        velocity_socket=initial_vel_socket,
        position_socket=spawn_pos_socket,
        target_pos_socket=target_pos_socket,
        attraction_socket=sim_in.outputs['AttrParam'],
        repulsor_force_socket=repulsor_force_socket,
        active_socket=active_socket,
        arrived_socket=sim_in.outputs['Arrived'],
        max_speed_socket=sim_in.outputs['MaxSpeedParam'],
        max_accel_socket=sim_in.outputs['MaxAccelParam'],
        delta_time_socket=sim_in.outputs['Delta Time'],
        coast_gate_socket=coast_check.outputs[0],
        x_offset=2000,
    )

    # --- Sub-builder: Arrival Detection ---
    arrived_socket, final_pos_socket, final_vel_socket = _build_arrival_detection(
        nodes, links,
        position_socket=pos_socket,
        target_pos_socket=target_pos_socket,
        velocity_socket=vel_socket,
        dist_to_target_socket=dist_socket,
        arrival_dist_socket=sim_in.outputs['ArrDistParam'],
        prev_arrived_socket=sim_in.outputs['Arrived'],
        x_offset=4600,
    )

    # --- Wire to Sim Zone output ---
    _link(links, final_pos_socket, sim_out.inputs['Position'])
    _link(links, final_vel_socket, sim_out.inputs['Velocity'])
    _link(links, active_socket, sim_out.inputs['Active'])
    _link(links, arrived_socket, sim_out.inputs['Arrived'])
    _link(links, new_age.outputs[0], sim_out.inputs['Age'])

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
        x_offset=4400,
    )

    _link(links, final_geo, group_out.inputs['Geometry'])

    print(f"  Built TorpedoEffect with {n_torpedoes} torpedoes, "
          f"{len(repulsors)} repulsors.")
    return ng


# ============================================================
# Test Scene Setup
# ============================================================

def setup_test_scene(num_launchpads=4):
    """Create a test scene with launchpads, targets, repulsors, and keyframes."""
    # Ensure collections exist
    for col_name in (LAUNCHPAD_COLLECTION, TARGET_COLLECTION, REPULSOR_COLLECTION):
        if col_name not in bpy.data.collections:
            col = bpy.data.collections.new(col_name)
            bpy.context.scene.collection.children.link(col)

    lp_col = bpy.data.collections[LAUNCHPAD_COLLECTION]
    tgt_col = bpy.data.collections[TARGET_COLLECTION]
    rep_col = bpy.data.collections[REPULSOR_COLLECTION]

    # Clear existing objects in collections
    for col in (lp_col, tgt_col, rep_col):
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Create launchpads (arrow empties, left side)
    for i in range(num_launchpads):
        empty = bpy.data.objects.new(f"LP.{i+1:03d}", None)
        empty.empty_display_type = 'SINGLE_ARROW'
        empty.empty_display_size = 20.0
        empty.location = (-300, -150 + i * 100, 0)
        # Point toward +X (rotate arrow's +Z toward +X via Y-axis rotation)
        empty.rotation_euler = (0, radians(90), 0)
        empty.scale = (0, 0, 0)  # Start inactive
        lp_col.objects.link(empty)

        # Keyframe scale: activate at staggered frames
        activate_frame = 10 + i * 8
        empty.keyframe_insert(data_path="scale", frame=1)
        empty.scale = (1, 1, 1)
        empty.keyframe_insert(data_path="scale", frame=activate_frame)

        # Set constant interpolation
        if empty.animation_data and empty.animation_data.action:
            action = empty.animation_data.action
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for fcurve in bag.fcurves:
                            for kp in fcurve.keyframe_points:
                                kp.interpolation = 'CONSTANT'

    # Create targets (empties, right side)
    for i in range(num_launchpads):
        empty = bpy.data.objects.new(f"TGT.{i+1:03d}", None)
        empty.empty_display_type = 'SPHERE'
        empty.empty_display_size = 15.0
        empty.location = (500, -150 + i * 100, 0)
        tgt_col.objects.link(empty)

    # Create repulsors (cubes, middle area)
    for i in range(2):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=30.0)
        mesh = bpy.data.meshes.new(f"REP.{i+1:03d}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"REP.{i+1:03d}", mesh)
        obj.location = (100, -50 + i * 100, 0)
        obj.display_type = 'WIRE'
        rep_col.objects.link(obj)

    print(f"  Test scene: {num_launchpads} launchpads, {num_launchpads} targets, 2 repulsors.")


# ============================================================
# Entry Point
# ============================================================

def main():
    """Validate collections, build node tree, apply modifier."""
    launchpads, targets, repulsors = _validate_collections()
    n = len(launchpads)

    ctrl = _create_controller_mesh(n)
    ng = build_torpedo_effect(launchpads, targets, repulsors)

    # Apply modifier
    for mod in list(ctrl.modifiers):
        ctrl.modifiers.remove(mod)
    mod = ctrl.modifiers.new(NODE_GROUP_NAME, 'NODES')
    mod.node_group = ng

    print(f"\nTorpedoEffect applied to {CONTROLLER_NAME} with {n} torpedoes.")
    print(f"Collections: {LAUNCHPAD_COLLECTION}, {TARGET_COLLECTION}, {REPULSOR_COLLECTION}")
    print("Adjust physics params in the modifier properties.")


if __name__ == "__main__":
    # If collections don't exist yet, set up test scene first
    if LAUNCHPAD_COLLECTION not in bpy.data.collections:
        print("No collections found — creating test scene...")
        setup_test_scene(num_launchpads=4)

    main()
