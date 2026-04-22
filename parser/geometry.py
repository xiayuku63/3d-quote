import trimesh

def calculate_geometry(model_path):
    """Calculate model geometry (volume, surface_area, dimensions)"""
    try:
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

        # If the mesh is not watertight (e.g., holes, inverted normals), volume might be None or 0
        if not volume or volume <= 0:
            # Fallback: attempt to calculate convex hull volume or use bounding box
            if mesh.convex_hull.volume > 0:
                volume = mesh.convex_hull.volume
                print("Warning: Mesh is not watertight, using convex hull volume as fallback.")
            else:
                volume = 0

        return volume, surface_area, dimensions
    except Exception as e:
        print(f"Error reading model with trimesh: {e}")
        return 0, 0, {"x": 0, "y": 0, "z": 0}
