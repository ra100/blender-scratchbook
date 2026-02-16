"""
Torpedo Effect — Scene Setup Script
=====================================
Creates the scene objects and GeoNodes node tree for the torpedo animation.
Run once to set up, then the animation is fully driven by GeoNodes.

No Python handlers are used during simulation — everything runs inside
Geometry Nodes with a Simulation Zone.

Usage:
    blender torpedo_001.blend --python torpedo_physics_handler.py
"""

import bpy
import bmesh
from mathutils import Vector


# ============================================================
# Configuration
# ============================================================

TORPEDO_CONFIG = {
    "Torpedo1": {"target": "Target1", "launch_frame": 10},
    "Torpedo2": {"target": "Target2", "launch_frame": 15},
}

PHYSICS = {
    "attraction": 400.0,
    "max_speed": 200.0,
    "initial_speed": 50.0,
    "repulsor_strength": 300.0,       # effective strength at current scale
    "repulsor_strength_base": 30.1,   # multiplied by object scale.x
    "repulsor_radius": 250.0,
    "arrival_distance": 20.0,
    "torpedo_radius": 10.0,
}


# ============================================================
# Material
# ============================================================

def create_emission_material():
    mat = bpy.data.materials.get("TorpedoEmission")
    if not mat:
        mat = bpy.data.materials.new("TorpedoEmission")
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
# Node Tree Builder
# ============================================================

def build_torpedo_effect():
    """Build the TorpedoEffect GeoNodes tree.

    Architecture:
      Controller (2-vertex mesh, one per torpedo)
        └── TorpedoEffect GeoNodes Modifier
              ├── Object Info nodes for Target1, Target2, Repulsor1
              │   (reads actual scene object positions — move them to change trajectories)
              ├── Scene Time for activation frames
              ├── Simulation Zone
              │     ├── State: Position, Velocity, Active, Arrived
              │     ├── Target attraction, repulsor avoidance, speed clamping
              │     └── Active/Arrived masking
              ├── Set Position (apply sim results to geometry)
              ├── Delete non-visible torpedoes
              ├── Instance UV Sphere on remaining points
              └── Set TorpedoEmission material
    """
    ng_name = "TorpedoEffect"
    old = bpy.data.node_groups.get(ng_name)
    if old:
        bpy.data.node_groups.remove(old)

    ng = bpy.data.node_groups.new(ng_name, 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    # --- Group Input parameter sockets ---
    params = [
        ("Attraction",            'NodeSocketFloat', PHYSICS["attraction"]),
        ("Max Speed",             'NodeSocketFloat', PHYSICS["max_speed"]),
        ("Initial Speed",         'NodeSocketFloat', PHYSICS["initial_speed"]),
        ("Repulsor Strength Base",'NodeSocketFloat', PHYSICS["repulsor_strength_base"]),
        ("Repulsor Radius",       'NodeSocketFloat', PHYSICS["repulsor_radius"]),
        ("Arrival Distance",      'NodeSocketFloat', PHYSICS["arrival_distance"]),
        ("Torpedo Radius",        'NodeSocketFloat', PHYSICS["torpedo_radius"]),
    ]
    for name, sock_type, default in params:
        sock = ng.interface.new_socket(name, in_out='INPUT', socket_type=sock_type)
        sock.default_value = default

    # --- Group I/O ---
    group_in = ng.nodes.new('NodeGroupInput')
    group_in.location = (-1200, 0)
    group_out = ng.nodes.new('NodeGroupOutput')
    group_out.location = (2200, 0)

    # --- Simulation Zone ---
    sim_in = ng.nodes.new('GeometryNodeSimulationInput')
    sim_in.location = (-600, 0)
    sim_out = ng.nodes.new('GeometryNodeSimulationOutput')
    sim_out.location = (800, 0)
    sim_in.pair_with_output(sim_out)

    sim_out.state_items.new('VECTOR', "Position")
    sim_out.state_items.new('VECTOR', "Velocity")
    sim_out.state_items.new('FLOAT', "Active")
    sim_out.state_items.new('FLOAT', "Arrived")

    # Pass-through state items for parameters (Group Input → sim zone interior)
    param_states = [
        "AttractionParam",
        "MaxSpeedParam",
        "InitialSpeedParam",
        "RepStrengthBaseParam",
        "RepRadiusParam",
        "ArrivalDistParam",
    ]
    for name in param_states:
        sim_out.state_items.new('FLOAT', name)

    ng.links.new(group_in.outputs['Geometry'], sim_in.inputs['Geometry'])

    # Wire Group Inputs → sim_in state inputs (external entry into sim zone)
    gi_to_state = [
        ("Attraction",             "AttractionParam"),
        ("Max Speed",              "MaxSpeedParam"),
        ("Initial Speed",          "InitialSpeedParam"),
        ("Repulsor Strength Base", "RepStrengthBaseParam"),
        ("Repulsor Radius",        "RepRadiusParam"),
        ("Arrival Distance",       "ArrivalDistParam"),
    ]
    for gi_name, state_name in gi_to_state:
        ng.links.new(group_in.outputs[gi_name], sim_in.inputs[state_name])

    # --- Object Info nodes (read actual scene positions) ---
    target1_info = ng.nodes.new('GeometryNodeObjectInfo')
    target1_info.name = "Target1Info"
    target1_info.location = (-400, -400)
    target1_info.inputs['Object'].default_value = bpy.data.objects["Target1"]
    target1_info.transform_space = 'ORIGINAL'

    target2_info = ng.nodes.new('GeometryNodeObjectInfo')
    target2_info.name = "Target2Info"
    target2_info.location = (-400, -600)
    target2_info.inputs['Object'].default_value = bpy.data.objects["Target2"]
    target2_info.transform_space = 'ORIGINAL'

    rep_info = ng.nodes.new('GeometryNodeObjectInfo')
    rep_info.name = "Repulsor1Info"
    rep_info.location = (-400, -800)
    rep_info.inputs['Object'].default_value = bpy.data.objects["Repulsor1"]
    rep_info.transform_space = 'ORIGINAL'

    # --- Scene Time + Index for per-torpedo selection ---
    scene_time = ng.nodes.new('GeometryNodeInputSceneTime')
    scene_time.location = (-400, -200)

    index = ng.nodes.new('GeometryNodeInputIndex')
    index.location = (-400, -100)

    is_t1 = ng.nodes.new('FunctionNodeCompare')
    is_t1.name = "IsT1"
    is_t1.location = (-200, -100)
    is_t1.data_type = 'INT'
    is_t1.operation = 'EQUAL'
    is_t1.inputs[2].default_value = 0
    is_t1.inputs[3].default_value = 0
    ng.links.new(index.outputs['Index'], is_t1.inputs[2])

    # Per-torpedo target: Mix(is_T1, Target2, Target1)
    target_mix = ng.nodes.new('ShaderNodeMix')
    target_mix.name = "TargetPosMix"
    target_mix.location = (0, -400)
    target_mix.data_type = 'VECTOR'
    target_mix.clamp_factor = True
    ng.links.new(is_t1.outputs['Result'], target_mix.inputs['Factor'])
    ng.links.new(target2_info.outputs['Location'], target_mix.inputs[4])
    ng.links.new(target1_info.outputs['Location'], target_mix.inputs[5])

    # Per-torpedo launch frame: Mix(is_T1, T2_frame, T1_frame)
    launch_mix = ng.nodes.new('ShaderNodeMix')
    launch_mix.name = "LaunchFrameMix"
    launch_mix.location = (0, -200)
    launch_mix.data_type = 'FLOAT'
    launch_mix.clamp_factor = True
    launch_mix.inputs[2].default_value = float(TORPEDO_CONFIG["Torpedo2"]["launch_frame"])
    launch_mix.inputs[3].default_value = float(TORPEDO_CONFIG["Torpedo1"]["launch_frame"])
    ng.links.new(is_t1.outputs['Result'], launch_mix.inputs['Factor'])

    # Activation: frame > launch_frame - 0.5
    launch_threshold = ng.nodes.new('ShaderNodeMath')
    launch_threshold.name = "LaunchThreshold"
    launch_threshold.location = (200, -300)
    launch_threshold.operation = 'SUBTRACT'
    launch_threshold.inputs[1].default_value = 0.5
    ng.links.new(launch_mix.outputs[0], launch_threshold.inputs[0])

    activation_check = ng.nodes.new('ShaderNodeMath')
    activation_check.name = "ActivationCheck"
    activation_check.location = (200, -200)
    activation_check.operation = 'GREATER_THAN'
    ng.links.new(scene_time.outputs['Frame'], activation_check.inputs[0])
    ng.links.new(launch_threshold.outputs[0], activation_check.inputs[1])

    # --- Active latching ---
    active_latch = ng.nodes.new('ShaderNodeMath')
    active_latch.name = "ActiveLatch"
    active_latch.location = (400, -200)
    active_latch.operation = 'MAXIMUM'
    ng.links.new(sim_in.outputs['Active'], active_latch.inputs[0])
    ng.links.new(activation_check.outputs[0], active_latch.inputs[1])

    # --- Start Position ---
    start_pos = ng.nodes.new('GeometryNodeInputPosition')
    start_pos.name = "StartPosition"
    start_pos.location = (-400, 200)

    # --- Target attraction ---
    to_target = ng.nodes.new('ShaderNodeVectorMath')
    to_target.name = "ToTarget"
    to_target.location = (200, -500)
    to_target.operation = 'SUBTRACT'
    ng.links.new(target_mix.outputs[1], to_target.inputs[0])
    ng.links.new(sim_in.outputs['Position'], to_target.inputs[1])

    dist_to_target = ng.nodes.new('ShaderNodeVectorMath')
    dist_to_target.name = "DistToTarget"
    dist_to_target.location = (400, -500)
    dist_to_target.operation = 'LENGTH'
    ng.links.new(to_target.outputs['Vector'], dist_to_target.inputs[0])

    norm_to_target = ng.nodes.new('ShaderNodeVectorMath')
    norm_to_target.name = "NormToTarget"
    norm_to_target.location = (400, -600)
    norm_to_target.operation = 'NORMALIZE'
    ng.links.new(to_target.outputs['Vector'], norm_to_target.inputs[0])

    # Attraction boost: stronger as torpedo gets closer to target
    # effective_attraction = Attraction * (1 + RefDist / dist_to_target)
    ref_over_dist = ng.nodes.new('ShaderNodeMath')
    ref_over_dist.name = "RefOverDist"
    ref_over_dist.location = (500, -700)
    ref_over_dist.operation = 'DIVIDE'
    ref_over_dist.inputs[0].default_value = 1000.0  # reference distance for close-range boost
    ng.links.new(dist_to_target.outputs['Value'], ref_over_dist.inputs[1])

    one_plus_boost = ng.nodes.new('ShaderNodeMath')
    one_plus_boost.name = "OnePlusBoost"
    one_plus_boost.location = (500, -600)
    one_plus_boost.operation = 'ADD'
    one_plus_boost.inputs[0].default_value = 1.0
    ng.links.new(ref_over_dist.outputs[0], one_plus_boost.inputs[1])

    effective_attr = ng.nodes.new('ShaderNodeMath')
    effective_attr.name = "EffectiveAttraction"
    effective_attr.location = (550, -550)
    effective_attr.operation = 'MULTIPLY'
    ng.links.new(sim_in.outputs['AttractionParam'], effective_attr.inputs[0])
    ng.links.new(one_plus_boost.outputs[0], effective_attr.inputs[1])

    attr_scale = ng.nodes.new('ShaderNodeVectorMath')
    attr_scale.name = "AttractionScale"
    attr_scale.location = (600, -600)
    attr_scale.operation = 'SCALE'
    ng.links.new(norm_to_target.outputs['Vector'], attr_scale.inputs[0])
    ng.links.new(effective_attr.outputs[0], attr_scale.inputs[3])

    # --- Arrival detection ---
    arrival_check = ng.nodes.new('ShaderNodeMath')
    arrival_check.name = "ArrivalCheck"
    arrival_check.location = (600, -400)
    arrival_check.operation = 'LESS_THAN'
    ng.links.new(dist_to_target.outputs['Value'], arrival_check.inputs[0])
    ng.links.new(sim_in.outputs['ArrivalDistParam'], arrival_check.inputs[1])

    arrived_latch = ng.nodes.new('ShaderNodeMath')
    arrived_latch.name = "ArrivedLatch"
    arrived_latch.location = (800, -400)
    arrived_latch.operation = 'MAXIMUM'
    ng.links.new(sim_in.outputs['Arrived'], arrived_latch.inputs[0])
    ng.links.new(arrival_check.outputs[0], arrived_latch.inputs[1])

    # --- Repulsor avoidance ---
    away = ng.nodes.new('ShaderNodeVectorMath')
    away.name = "AwayFromRepulsor"
    away.location = (200, -800)
    away.operation = 'SUBTRACT'
    ng.links.new(sim_in.outputs['Position'], away.inputs[0])
    ng.links.new(rep_info.outputs['Location'], away.inputs[1])

    dist_rep = ng.nodes.new('ShaderNodeVectorMath')
    dist_rep.name = "DistToRepulsor"
    dist_rep.location = (400, -800)
    dist_rep.operation = 'LENGTH'
    ng.links.new(away.outputs['Vector'], dist_rep.inputs[0])

    dist_norm = ng.nodes.new('ShaderNodeMath')
    dist_norm.name = "RepDistNorm"
    dist_norm.location = (400, -900)
    dist_norm.operation = 'DIVIDE'
    ng.links.new(dist_rep.outputs['Value'], dist_norm.inputs[0])
    ng.links.new(sim_in.outputs['RepRadiusParam'], dist_norm.inputs[1])

    falloff_sub = ng.nodes.new('ShaderNodeMath')
    falloff_sub.name = "RepFalloffSub"
    falloff_sub.location = (600, -900)
    falloff_sub.operation = 'SUBTRACT'
    falloff_sub.inputs[0].default_value = 1.0
    ng.links.new(dist_norm.outputs[0], falloff_sub.inputs[1])

    falloff = ng.nodes.new('ShaderNodeMath')
    falloff.name = "RepFalloff"
    falloff.location = (800, -900)
    falloff.operation = 'MAXIMUM'
    falloff.inputs[1].default_value = 0.0
    ng.links.new(falloff_sub.outputs[0], falloff.inputs[0])

    norm_away = ng.nodes.new('ShaderNodeVectorMath')
    norm_away.name = "NormAway"
    norm_away.location = (600, -800)
    norm_away.operation = 'NORMALIZE'
    ng.links.new(away.outputs['Vector'], norm_away.inputs[0])

    # Repulsor strength driven by object scale
    sep_scale = ng.nodes.new('ShaderNodeSeparateXYZ')
    sep_scale.name = "RepSepScale"
    sep_scale.location = (800, -1050)
    ng.links.new(rep_info.outputs['Scale'], sep_scale.inputs['Vector'])

    scale_strength = ng.nodes.new('ShaderNodeMath')
    scale_strength.name = "RepScaleStrength"
    scale_strength.location = (1000, -1000)
    scale_strength.operation = 'MULTIPLY'
    ng.links.new(sep_scale.outputs['X'], scale_strength.inputs[0])
    ng.links.new(sim_in.outputs['RepStrengthBaseParam'], scale_strength.inputs[1])

    strength_falloff = ng.nodes.new('ShaderNodeMath')
    strength_falloff.name = "RepStrengthFalloff"
    strength_falloff.location = (1000, -900)
    strength_falloff.operation = 'MULTIPLY'
    ng.links.new(scale_strength.outputs[0], strength_falloff.inputs[0])
    ng.links.new(falloff.outputs[0], strength_falloff.inputs[1])

    rep_force = ng.nodes.new('ShaderNodeVectorMath')
    rep_force.name = "RepulsorForce"
    rep_force.location = (1000, -800)
    rep_force.operation = 'SCALE'
    ng.links.new(norm_away.outputs['Vector'], rep_force.inputs[0])
    ng.links.new(strength_falloff.outputs[0], rep_force.inputs[3])

    # --- Repulsor gate: only active when torpedo hasn't passed it ---
    # If torpedo is closer to target than repulsor is, torpedo has passed → ignore repulsor
    rep_to_target = ng.nodes.new('ShaderNodeVectorMath')
    rep_to_target.name = "RepToTarget"
    rep_to_target.location = (400, -1100)
    rep_to_target.operation = 'SUBTRACT'
    ng.links.new(target_mix.outputs[1], rep_to_target.inputs[0])
    ng.links.new(rep_info.outputs['Location'], rep_to_target.inputs[1])

    dist_rep_target = ng.nodes.new('ShaderNodeVectorMath')
    dist_rep_target.name = "DistRepToTarget"
    dist_rep_target.location = (600, -1100)
    dist_rep_target.operation = 'LENGTH'
    ng.links.new(rep_to_target.outputs['Vector'], dist_rep_target.inputs[0])

    rep_gate = ng.nodes.new('ShaderNodeMath')
    rep_gate.name = "RepGate"
    rep_gate.location = (800, -1100)
    rep_gate.operation = 'GREATER_THAN'
    ng.links.new(dist_to_target.outputs['Value'], rep_gate.inputs[0])
    ng.links.new(dist_rep_target.outputs['Value'], rep_gate.inputs[1])

    gated_rep = ng.nodes.new('ShaderNodeVectorMath')
    gated_rep.name = "GatedRepForce"
    gated_rep.location = (1100, -900)
    gated_rep.operation = 'SCALE'
    ng.links.new(rep_force.outputs['Vector'], gated_rep.inputs[0])
    ng.links.new(rep_gate.outputs[0], gated_rep.inputs[3])

    # --- Velocity update ---
    total_force = ng.nodes.new('ShaderNodeVectorMath')
    total_force.name = "TotalForce"
    total_force.location = (1200, -600)
    total_force.operation = 'ADD'
    ng.links.new(attr_scale.outputs['Vector'], total_force.inputs[0])
    ng.links.new(gated_rep.outputs['Vector'], total_force.inputs[1])

    force_dt = ng.nodes.new('ShaderNodeVectorMath')
    force_dt.name = "ForceDt"
    force_dt.location = (1200, -500)
    force_dt.operation = 'SCALE'
    ng.links.new(total_force.outputs['Vector'], force_dt.inputs[0])
    ng.links.new(sim_in.outputs['Delta Time'], force_dt.inputs[3])

    new_vel = ng.nodes.new('ShaderNodeVectorMath')
    new_vel.name = "NewVel"
    new_vel.location = (1400, -500)
    new_vel.operation = 'ADD'
    ng.links.new(sim_in.outputs['Velocity'], new_vel.inputs[0])
    ng.links.new(force_dt.outputs['Vector'], new_vel.inputs[1])

    # Speed clamping
    vel_len = ng.nodes.new('ShaderNodeVectorMath')
    vel_len.name = "VelLength"
    vel_len.location = (1400, -600)
    vel_len.operation = 'LENGTH'
    ng.links.new(new_vel.outputs['Vector'], vel_len.inputs[0])

    clamped_len = ng.nodes.new('ShaderNodeMath')
    clamped_len.name = "ClampedLen"
    clamped_len.location = (1600, -600)
    clamped_len.operation = 'MINIMUM'
    ng.links.new(vel_len.outputs['Value'], clamped_len.inputs[0])
    ng.links.new(sim_in.outputs['MaxSpeedParam'], clamped_len.inputs[1])

    scale_factor = ng.nodes.new('ShaderNodeMath')
    scale_factor.name = "ScaleFactor"
    scale_factor.location = (1600, -700)
    scale_factor.operation = 'DIVIDE'
    ng.links.new(clamped_len.outputs[0], scale_factor.inputs[0])
    ng.links.new(vel_len.outputs['Value'], scale_factor.inputs[1])

    cap = ng.nodes.new('ShaderNodeMath')
    cap.name = "Cap"
    cap.location = (1800, -700)
    cap.operation = 'MINIMUM'
    cap.inputs[1].default_value = 1.0
    ng.links.new(scale_factor.outputs[0], cap.inputs[0])

    clamped_vel = ng.nodes.new('ShaderNodeVectorMath')
    clamped_vel.name = "ClampedVel"
    clamped_vel.location = (1800, -500)
    clamped_vel.operation = 'SCALE'
    ng.links.new(new_vel.outputs['Vector'], clamped_vel.inputs[0])
    ng.links.new(cap.outputs[0], clamped_vel.inputs[3])

    # --- Launch impulse ---
    prev_vel_len = ng.nodes.new('ShaderNodeVectorMath')
    prev_vel_len.name = "PrevVelLen"
    prev_vel_len.location = (400, -300)
    prev_vel_len.operation = 'LENGTH'
    ng.links.new(sim_in.outputs['Velocity'], prev_vel_len.inputs[0])

    is_first_frame = ng.nodes.new('ShaderNodeMath')
    is_first_frame.name = "IsFirstFrame"
    is_first_frame.location = (600, -300)
    is_first_frame.operation = 'LESS_THAN'
    is_first_frame.inputs[1].default_value = 0.001
    ng.links.new(prev_vel_len.outputs['Value'], is_first_frame.inputs[0])

    launch_impulse = ng.nodes.new('ShaderNodeVectorMath')
    launch_impulse.name = "LaunchImpulse"
    launch_impulse.location = (600, -200)
    launch_impulse.operation = 'SCALE'
    ng.links.new(norm_to_target.outputs['Vector'], launch_impulse.inputs[0])
    ng.links.new(sim_in.outputs['InitialSpeedParam'], launch_impulse.inputs[3])

    launch_active = ng.nodes.new('ShaderNodeMath')
    launch_active.name = "LaunchActive"
    launch_active.location = (800, -300)
    launch_active.operation = 'MULTIPLY'
    ng.links.new(active_latch.outputs[0], launch_active.inputs[0])
    ng.links.new(is_first_frame.outputs[0], launch_active.inputs[1])

    vel_select = ng.nodes.new('ShaderNodeMix')
    vel_select.name = "VelSelect"
    vel_select.location = (2000, -400)
    vel_select.data_type = 'VECTOR'
    vel_select.clamp_factor = True
    ng.links.new(launch_active.outputs[0], vel_select.inputs['Factor'])
    ng.links.new(clamped_vel.outputs['Vector'], vel_select.inputs[4])
    ng.links.new(launch_impulse.outputs['Vector'], vel_select.inputs[5])

    # --- Active/Arrived masking ---
    one_minus_arrived = ng.nodes.new('ShaderNodeMath')
    one_minus_arrived.name = "OneMinusArrived"
    one_minus_arrived.location = (1000, -200)
    one_minus_arrived.operation = 'SUBTRACT'
    one_minus_arrived.inputs[0].default_value = 1.0
    ng.links.new(arrived_latch.outputs[0], one_minus_arrived.inputs[1])

    active_mask = ng.nodes.new('ShaderNodeMath')
    active_mask.name = "ActiveMask"
    active_mask.location = (1200, -200)
    active_mask.operation = 'MULTIPLY'
    ng.links.new(active_latch.outputs[0], active_mask.inputs[0])
    ng.links.new(one_minus_arrived.outputs[0], active_mask.inputs[1])

    final_vel = ng.nodes.new('ShaderNodeVectorMath')
    final_vel.name = "FinalVel"
    final_vel.location = (2200, -400)
    final_vel.operation = 'SCALE'
    ng.links.new(vel_select.outputs[1], final_vel.inputs[0])
    ng.links.new(active_mask.outputs[0], final_vel.inputs[3])

    # --- Position update ---
    vel_dt = ng.nodes.new('ShaderNodeVectorMath')
    vel_dt.name = "VelDt"
    vel_dt.location = (2400, -400)
    vel_dt.operation = 'SCALE'
    ng.links.new(final_vel.outputs['Vector'], vel_dt.inputs[0])
    ng.links.new(sim_in.outputs['Delta Time'], vel_dt.inputs[3])

    new_pos = ng.nodes.new('ShaderNodeVectorMath')
    new_pos.name = "NewPos"
    new_pos.location = (2600, -400)
    new_pos.operation = 'ADD'
    ng.links.new(sim_in.outputs['Position'], new_pos.inputs[0])
    ng.links.new(vel_dt.outputs['Vector'], new_pos.inputs[1])

    pos_select = ng.nodes.new('ShaderNodeMix')
    pos_select.name = "PosSelect"
    pos_select.location = (2800, -400)
    pos_select.data_type = 'VECTOR'
    pos_select.clamp_factor = True
    ng.links.new(active_latch.outputs[0], pos_select.inputs['Factor'])
    ng.links.new(start_pos.outputs['Position'], pos_select.inputs[4])
    ng.links.new(new_pos.outputs['Vector'], pos_select.inputs[5])

    # --- Wire to sim zone output ---
    ng.links.new(sim_in.outputs['Geometry'], sim_out.inputs['Geometry'])
    ng.links.new(pos_select.outputs[1], sim_out.inputs['Position'])
    ng.links.new(final_vel.outputs['Vector'], sim_out.inputs['Velocity'])
    ng.links.new(active_latch.outputs[0], sim_out.inputs['Active'])
    ng.links.new(arrived_latch.outputs[0], sim_out.inputs['Arrived'])

    # Pass-through state items: sim_in → sim_out (so values persist each frame)
    for state_name in param_states:
        ng.links.new(sim_in.outputs[state_name], sim_out.inputs[state_name])

    # === POST-SIM ZONE ===

    # Set Position
    set_pos = ng.nodes.new('GeometryNodeSetPosition')
    set_pos.name = "SetPosition"
    set_pos.location = (1000, 0)
    ng.links.new(sim_out.outputs['Geometry'], set_pos.inputs['Geometry'])
    ng.links.new(sim_out.outputs['Position'], set_pos.inputs['Position'])

    # Visibility filter
    vis_inv = ng.nodes.new('ShaderNodeMath')
    vis_inv.name = "OneMinusArrivedPost"
    vis_inv.location = (1000, -100)
    vis_inv.operation = 'SUBTRACT'
    vis_inv.inputs[0].default_value = 1.0
    ng.links.new(sim_out.outputs['Arrived'], vis_inv.inputs[1])

    vis_mask = ng.nodes.new('ShaderNodeMath')
    vis_mask.name = "VisibilityMask"
    vis_mask.location = (1200, -100)
    vis_mask.operation = 'MULTIPLY'
    ng.links.new(sim_out.outputs['Active'], vis_mask.inputs[0])
    ng.links.new(vis_inv.outputs[0], vis_mask.inputs[1])

    vis_invert = ng.nodes.new('ShaderNodeMath')
    vis_invert.name = "VisInvert"
    vis_invert.location = (1200, 0)
    vis_invert.operation = 'SUBTRACT'
    vis_invert.inputs[0].default_value = 1.0
    ng.links.new(vis_mask.outputs[0], vis_invert.inputs[1])

    vis_bool = ng.nodes.new('ShaderNodeMath')
    vis_bool.name = "VisBool"
    vis_bool.location = (1400, 0)
    vis_bool.operation = 'GREATER_THAN'
    vis_bool.inputs[1].default_value = 0.5
    ng.links.new(vis_invert.outputs[0], vis_bool.inputs[0])

    delete = ng.nodes.new('GeometryNodeDeleteGeometry')
    delete.name = "DeleteInvisible"
    delete.location = (1400, 100)
    delete.domain = 'POINT'
    ng.links.new(set_pos.outputs['Geometry'], delete.inputs['Geometry'])
    ng.links.new(vis_bool.outputs[0], delete.inputs['Selection'])

    # Instance torpedo spheres
    uv_sphere = ng.nodes.new('GeometryNodeMeshUVSphere')
    uv_sphere.name = "TorpedoSphere"
    uv_sphere.location = (1400, 300)
    uv_sphere.inputs['Segments'].default_value = 16
    uv_sphere.inputs['Rings'].default_value = 8
    ng.links.new(group_in.outputs['Torpedo Radius'], uv_sphere.inputs['Radius'])

    instance_pts = ng.nodes.new('GeometryNodeInstanceOnPoints')
    instance_pts.name = "InstanceOnPoints"
    instance_pts.location = (1600, 100)
    ng.links.new(delete.outputs['Geometry'], instance_pts.inputs['Points'])
    ng.links.new(uv_sphere.outputs['Mesh'], instance_pts.inputs['Instance'])

    realize = ng.nodes.new('GeometryNodeRealizeInstances')
    realize.name = "Realize"
    realize.location = (1800, 100)
    ng.links.new(instance_pts.outputs['Instances'], realize.inputs['Geometry'])

    mat = bpy.data.materials.get("TorpedoEmission")
    set_mat = ng.nodes.new('GeometryNodeSetMaterial')
    set_mat.name = "SetMaterial"
    set_mat.location = (2000, 100)
    set_mat.inputs['Material'].default_value = mat
    ng.links.new(realize.outputs['Geometry'], set_mat.inputs['Geometry'])

    ng.links.new(set_mat.outputs['Geometry'], group_out.inputs['Geometry'])

    return ng


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    create_emission_material()
    ng = build_torpedo_effect()

    ctrl = bpy.data.objects["TorpedoController"]
    for mod in list(ctrl.modifiers):
        ctrl.modifiers.remove(mod)
    mod = ctrl.modifiers.new("TorpedoEffect", 'NODES')
    mod.node_group = ng

    # Set modifier override values for Group Input parameters
    # (Socket identifiers use Socket_N format based on interface order)
    for key in mod.keys():
        if not key.startswith("_"):
            print(f"  modifier key: {key} = {mod[key]}")

    print("\nTorpedoEffect applied to TorpedoController.")
    print("Move Target1, Target2, Repulsor1 to change trajectories.")
    print("Adjust Attraction, Max Speed, etc. in the modifier properties.")
