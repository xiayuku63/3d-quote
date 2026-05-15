"""STL parsing tests."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from stl.mesh import Mesh
from parser.geometry import calculate_geometry


def _make_cube_stl(size_mm: float = 10.0) -> str:
    """Generate a unit cube STL file, return path."""
    vertices = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ]) * size_mm

    faces = np.array([
        [0, 3, 1], [1, 3, 2],  # bottom
        [0, 4, 7], [0, 7, 3],  # back
        [4, 5, 6], [4, 6, 7],  # top
        [5, 1, 2], [5, 2, 6],  # right
        [0, 1, 5], [0, 5, 4],  # front
        [3, 7, 6], [3, 6, 2],  # left
    ])

    mesh_data = np.zeros(len(faces), dtype=Mesh.dtype)
    mesh_data["vectors"] = vertices[faces]

    fd, path = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    mesh = Mesh(mesh_data)
    mesh.save(path)
    return path


class TestCubeGeometry:
    """Basic geometry parsing for a 10mm cube."""

    def test_cube_volume(self):
        path = _make_cube_stl(10.0)
        vol, _, _ = calculate_geometry(path)
        os.unlink(path)
        # 10x10x10 mm³ = 1000 mm³
        assert abs(vol - 1000.0) < 10.0, f"Expected ~1000, got {vol}"

    def test_cube_surface_area(self):
        path = _make_cube_stl(10.0)
        _, sa, _ = calculate_geometry(path)
        os.unlink(path)
        # 6 faces × 100mm² = 600 mm²
        assert abs(sa - 600.0) < 10.0, f"Expected ~600, got {sa}"

    def test_cube_dimensions(self):
        path = _make_cube_stl(10.0)
        _, _, dims = calculate_geometry(path)
        os.unlink(path)
        assert 9.0 < dims["x"] < 11.0
        assert 9.0 < dims["y"] < 11.0
        assert 9.0 < dims["z"] < 11.0


class TestLargeModel:
    def test_large_cube(self):
        path = _make_cube_stl(100.0)
        vol, _, dims = calculate_geometry(path)
        os.unlink(path)
        assert vol > 500000  # 100³ ≈ 1e6 mm³
        assert dims["x"] > 50


class TestInvalidInput:
    def test_nonexistent_file(self):
        vol, sa, dims = calculate_geometry("/tmp/no_such_file_12345.stl")
        assert vol == 0
        assert sa == 0

    def test_empty_file(self):
        fd, path = tempfile.mkstemp(suffix=".stl")
        os.write(fd, b"")
        os.close(fd)
        vol, sa, dims = calculate_geometry(path)
        os.unlink(path)
        assert vol == 0


class TestMultipleSizes:
    def test_tiny_cube(self):
        path = _make_cube_stl(1.0)
        vol, _, _ = calculate_geometry(path)
        os.unlink(path)
        assert abs(vol - 1.0) < 0.5

    def test_medium_cube(self):
        path = _make_cube_stl(50.0)
        vol, _, _ = calculate_geometry(path)
        os.unlink(path)
        assert abs(vol - 125000.0) < 500  # 50³ = 125,000
