"""
test_knowledge_graph_pane.py
Unit tests for the pure/testable parts of app/knowledge_graph_pane.py.
Deliberately excludes render_graph_3d/_make_context_lazy/_make_camera_lazy
— those import termgl, which only exists under the /opt/render3d Python
3.11 venv, not this project's regular test venv (see the module docstring).
conftest.py inserts app/ onto sys.path.
"""
import knowledge_graph_pane
from mesh3d import build_node_positions, build_graph_mesh, TRIG3D_DTYPE


def test_placeholder_data_is_internally_consistent():
    # Every edge index must reference a real placeholder node.
    n = len(knowledge_graph_pane.PLACEHOLDER_NODES)
    for a, b in knowledge_graph_pane.PLACEHOLDER_EDGES:
        assert 0 <= a < n
        assert 0 <= b < n


def test_build_node_positions_matches_placeholder_node_count():
    positions = build_node_positions(len(knowledge_graph_pane.PLACEHOLDER_NODES),
                                      radius=knowledge_graph_pane.NODE_RADIUS)
    assert len(positions) == len(knowledge_graph_pane.PLACEHOLDER_NODES)


def test_build_node_positions_empty_for_zero_nodes():
    assert build_node_positions(0) == []


def test_build_graph_mesh_returns_trig3d_dtype_and_edge_segments():
    positions = build_node_positions(len(knowledge_graph_pane.PLACEHOLDER_NODES))
    trigs, edges = build_graph_mesh(positions, knowledge_graph_pane.PLACEHOLDER_EDGES)
    assert trigs.dtype == TRIG3D_DTYPE
    assert len(trigs) > 0
    assert len(edges) == len(knowledge_graph_pane.PLACEHOLDER_EDGES)
    for p0, p1 in edges:
        assert len(p0) == 3
        assert len(p1) == 3


def test_build_graph_mesh_skips_out_of_range_edges():
    positions = build_node_positions(3)
    trigs, edges = build_graph_mesh(positions, [(0, 1), (5, 6)])
    assert len(edges) == 1  # (5, 6) silently dropped, never crashes
