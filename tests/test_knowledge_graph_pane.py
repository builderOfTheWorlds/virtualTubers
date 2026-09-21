"""
test_knowledge_graph_pane.py
Unit tests for app/knowledge_graph_pane.py's pure render function. This pane
is a static placeholder — no Kafka/filesystem dependency to mock.
conftest.py inserts app/ onto sys.path.
"""
import knowledge_graph_pane


def test_render_placeholder_graph_is_labeled():
    output = knowledge_graph_pane.render_placeholder_graph()
    assert "placeholder" in output.lower()


def test_render_placeholder_graph_contains_nodes_and_edges():
    output = knowledge_graph_pane.render_placeholder_graph()
    # Static node markers from PLACEHOLDER_GRAPH.
    for node in ("(A)", "(B)", "(C)", "(D)", "(E)"):
        assert node in output


def test_render_placeholder_graph_returns_stable_string():
    # Static/deterministic — no randomness, no time dependency.
    assert knowledge_graph_pane.render_placeholder_graph() == knowledge_graph_pane.render_placeholder_graph()


def test_render_placeholder_graph_notes_not_real_data():
    output = knowledge_graph_pane.render_placeholder_graph()
    assert "not wired to real campaign data" in output
