"""
Shield Ripple Effect via Geometry Nodes Simulation Zone.

Generates a complete Blender scene with an animated sci-fi shield ripple effect.
Impact markers activate energy waves that propagate across the shield surface
using per-vertex diffusion (Blur Attribute), creating organic displacement and
emission glow that fades over time.

Requires Blender 4.0+ (Simulation Zones, interface.new_socket API).
Run in Blender's Text Editor or via: blender --python shield_ripple_effect.py
"""

import bpy
import bmesh
from mathutils import Vector

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENERGY_ATTR_NAME = "shield_energy"
IMPACT_COLLECTION_NAME = "Impacts"
MATERIAL_NAME = "ShieldMaterial"
NODE_GROUP_NAME = "ShieldRippleEffect"
MIN_BLENDER_VERSION = (4, 0, 0)

# Hardcoded parameters (edit here if needed)
INJECTION_STRENGTH = 1.0
DISPLACEMENT_STRENGTH = 0.05
NOISE_SCALE = 5.0
EMISSION_STRENGTH = 5.0
VOXEL_SIZE = 0.035

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_node(nodes, type_str, label, location):
    """Create a node, set its label and location."""
    node = nodes.new(type_str)
    node.label = label
    node.location = location
    return node


def _add_math_node(nodes, operation, label, location):
    """Create a ShaderNodeMath / FunctionNodeMath with a preset operation."""
    # Geometry Nodes uses ShaderNodeMath for math operations
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.label = label
    node.location = location
    return node


def _link(links, from_socket, to_socket):
    """Create a node link (thin wrapper for readability)."""
    links.new(from_socket, to_socket)


# ---------------------------------------------------------------------------
# Phase 1: Scene Setup
# ---------------------------------------------------------------------------


def clear_scene():
    """Remove all objects, meshes, materials, node groups, and extra collections."""
    # Remove all objects
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # Remove orphan data
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    for ng in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(ng)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    for cam in list(bpy.data.cameras):
        bpy.data.cameras.remove(cam)
    for light in list(bpy.data.lights):
        bpy.data.lights.remove(light)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)

    print("  Scene cleared.")


def create_test_shield():
    """Create a test shield from 2 merged UV spheres with voxel remesh.

    Returns the shield object (~15k-20k verts at voxel size 0.035).
    """
    # Sphere A at origin
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=32, ring_count=32, radius=1.0, location=(0, 0, 0)
    )
    sphere_a = bpy.context.active_object
    sphere_a.name = "ShieldSphereA"

    # Sphere B offset on X axis (overlapping)
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=32, ring_count=32, radius=1.0, location=(1.2, 0, 0)
    )
    sphere_b = bpy.context.active_object
    sphere_b.name = "ShieldSphereB"

    # Boolean Union
    bool_mod = sphere_a.modifiers.new(name="Boolean", type='BOOLEAN')
    bool_mod.operation = 'UNION'
    bool_mod.object = sphere_b
    bpy.context.view_layer.objects.active = sphere_a
    bpy.ops.object.modifier_apply(modifier=bool_mod.name)

    # Remove sphere B (now merged into A)
    bpy.data.objects.remove(sphere_b, do_unlink=True)

    # Voxel Remesh for uniform vertex density
    remesh_mod = sphere_a.modifiers.new(name="Remesh", type='REMESH')
    remesh_mod.mode = 'VOXEL'
    remesh_mod.voxel_size = VOXEL_SIZE
    bpy.ops.object.modifier_apply(modifier=remesh_mod.name)

    # Validate mesh
    vert_count = len(sphere_a.data.vertices)
    if vert_count == 0:
        raise RuntimeError("Boolean + Remesh produced empty mesh. Check sphere overlap.")
    print(f"  Shield created: {vert_count} vertices (voxel size {VOXEL_SIZE})")

    # Smooth shading
    sphere_a.data.shade_smooth()

    sphere_a.name = "Shield"
    return sphere_a


def create_impact_collection(count=3):
    """Create single-vertex mesh impact markers in an 'Impacts' collection.

    Returns (list_of_impact_objects, collection).
    """
    # Create collection
    impact_col = bpy.data.collections.new(IMPACT_COLLECTION_NAME)
    bpy.context.scene.collection.children.link(impact_col)

    # Marker positions near the shield surface
    positions = [
        Vector((0, 0, 1.0)),       # Top of Sphere A
        Vector((1.8, 0, 0)),       # Side of Sphere B
        Vector((0.6, 0.6, 0)),     # Junction between spheres
    ]

    impacts = []
    for i in range(count):
        name = f"Impact.{i + 1:03d}"
        mesh = bpy.data.meshes.new(name)

        # Create single-vertex mesh via bmesh
        bm = bmesh.new()
        bm.verts.new((0, 0, 0))
        bm.to_mesh(mesh)
        bm.free()

        obj = bpy.data.objects.new(name, mesh)
        impact_col.objects.link(obj)

        # Position near shield surface
        pos = positions[i] if i < len(positions) else Vector((0, 0, 1.0))
        obj.location = pos

        # Look like empties in viewport but are mesh objects
        obj.display_type = 'PLAIN_AXES'
        obj.hide_render = True

        # Start inactive (scale 0)
        obj.scale = (0, 0, 0)

        impacts.append(obj)

    print(f"  Created {count} impact markers in '{IMPACT_COLLECTION_NAME}' collection.")
    return impacts, impact_col


# ---------------------------------------------------------------------------
# Phase 2: Geometry Nodes Modifier — Sub-builders
# ---------------------------------------------------------------------------


def _build_injection_pipeline(nodes, links, sim_input_geo_socket, impact_col_socket,
                              inj_radius_socket, x_offset):
    """Build the injection pipeline: Collection Info → filter active → Proximity → falloff.

    Returns the new_energy output socket.
    """
    x = x_offset

    # --- Collection Info ---
    col_info = _add_node(nodes, "GeometryNodeCollectionInfo", "Collection Info", (x, 200))
    col_info.transform_space = 'RELATIVE'
    _link(links, impact_col_socket, col_info.inputs["Collection"])

    # --- Instance Scale → Length → Compare → is_active ---
    inst_scale = _add_node(nodes, "GeometryNodeInputInstanceScale", "Instance Scale", (x + 200, 200))

    vec_length = _add_node(nodes, "ShaderNodeVectorMath", "Scale Length", (x + 400, 200))
    vec_length.operation = 'LENGTH'
    _link(links, inst_scale.outputs["Scale"], vec_length.inputs[0])

    compare = _add_node(nodes, "FunctionNodeCompare", "Is Active?", (x + 600, 200))
    compare.data_type = 'FLOAT'
    compare.operation = 'GREATER_THAN'
    compare.inputs["B"].default_value = 0.01
    _link(links, vec_length.outputs["Value"], compare.inputs["A"])

    # --- Invert selection for Delete Geometry ---
    not_active = _add_node(nodes, "FunctionNodeBooleanMath", "NOT Active", (x + 800, 200))
    not_active.operation = 'NOT'
    _link(links, compare.outputs["Result"], not_active.inputs[0])

    # --- Delete inactive markers BEFORE realizing ---
    delete_geo = _add_node(nodes, "GeometryNodeDeleteGeometry", "Delete Inactive", (x + 1000, 300))
    delete_geo.domain = 'INSTANCE'
    _link(links, col_info.outputs["Instances"], delete_geo.inputs["Geometry"])
    _link(links, not_active.outputs["Boolean"], delete_geo.inputs["Selection"])

    # --- Realize Instances (only active markers remain) ---
    realize = _add_node(nodes, "GeometryNodeRealizeInstances", "Realize Active", (x + 1200, 300))
    _link(links, delete_geo.outputs["Geometry"], realize.inputs["Geometry"])

    # --- Empty-target guard: Domain Size + Switch ---
    domain_size = _add_node(nodes, "GeometryNodeAttributeDomainSize", "Domain Size", (x + 1200, 100))
    _link(links, realize.outputs["Geometry"], domain_size.inputs["Geometry"])

    has_points = _add_node(nodes, "FunctionNodeCompare", "Has Points?", (x + 1400, 100))
    has_points.data_type = 'INT'
    has_points.operation = 'GREATER_THAN'
    has_points.inputs["B"].default_value = 0
    _link(links, domain_size.outputs["Point Count"], has_points.inputs["A"])

    # --- Geometry Proximity ---
    proximity = _add_node(nodes, "GeometryNodeProximity", "Proximity to Impacts", (x + 1400, 300))
    proximity.target_element = 'POINTS'
    _link(links, realize.outputs["Geometry"], proximity.inputs["Target"])
    _link(links, sim_input_geo_socket, proximity.inputs["Source Position"])

    # --- Map Range (distance → falloff) ---
    map_range = _add_node(nodes, "ShaderNodeMapRange", "Injection Falloff", (x + 1600, 300))
    map_range.clamp = True
    map_range.inputs["From Min"].default_value = 0.0
    map_range.inputs["To Min"].default_value = 1.0     # Close = full energy
    map_range.inputs["To Max"].default_value = 0.0     # Far = no energy
    _link(links, proximity.outputs["Distance"], map_range.inputs["Value"])
    _link(links, inj_radius_socket, map_range.inputs["From Max"])

    # --- Multiply by injection strength (hardcoded 1.0) ---
    inj_multiply = _add_math_node(nodes, 'MULTIPLY', "Injection × Strength", (x + 1800, 300))
    inj_multiply.inputs[1].default_value = INJECTION_STRENGTH
    _link(links, map_range.outputs["Result"], inj_multiply.inputs[0])

    # --- Switch: if no active points, new_energy = 0 ---
    switch = _add_node(nodes, "GeometryNodeSwitch", "Empty Guard", (x + 2000, 300))
    switch.input_type = 'FLOAT'
    switch.inputs["False"].default_value = 0.0  # No active impacts → 0 energy
    _link(links, has_points.outputs["Result"], switch.inputs["Switch"])
    _link(links, inj_multiply.outputs["Value"], switch.inputs["True"])

    return switch.outputs["Output"]


def _build_accumulation(nodes, links, prev_energy_socket, new_energy_socket, x_offset):
    """Add new energy to previous energy, clamp to 1.0.

    Returns the clamped_energy output socket.
    """
    x = x_offset

    add_energy = _add_math_node(nodes, 'ADD', "Accumulate Energy", (x, 0))
    _link(links, prev_energy_socket, add_energy.inputs[0])
    _link(links, new_energy_socket, add_energy.inputs[1])

    clamp = _add_math_node(nodes, 'MINIMUM', "Clamp to 1.0", (x + 200, 0))
    clamp.inputs[1].default_value = 1.0
    _link(links, add_energy.outputs["Value"], clamp.inputs[0])

    return clamp.outputs["Value"]


def _build_diffusion_decay(nodes, links, energy_socket, geo_socket,
                           wave_speed_socket, decay_rate_socket, x_offset):
    """Blur Attribute for diffusion, then exponential decay.

    Returns the decayed_energy output socket.
    """
    x = x_offset

    # --- Blur Attribute (diffusion) ---
    blur = _add_node(nodes, "GeometryNodeBlurAttribute", "Diffuse Energy", (x, 0))
    blur.data_type = 'FLOAT'
    _link(links, energy_socket, blur.inputs["Value"])
    _link(links, wave_speed_socket, blur.inputs["Iterations"])
    _link(links, geo_socket, blur.inputs["Geometry"])

    # --- Decay: energy *= (1 - decay_rate) ---
    one_minus = _add_math_node(nodes, 'SUBTRACT', "1 - Decay", (x + 200, -150))
    one_minus.inputs[0].default_value = 1.0
    _link(links, decay_rate_socket, one_minus.inputs[1])

    decay_mul = _add_math_node(nodes, 'MULTIPLY', "Apply Decay", (x + 400, 0))
    _link(links, blur.outputs["Value"], decay_mul.inputs[0])
    _link(links, one_minus.outputs["Value"], decay_mul.inputs[1])

    # --- Floor at 0.0 ---
    floor = _add_math_node(nodes, 'MAXIMUM', "Floor at 0", (x + 600, 0))
    floor.inputs[1].default_value = 0.0
    _link(links, decay_mul.outputs["Value"], floor.inputs[0])

    return floor.outputs["Value"]


def _build_post_sim(nodes, links, geo_socket, energy_socket, x_offset):
    """Noise-modulated displacement along normals + Store Named Attribute.

    Returns the final geometry output socket.
    """
    x = x_offset

    # --- Position input for Noise Texture ---
    position = _add_node(nodes, "GeometryNodeInputPosition", "Position", (x, -200))

    # --- Noise Texture ---
    noise = _add_node(nodes, "ShaderNodeTexNoise", "Noise Texture", (x + 200, -200))
    noise.inputs["Scale"].default_value = NOISE_SCALE
    _link(links, position.outputs["Position"], noise.inputs["Vector"])

    # --- energy × disp_strength × noise ---
    mul1 = _add_math_node(nodes, 'MULTIPLY', "Energy × Disp", (x + 200, 0))
    mul1.inputs[1].default_value = DISPLACEMENT_STRENGTH
    _link(links, energy_socket, mul1.inputs[0])

    mul2 = _add_math_node(nodes, 'MULTIPLY', "× Noise", (x + 400, 0))
    _link(links, mul1.outputs["Value"], mul2.inputs[0])
    _link(links, noise.outputs["Fac"], mul2.inputs[1])

    # --- Normal ---
    normal = _add_node(nodes, "GeometryNodeInputNormal", "Normal", (x + 400, -150))

    # --- Vector Math: Scale (normal × offset_magnitude) ---
    vec_scale = _add_node(nodes, "ShaderNodeVectorMath", "Scale Normal", (x + 600, 0))
    vec_scale.operation = 'SCALE'
    _link(links, normal.outputs["Normal"], vec_scale.inputs[0])
    _link(links, mul2.outputs["Value"], vec_scale.inputs[3])  # Scale float is index 3

    # --- Set Position (offset) ---
    set_pos = _add_node(nodes, "GeometryNodeSetPosition", "Displace", (x + 800, 100))
    _link(links, geo_socket, set_pos.inputs["Geometry"])
    _link(links, vec_scale.outputs["Vector"], set_pos.inputs["Offset"])

    # --- Store Named Attribute ---
    store_attr = _add_node(nodes, "GeometryNodeStoreNamedAttribute", "Store Energy", (x + 1000, 100))
    store_attr.data_type = 'FLOAT'
    store_attr.domain = 'POINT'
    store_attr.inputs["Name"].default_value = ENERGY_ATTR_NAME
    _link(links, set_pos.outputs["Geometry"], store_attr.inputs["Geometry"])
    _link(links, energy_socket, store_attr.inputs["Value"])

    return store_attr.outputs["Geometry"]


# ---------------------------------------------------------------------------
# Phase 2: Geometry Nodes Modifier — Main Builder
# ---------------------------------------------------------------------------


def create_geometry_nodes(shield_obj, impact_collection):
    """Build the full ShieldRippleEffect geometry nodes tree and attach to shield.

    Returns the modifier.
    """
    # Create node group
    node_group = bpy.data.node_groups.new(NODE_GROUP_NAME, 'GeometryNodeTree')
    node_group.is_modifier = True
    nodes = node_group.nodes
    links = node_group.links

    # --- Group Interface (Inputs) ---
    # Geometry input (auto-created, but we reference it)
    geo_in = node_group.interface.new_socket(
        name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry'
    )

    impact_col_in = node_group.interface.new_socket(
        name="Impact Collection", in_out='INPUT', socket_type='NodeSocketCollection'
    )

    wave_speed_in = node_group.interface.new_socket(
        name="Wave Speed", in_out='INPUT', socket_type='NodeSocketInt'
    )
    wave_speed_in.default_value = 5
    wave_speed_in.min_value = 1
    wave_speed_in.max_value = 15

    decay_rate_in = node_group.interface.new_socket(
        name="Decay Rate", in_out='INPUT', socket_type='NodeSocketFloat'
    )
    decay_rate_in.default_value = 0.05
    decay_rate_in.min_value = 0.0
    decay_rate_in.max_value = 1.0

    inj_radius_in = node_group.interface.new_socket(
        name="Injection Radius", in_out='INPUT', socket_type='NodeSocketFloat'
    )
    inj_radius_in.default_value = 0.3
    inj_radius_in.min_value = 0.01
    inj_radius_in.max_value = 5.0

    # --- Group Interface (Output) ---
    geo_out = node_group.interface.new_socket(
        name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry'
    )

    # --- Group Input / Output nodes ---
    group_in = _add_node(nodes, "NodeGroupInput", "Group Input", (-800, 0))
    group_out = _add_node(nodes, "NodeGroupOutput", "Group Output", (4000, 0))

    # --- Simulation Zone ---
    sim_output = _add_node(nodes, "GeometryNodeSimulationOutput", "Sim Output", (2200, 0))
    sim_input = _add_node(nodes, "GeometryNodeSimulationInput", "Sim Input", (-400, 0))
    sim_input.pair_with_output(sim_output)

    # Add Energy state item (FLOAT)
    sim_output.state_items.new('FLOAT', 'Energy')

    # Connect Group Input geometry → Sim Input geometry
    _link(links, group_in.outputs["Geometry"], sim_input.inputs["Geometry"])

    # --- STEP 1: INJECTION ---
    new_energy_socket = _build_injection_pipeline(
        nodes, links,
        sim_input_geo_socket=sim_input.outputs["Geometry"],
        impact_col_socket=group_in.outputs["Impact Collection"],
        inj_radius_socket=group_in.outputs["Injection Radius"],
        x_offset=0,
    )

    # --- STEP 2: ACCUMULATION ---
    clamped_energy_socket = _build_accumulation(
        nodes, links,
        prev_energy_socket=sim_input.outputs["Energy"],
        new_energy_socket=new_energy_socket,
        x_offset=400,
    )

    # --- STEP 3 & 4: DIFFUSION + DECAY ---
    decayed_energy_socket = _build_diffusion_decay(
        nodes, links,
        energy_socket=clamped_energy_socket,
        geo_socket=sim_input.outputs["Geometry"],
        wave_speed_socket=group_in.outputs["Wave Speed"],
        decay_rate_socket=group_in.outputs["Decay Rate"],
        x_offset=900,
    )

    # --- Connect to Sim Output ---
    _link(links, sim_input.outputs["Geometry"], sim_output.inputs["Geometry"])
    _link(links, decayed_energy_socket, sim_output.inputs["Energy"])

    # --- STEP 5 & 6: POST-SIM (Displacement + Store Attribute) ---
    final_geo_socket = _build_post_sim(
        nodes, links,
        geo_socket=sim_output.outputs["Geometry"],
        energy_socket=sim_output.outputs["Energy"],
        x_offset=2500,
    )

    # --- Connect to Group Output ---
    _link(links, final_geo_socket, group_out.inputs["Geometry"])

    # --- Attach modifier to shield ---
    modifier = shield_obj.modifiers.new(name=NODE_GROUP_NAME, type='NODES')
    modifier.node_group = node_group

    # Set the Impact Collection input
    # Find the socket identifier for "Impact Collection"
    for item in node_group.interface.items_tree:
        if item.name == "Impact Collection" and item.in_out == 'INPUT':
            modifier[item.identifier] = impact_collection
            break

    print("  Geometry Nodes modifier created and attached.")
    return modifier


# ---------------------------------------------------------------------------
# Phase 3: Shield Shader
# ---------------------------------------------------------------------------


def create_shield_material(shield_obj):
    """Build the shield shader: transparent when inactive, emission glow when hit.

    Returns the material.
    """
    mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    mat.use_backface_culling = False

    # EEVEE Next transparency settings
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    if hasattr(mat, 'use_transparency_overlap'):
        mat.use_transparency_overlap = True
    if hasattr(mat, 'show_transparent_back'):
        mat.show_transparent_back = True

    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links

    # Clear default nodes
    nodes.clear()

    # --- Attribute node (reads shield_energy) ---
    attr = _add_node(nodes, "ShaderNodeAttribute", "Energy Attribute", (-600, 0))
    attr.attribute_name = ENERGY_ATTR_NAME
    attr.attribute_type = 'GEOMETRY'

    # --- Layer Weight (Fresnel hint) ---
    layer_weight = _add_node(nodes, "ShaderNodeLayerWeight", "Layer Weight", (-600, -200))
    layer_weight.inputs["Blend"].default_value = 0.1

    fresnel_mul = _add_node(nodes, "ShaderNodeMath", "Fresnel × 0.05", (-400, -200))
    fresnel_mul.operation = 'MULTIPLY'
    fresnel_mul.inputs[1].default_value = 0.05
    links.new(layer_weight.outputs["Fresnel"], fresnel_mul.inputs[0])

    # --- Max(energy, fresnel_subtle) ---
    combined = _add_node(nodes, "ShaderNodeMath", "Combined Factor", (-200, -100))
    combined.operation = 'MAXIMUM'
    links.new(attr.outputs["Fac"], combined.inputs[0])
    links.new(fresnel_mul.outputs["Value"], combined.inputs[1])

    # --- Color Ramp ---
    color_ramp = _add_node(nodes, "ShaderNodeValToRGB", "Energy Color Ramp", (0, 0))
    cr = color_ramp.color_ramp
    # Remove default elements and recreate
    # Default has 2 elements at 0 and 1
    cr.elements[0].position = 0.0
    cr.elements[0].color = (0, 0, 0, 0)  # Transparent black

    cr.elements[1].position = 0.15
    cr.elements[1].color = (0.1, 0.4, 0.8, 0.5)  # Faint blue

    e2 = cr.elements.new(0.5)
    e2.color = (0.3, 0.7, 1.0, 1.0)  # Bright cyan

    e3 = cr.elements.new(1.0)
    e3.color = (0.8, 0.95, 1.0, 1.0)  # White-hot

    links.new(combined.outputs["Value"], color_ramp.inputs["Fac"])

    # --- Transparent BSDF ---
    transparent = _add_node(nodes, "ShaderNodeBsdfTransparent", "Transparent", (200, -150))

    # --- Emission ---
    emission = _add_node(nodes, "ShaderNodeEmission", "Emission", (200, 50))
    emission.inputs["Strength"].default_value = EMISSION_STRENGTH
    links.new(color_ramp.outputs["Color"], emission.inputs["Color"])

    # --- Mix Shader ---
    mix_shader = _add_node(nodes, "ShaderNodeMixShader", "Mix Shader", (400, 0))
    links.new(combined.outputs["Value"], mix_shader.inputs["Fac"])
    links.new(transparent.outputs["BSDF"], mix_shader.inputs[1])
    links.new(emission.outputs["Emission"], mix_shader.inputs[2])

    # --- Material Output ---
    mat_output = _add_node(nodes, "ShaderNodeOutputMaterial", "Material Output", (600, 0))
    links.new(mix_shader.outputs["Shader"], mat_output.inputs["Surface"])

    # Assign to shield
    shield_obj.data.materials.clear()
    shield_obj.data.materials.append(mat)

    print("  Shield material created and assigned.")
    return mat


def setup_bloom_glow():
    """Configure bloom/glow for EEVEE and Cycles compositor Glare."""
    scene = bpy.context.scene

    # EEVEE bloom
    if hasattr(scene.eevee, 'use_bloom'):
        scene.eevee.use_bloom = True
        scene.eevee.bloom_threshold = 0.8
        scene.eevee.bloom_intensity = 0.5
        scene.eevee.bloom_radius = 6.5

    # Cycles compositor Glare
    scene.use_nodes = True
    comp_tree = scene.node_tree
    comp_nodes = comp_tree.nodes
    comp_links = comp_tree.links

    # Find existing Render Layers and Composite nodes
    render_layers = None
    composite = None
    for node in comp_nodes:
        if node.type == 'R_LAYERS':
            render_layers = node
        elif node.type == 'COMPOSITE':
            composite = node

    if render_layers and composite:
        # Remove direct link between Render Layers and Composite
        for link in list(comp_links):
            if link.from_node == render_layers and link.to_node == composite:
                comp_links.remove(link)

        # Add Glare node
        glare = comp_nodes.new('CompositorNodeGlare')
        glare.glare_type = 'FOG_GLOW'
        glare.quality = 'HIGH'
        glare.threshold = 0.8
        glare.size = 7
        glare.location = (render_layers.location.x + 300, render_layers.location.y)

        # Render Layers → Glare → Composite
        comp_links.new(render_layers.outputs["Image"], glare.inputs["Image"])
        comp_links.new(glare.outputs["Image"], composite.inputs["Image"])

    print("  Bloom/glow configured (EEVEE + Cycles compositor).")


# ---------------------------------------------------------------------------
# Phase 4: Test Animation & Demo Scene
# ---------------------------------------------------------------------------


def setup_demo_scene():
    """Add camera, light, and configure frame range."""
    scene = bpy.context.scene

    # Frame range
    scene.frame_start = 1
    scene.frame_end = 120
    scene.render.fps = 24

    # Camera
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    cam_obj.location = (3.5, -3.5, 2.5)
    cam_obj.rotation_euler = (1.1, 0.0, 0.8)
    scene.camera = cam_obj

    # Area light
    light_data = bpy.data.lights.new("AreaLight", type='AREA')
    light_data.energy = 100
    light_data.size = 5
    light_obj = bpy.data.objects.new("AreaLight", light_data)
    bpy.context.scene.collection.objects.link(light_obj)
    light_obj.location = (2, -2, 4)

    print("  Demo scene configured (camera, light, 120 frames @ 24fps).")


def _set_constant_interpolation(obj):
    """Set CONSTANT interpolation on all scale keyframes of an object."""
    if obj.animation_data and obj.animation_data.action:
        for fcurve in obj.animation_data.action.fcurves:
            if fcurve.data_path == "scale":
                for kp in fcurve.keyframe_points:
                    kp.interpolation = 'CONSTANT'
                fcurve.update()


def setup_test_animation(impact_objects):
    """Keyframe the 3 impact markers across 120 frames.

    Test sequence:
      Frame 10-12: Impact.001 active
      Frame 40-42: Impact.002 active
      Frame 60-62: Impact.001 + Impact.003 active simultaneously
    """
    imp1, imp2, imp3 = impact_objects[0], impact_objects[1], impact_objects[2]

    # --- Frame 1: All markers at rest (scale 0) ---
    for obj in impact_objects:
        obj.scale = (0, 0, 0)
        obj.keyframe_insert(data_path="scale", frame=1)

    # --- Impact 1: frames 10-12 ---
    imp1.scale = (1, 1, 1)
    imp1.keyframe_insert(data_path="scale", frame=10)
    imp1.scale = (0, 0, 0)
    imp1.keyframe_insert(data_path="scale", frame=13)

    # --- Impact 2: frames 40-42 ---
    imp2.scale = (1, 1, 1)
    imp2.keyframe_insert(data_path="scale", frame=40)
    imp2.scale = (0, 0, 0)
    imp2.keyframe_insert(data_path="scale", frame=43)

    # --- Impact 1 + 3 simultaneous: frames 60-62 ---
    imp1.scale = (1, 1, 1)
    imp1.keyframe_insert(data_path="scale", frame=60)
    imp1.scale = (0, 0, 0)
    imp1.keyframe_insert(data_path="scale", frame=63)

    imp3.scale = (1, 1, 1)
    imp3.keyframe_insert(data_path="scale", frame=60)
    imp3.scale = (0, 0, 0)
    imp3.keyframe_insert(data_path="scale", frame=63)

    # Set CONSTANT interpolation on all markers
    for obj in impact_objects:
        _set_constant_interpolation(obj)

    print("  Test animation keyframed (3 impact events across 120 frames).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Orchestrate the full shield ripple effect setup."""
    # Version check
    if bpy.app.version < MIN_BLENDER_VERSION:
        raise RuntimeError(
            f"Shield Ripple Effect requires Blender {'.'.join(str(v) for v in MIN_BLENDER_VERSION)}+. "
            f"Current version: {bpy.app.version_string}"
        )

    print("=" * 60)
    print("Shield Ripple Effect — Setup")
    print("=" * 60)

    print("\n[1/7] Clearing scene...")
    clear_scene()

    print("[2/7] Creating test shield geometry...")
    shield = create_test_shield()

    print("[3/7] Creating impact markers...")
    impacts, impact_col = create_impact_collection(count=3)

    print("[4/7] Building Geometry Nodes modifier...")
    create_geometry_nodes(shield, impact_col)

    print("[5/7] Creating shield material...")
    create_shield_material(shield)

    print("[6/7] Setting up demo scene & bloom...")
    setup_demo_scene()
    setup_bloom_glow()

    print("[7/7] Keyframing test animation...")
    setup_test_animation(impacts)

    # Set frame to 1
    bpy.context.scene.frame_set(1)

    print("\n" + "=" * 60)
    print("Setup complete! Play the animation (Space) to see ripples.")
    print("Adjust Wave Speed, Decay Rate, and Injection Radius on the")
    print("Shield's Geometry Nodes modifier panel.")
    print("=" * 60)


if __name__ == "__main__":
    main()
