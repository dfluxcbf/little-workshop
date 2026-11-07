"""A UV sphere as a binary glTF, the model a picture's PBR maps are shown on.

Built once in memory, no file in the repository: positions, normals, tangents and texture coordinates, one
material with its metallic and roughness factors at 1 so the maps alone decide them. The texture wraps twice
around and once from pole to pole, so a square seamless texture keeps its proportions on the sphere.
"""

from __future__ import annotations

import functools
import json
import math
import struct

ROWS, COLUMNS = 64, 128
U_REPEAT = 2  # the equator is twice as long as a meridian


@functools.cache
def glb() -> bytes:
    positions, normals, tangents, uvs, indices = [], [], [], [], []
    for row in range(ROWS + 1):
        v = row / ROWS
        theta = v * math.pi
        for column in range(COLUMNS + 1):
            u = column / COLUMNS
            phi = u * 2 * math.pi
            x, y, z = -math.cos(phi) * math.sin(theta), math.cos(theta), math.sin(phi) * math.sin(theta)
            positions += [x, y, z]
            normals += [x, y, z]
            tangents += [math.sin(phi), 0.0, math.cos(phi), 1.0]
            uvs += [u * U_REPEAT, v]
    for row in range(ROWS):
        for column in range(COLUMNS):
            a = row * (COLUMNS + 1) + column
            b = a + COLUMNS + 1
            indices += [a, b, a + 1, b, b + 1, a + 1]
    count = (ROWS + 1) * (COLUMNS + 1)
    chunks = [struct.pack(f"<{len(positions)}f", *positions), struct.pack(f"<{len(normals)}f", *normals),
              struct.pack(f"<{len(tangents)}f", *tangents), struct.pack(f"<{len(uvs)}f", *uvs),
              struct.pack(f"<{len(indices)}I", *indices)]
    views, offset = [], 0
    for chunk, target in zip(chunks, (34962, 34962, 34962, 34962, 34963)):
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(chunk), "target": target})
        offset += len(chunk)
    binary = b"".join(chunks)
    gltf = {
        "asset": {"version": "2.0", "generator": "little-workshop"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "material-sphere"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TANGENT": 2, "TEXCOORD_0": 3},
                                    "indices": 4, "material": 0}]}],
        "materials": [{"name": "material", "pbrMetallicRoughness": {"metallicFactor": 1.0, "roughnessFactor": 1.0}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": count, "type": "VEC3", "min": [-1, -1, -1], "max": [1, 1, 1]},
            {"bufferView": 1, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": count, "type": "VEC4"},
            {"bufferView": 3, "componentType": 5126, "count": count, "type": "VEC2"},
            {"bufferView": 4, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body
