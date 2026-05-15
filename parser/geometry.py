"""Geometry parsing for 3D model files.

Supports STL/STEP/OBJ via trimesh, plus native 3MF XML parsing.
"""

import re
import zipfile
import tempfile
import os

import numpy as np
import trimesh


def _parse_3mf_xml_mesh(xml_data: bytes) -> "np.ndarray | None":
    """Parse native 3MF XML mesh (<vertices> + <triangles>) into numpy array.

    Returns (faces, 3, 3) float array or None if parsing fails.
    """
    text = xml_data.decode("utf-8", errors="replace")

    # Extract vertices
    verts = []
    for m in re.finditer(r'<vertex\s+x="([^"]*)"\s+y="([^"]*)"\s+z="([^"]*)"', text):
        verts.append([float(m.group(1)), float(m.group(2)), float(m.group(3))])

    # Extract triangles
    tris = []
    for m in re.finditer(r'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"', text):
        tris.append([int(m.group(1)), int(m.group(2)), int(m.group(3))])

    if not verts or not tris:
        return None

    vertices = np.array(verts, dtype=np.float64)
    faces = np.array(tris, dtype=np.int32)

    # Build (N, 3, 3) face array for trimesh
    result = np.zeros((len(faces), 3, 3), dtype=np.float64)
    for i, f in enumerate(faces):
        result[i] = vertices[f]
    return result


def _extract_geometry_from_3mf(path_3mf: str) -> "np.ndarray | None":
    """Extract 3D geometry from a 3MF file, handling multiple formats.

    Tries in order:
    1. Embedded STL/OBJ inside the ZIP
    2. Native 3MF XML mesh (vertices + triangles)
    3. Returns None if nothing found
    """
    mesh_exts = {".stl", ".obj", ".ply", ".off"}
    xml_meshes = []

    with zipfile.ZipFile(path_3mf, "r") as zf:
        # Check for embedded mesh files first
        for name in zf.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext in mesh_exts:
                fd, tmp = tempfile.mkstemp(suffix=ext)
                with os.fdopen(fd, "wb") as dst:
                    dst.write(zf.read(name))
                return tmp  # caller handles cleanup via calculate_geometry's tmp_file

        # Check for native 3MF XML geometry
        for name in zf.namelist():
            if name.startswith("3D/Objects/") and name.endswith(".model"):
                data = zf.read(name)
                # Quick check: is it XML?
                if data[:5] == b"<?xml":
                    faces = _parse_3mf_xml_mesh(data)
                    if faces is not None and len(faces) > 0:
                        xml_meshes.append(faces)

    if xml_meshes:
        # Concatenate all meshes
        if len(xml_meshes) == 1:
            combined = xml_meshes[0]
        else:
            combined = np.concatenate(xml_meshes, axis=0)
        # Write to temp STL so trimesh can load it
        mesh = trimesh.Trimesh(vertices=combined.reshape(-1, 3), faces=np.arange(len(combined) * 3).reshape(-1, 3))
        fd, tmp = tempfile.mkstemp(suffix=".stl")
        with os.fdopen(fd, "wb") as dst:
            mesh.export(dst, file_type="stl")
        return tmp

    return None


def calculate_geometry(model_path):
    """Calculate model geometry (volume, surface_area, dimensions)."""
    tmp_file = None
    ext = os.path.splitext(model_path)[1].lower()

    try:
        if ext == ".3mf":
            tmp_file = _extract_geometry_from_3mf(model_path)
            if tmp_file is None:
                return 0, 0, {"x": 0, "y": 0, "z": 0}
            model_path = tmp_file

        mesh = trimesh.load(model_path, force="mesh")

        if isinstance(mesh, trimesh.Scene):
            geom = mesh.dump()
            mesh = trimesh.util.concatenate(geom)

        volume = mesh.volume
        surface_area = mesh.area

        extents = mesh.extents
        dimensions = {
            "x": round(float(extents[0]), 2),
            "y": round(float(extents[1]), 2),
            "z": round(float(extents[2]), 2),
        }

        if not volume or volume <= 0:
            if mesh.convex_hull.volume > 0:
                volume = mesh.convex_hull.volume
            else:
                volume = 0

        return volume, surface_area, dimensions

    except Exception:
        return 0, 0, {"x": 0, "y": 0, "z": 0}

    finally:
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
