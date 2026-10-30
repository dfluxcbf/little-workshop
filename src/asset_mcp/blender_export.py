"""Runs INSIDE Blender (`blender -b <file>.blend --python blender_export.py -- <args.json>`), never imported
by little-workshop. Reads the arguments from the JSON file named after `--`, writes the bundle of a .blend
(see `common.assets`) and a `result.json` beside the arguments:

- `model.glb`: the visible meshes (or one collection) as one glTF binary, modifiers applied, textures embedded;
- `view_<name>.png`: ten Workbench renders, textures shown, from the sides, top, bottom and four orthographic corners;
- `uv_layout.png`: the UV layout of the exported meshes;
- `textures/<image>.png`: every image the exported materials sample;
- `manifest.json`.

It uses only the standard library and `bpy`, so it must stay free of little-workshop imports.
"""

import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

VIEWS = {
    "front": (0, 0), "right": (90, 0), "back": (180, 0), "left": (270, 0),
    "top": (0, 90), "bottom": (0, -90),
    "ortho_top_1": (45, 35), "ortho_top_2": (225, 35),
    "ortho_bottom_1": (45, -35), "ortho_bottom_2": (225, -35),
}
SKIP_COLLECTIONS = {"reference", "references", "scratch", "ignore", "lights", "cameras"}


def log(message):
    print(f"[asset-export] {message}", flush=True)


def export_objects(collection_name):
    if collection_name:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise RuntimeError(f"no collection named {collection_name!r}; there are: {sorted(c.name for c in bpy.data.collections)}")
        candidates = list(collection.all_objects)
    else:
        skipped = {o for c in bpy.data.collections if c.name.lower() in SKIP_COLLECTIONS for o in c.all_objects}
        candidates = [o for o in bpy.context.scene.objects if o not in skipped]
    view_layer = bpy.context.view_layer
    return [o for o in candidates if o.type in {"MESH", "CURVE", "SURFACE", "FONT"} and not o.hide_viewport and not o.hide_get(view_layer=view_layer)
            and o.name in view_layer.objects]


def world_bounds(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    low, high = Vector((1e18,) * 3), Vector((-1e18,) * 3)
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        for corner in evaluated.bound_box:
            point = evaluated.matrix_world @ Vector(corner)
            low = Vector(min(a, b) for a, b in zip(low, point))
            high = Vector(max(a, b) for a, b in zip(high, point))
    return low, high


def select_only(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def export_glb(objects, path):
    select_only(objects)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_apply=True,
                              export_yup=True, export_image_format="AUTO", export_cameras=False, export_lights=False)


def export_textures(objects, folder):
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            if material is None or not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None and node.image.name not in images:
                    images[node.image.name] = node.image
    written = []
    for name, image in images.items():
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).rsplit(".", 1)[0] or "image"
        target = folder / f"{safe}.png"
        copy = image.copy()
        try:
            copy.file_format = "PNG"
            copy.save(filepath=str(target))
            written.append(target.name)
        except Exception as exc:  # an image without pixels (missing file) must not stop the export
            log(f"texture {name!r} skipped: {exc}")
        finally:
            bpy.data.images.remove(copy)
    return written


def export_uv(objects, path, size):
    """Draw the UV layout of the exported meshes by hand: Blender's own `uv.export_layout` needs a GPU, which a
    background session does not have."""
    import numpy as np

    segments = []
    for obj in objects:
        if obj.type != "MESH" or not obj.data.uv_layers:
            continue
        mesh = obj.data
        loop_count = len(mesh.loops)
        if not loop_count:
            continue
        uv = np.empty(loop_count * 2, dtype=np.float32)
        mesh.uv_layers.active.uv.foreach_get("vector", uv)
        uv = uv.reshape(-1, 2)
        starts = np.empty(len(mesh.polygons), dtype=np.int64)
        totals = np.empty(len(mesh.polygons), dtype=np.int64)
        mesh.polygons.foreach_get("loop_start", starts)
        mesh.polygons.foreach_get("loop_total", totals)
        following = np.arange(loop_count) + 1
        following[starts + totals - 1] = starts
        segments.append(np.concatenate([uv, uv[following]], axis=1))
    if not segments:
        return False
    data = np.concatenate(segments)
    canvas = np.zeros((size, size, 4), dtype=np.float32)
    canvas[...] = (0.09, 0.09, 0.11, 1.0)
    edge = np.arange(size)
    for line in (canvas[0, :], canvas[size - 1, :], canvas[:, 0], canvas[:, size - 1]):
        line[...] = (0.4, 0.4, 0.45, 1.0)
    del edge
    scale = size - 1
    for chunk in np.array_split(data, max(1, len(data) // 2000)):
        length = int(np.max(np.hypot(chunk[:, 2] - chunk[:, 0], chunk[:, 3] - chunk[:, 1])) * scale) + 2
        t = np.linspace(0.0, 1.0, min(length, size * 2), dtype=np.float32)[None, :]
        x = ((chunk[:, 0:1] + (chunk[:, 2:3] - chunk[:, 0:1]) * t) * scale).round().astype(np.int64).ravel()
        y = ((chunk[:, 1:2] + (chunk[:, 3:4] - chunk[:, 1:2]) * t) * scale).round().astype(np.int64).ravel()
        keep = (x >= 0) & (x < size) & (y >= 0) & (y < size)
        canvas[y[keep], x[keep]] = (0.55, 0.85, 1.0, 1.0)
    image = bpy.data.images.new("uv_layout", size, size, alpha=True)
    try:
        image.pixels.foreach_set(canvas.ravel())
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    return path.is_file()


def render_views(objects, folder, resolution):
    scene = bpy.context.scene
    low, high = world_bounds(objects)
    center = (low + high) / 2
    radius = max((high - low).length / 2, 0.001)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.show_object_outline = True
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    shown = set(objects)
    for obj in scene.objects:
        obj.hide_render = obj not in shown
    camera_data = bpy.data.cameras.new("asset_view")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = radius * 2.15
    camera_data.clip_start, camera_data.clip_end = radius * 0.05, radius * 20
    camera = bpy.data.objects.new("asset_view", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    written = []
    for name, (azimuth, elevation) in VIEWS.items():
        az, el = math.radians(azimuth), math.radians(elevation)
        direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
        camera.location = center + direction * radius * 4
        camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(folder / f"view_{name}.png")
        bpy.ops.render.render(write_still=True)
        written.append(name)
    return written


def main():
    args_path = Path(sys.argv[sys.argv.index("--") + 1])
    args = json.loads(args_path.read_text(encoding="utf-8"))
    folder = Path(args["bundle"])
    folder.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "errors": [], "objects": 0}
    manifest = {"source": args["source_name"], "source_mtime": args["source_mtime"], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "collection": args.get("collection"), "errors": []}
    try:
        objects = export_objects(args.get("collection"))
        if not objects:
            raise RuntimeError("the file has no visible mesh objects to export")
        result["objects"] = len(objects)
        manifest["objects"] = len(objects)
        steps = [
            ("model", lambda: export_glb(objects, folder / "model.glb")),
            ("textures", lambda: manifest.__setitem__("textures", export_textures(objects, folder / "textures"))),
            ("uv", lambda: manifest.__setitem__("uv", export_uv(objects, folder / "uv_layout.png", args["uv_size"]))),
            ("views", lambda: manifest.__setitem__("views", render_views(objects, folder, args["resolution"]))),
        ]
        for name, step in steps:
            try:
                step()
                log(f"{name}: done")
            except Exception as exc:
                message = f"{name}: {exc}"
                manifest["errors"].append(message)
                log(message + "\n" + traceback.format_exc())
        result["ok"] = not manifest["errors"]
        result["errors"] = manifest["errors"]
    except Exception as exc:
        result["errors"] = [str(exc)]
        manifest["errors"] = [str(exc)]
        log(traceback.format_exc())
    # The manifest is what marks a bundle as present; written last, even when a step failed, so the Dashboard shows what exists.
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    args_path.with_name("result.json").write_text(json.dumps(result), encoding="utf-8")


main()
