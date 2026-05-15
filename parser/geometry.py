import trimesh
import zipfile
import tempfile
import os


def _extract_mesh_from_3mf(path_3mf: str) -> str:
    """Extract the mesh file from a 3MF container, return path to temp file."""
    mesh_exts = {".stl", ".obj", ".ply", ".off"}
    with zipfile.ZipFile(path_3mf, "r") as zf:
        for name in zf.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext in mesh_exts:
                fd, tmp = tempfile.mkstemp(suffix=ext)
                with os.fdopen(fd, "wb") as dst:
                    dst.write(zf.read(name))
                return tmp
            # Some 3MF files embed the model as .model
            if name.endswith(".model"):
                fd, tmp = tempfile.mkstemp(suffix=".stl")
                with os.fdopen(fd, "wb") as dst:
                    dst.write(zf.read(name))
                return tmp
    raise ValueError("3MF 文件中未找到可解析的模型数据")


def calculate_geometry(model_path):
    """Calculate model geometry (volume, surface_area, dimensions).

    Supports STL/STEP/OBJ via trimesh. 3MF files are unzipped first.
    """
    tmp_file = None
    ext = os.path.splitext(model_path)[1].lower()

    try:
        # 3MF: extract mesh from ZIP container
        if ext == ".3mf":
            tmp_file = _extract_mesh_from_3mf(model_path)
            model_path = tmp_file

        # trimesh can parse multiple 3D file formats
        mesh = trimesh.load(model_path, force="mesh")

        # In case the file contains multiple bodies
        if isinstance(mesh, trimesh.Scene):
            # concatenate all geometries
            geom = mesh.dump()
            mesh = trimesh.util.concatenate(geom)

        volume = mesh.volume
        surface_area = mesh.area

        # Calculate dimensions from bounding box
        extents = mesh.extents  # [x, y, z] array of lengths
        dimensions = {
            "x": round(extents[0], 2),
            "y": round(extents[1], 2),
            "z": round(extents[2], 2)
        }

        # If the mesh is not watertight, fallback to convex hull
        if not volume or volume <= 0:
            if mesh.convex_hull.volume > 0:
                volume = mesh.convex_hull.volume
            else:
                volume = 0

        return volume, surface_area, dimensions

    except Exception as e:
        return 0, 0, {"x": 0, "y": 0, "z": 0}

    finally:
        # Clean up temp file for 3MF extraction
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
