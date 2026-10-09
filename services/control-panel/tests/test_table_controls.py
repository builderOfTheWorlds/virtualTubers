"""Live-table operator buttons post scene_request / scene_stop / table_status_request to tuber_0."""
import pathlib

BASE = pathlib.Path(__file__).resolve().parents[1] / "templates" / "base.html"


def test_live_table_buttons_present():
    html = BASE.read_text()
    assert "Live table" in html
    for t in ("scene_request", "scene_stop", "table_status_request"):
        assert f'"type": "{t}"' in html
    assert '\\"next\\": true' in html and '\\"force\\": true' in html
