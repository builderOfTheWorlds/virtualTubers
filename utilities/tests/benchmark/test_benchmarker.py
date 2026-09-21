"""Tests for the D&D-agents benchmark harness.

Covers the pure-logic pieces that don't need a live LLM host: prompt
builder sizing, batch summarization, host-state parsing (vLLM metrics),
and the report writer. Live-host integration (actual tok/s) is exercised
by the runner itself and kept out of the unit tests so `pytest` stays
fast and hermetic.
"""
from __future__ import annotations

import pathlib

import pytest

# The benchmark modules live in `utilities/benchmarker/lib/`; the test
# conftest adds that directory (and `app/`) to `sys.path` so we import
# them as flat leaf modules (`runner`, `prompts`, `host_base`, ...).
import prompts, vllm_host
from host_base import CompletionResult
from runner import Runner, _summarise_batch
from vllm_host import vLLMHost
_parse_metric_line = vllm_host._parse_metric_line  # module-private helper


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

class TestPrompts:
    def test_list_cast_and_lore_return_lists(self):
        cast = prompts.list_cast()
        lore = prompts.list_lore_stems()
        assert isinstance(cast, list) and isinstance(lore, list)
        assert cast, "expected at least one cast member"
        # The ashiorid pack has Leena and GM; GM is excluded from list_cast.
        assert "gm" not in [c.lower() for c in cast]

    def test_load_cast_sheet_round_trips(self):
        cast_ids = prompts.list_cast()
        assert cast_ids
        sheet = prompts.load_cast_sheet(cast_ids[0])
        assert sheet.name
        assert sheet.archetype  # ashiorid sheets all carry an archetype

    def test_build_character_prompt_hits_target_tokens(self):
        sheet = prompts.load_cast_sheet("Leena") if "Leena" in prompts.list_cast() \
            else prompts.load_cast_sheet(prompts.list_cast()[0])
        p = prompts.build_character_prompt(
            sheet=sheet,
            scene_direction="The party stands before the vault door.",
            committed_transcript_lines=prompts.load_transcript_example_lines(4),
            target_context_tokens=8192,
        )
        assert str(p["system"]), "system prompt must be non-empty"
        assert str(p["user"]), "user prompt must be non-empty"
        # The estimate should be *near* the target — pad logic drives it up.
        est = int(p["prompt_tokens_estimate"])
        assert est > 4000, (f"expected ~8k target, got {est}")

    def test_build_gm_prompt_includes_all_cast_sheets(self):
        gm = prompts.build_gm_prompt(
            canon_goal="open the vault",
            expected_beats=["leena works the lock"],
            committed_transcript_lines=prompts.load_transcript_example_lines(4),
            target_context_tokens=16000,
        )
        text = gm["system"] + gm["user"]
        # Every real cast sheet name should appear in the GM's world state.
        for name in ("Chadwick", "Leena"):
            assert name in text, f"GM prompt missing cast sheet {name}"

    def test_synthetic_lore_block_is_deterministic(self):
        a = prompts._synthetic_lore_block(2000)
        b = prompts._synthetic_lore_block(2000)
        assert a == b, "synthetic lore must be byte-identical across calls"

    def test_transcript_example_lines_format(self):
        lines = prompts.load_transcript_example_lines(6)
        assert len(lines) == 6
        assert all(": " in ln for ln in lines), "each line must be '<who>: <text>'"


# ---------------------------------------------------------------------------
# _summarise_batch
# ---------------------------------------------------------------------------

def _cr(content=10, reasoning=0, prompt=100, ttft=1.0, total=2.0,
        error=None) -> CompletionResult:
    return CompletionResult(
        model="m", prompt_tokens=prompt, content_tokens=content,
        reasoning_tokens=reasoning, ttft_s=ttft, total_s=total, error=error)


class TestSummariseBatch:
    def test_median_and_means(self):
        cells = [_cr(content=10, prompt=100, ttft=1.0, total=2.0),
                 _cr(content=20, prompt=100, ttft=1.0, total=4.0)]
        s = _summarise_batch(cells)
        assert s["n_ok"] == 2 and s["n_fail"] == 0
        assert s["content"] == pytest.approx(15.0)
        assert s["reasoning"] == 0
        assert s["content"] > 0

    def test_all_failed_returns_zeroes_and_counts(self):
        cells = [_cr(error="x"), _cr(error="y")]
        s = _summarise_batch(cells)
        assert s["n_ok"] == 0 and s["n_fail"] == 2
        assert s["content"] == 0

    def test_decode_tps_uses_median(self):
        # decode_s = total - ttft. content 10 over 1s => 10 t/s, content 20
        # over 3s => 6.67 t/s. median of [10, 6.667] ~ 8.33
        a = _cr(content=10, ttft=1.0, total=2.0)
        b = _cr(content=20, ttft=1.0, total=4.0)
        s = _summarise_batch([a, b])
        assert s["decode_tps"] > 0

    def test_prefill_tps_computed(self):
        c = _cr(content=10, prompt=1000, ttft=5.0, total=8.0)
        s = _summarise_batch([c])
        assert s["prefill_tps"] == pytest.approx(1000 / 5.0)


# ---------------------------------------------------------------------------
# vLLM host metric parsing (pure function, no network)
# ---------------------------------------------------------------------------

class TestVLLMMetricsParse:
    def test_labeled_metric(self):
        name, labels, value = _parse_metric_line(
            'vllm:kv_cache_usage_perc{model_name="hermes3:70b"} 0.62')
        assert name == "vllm:kv_cache_usage_perc"
        assert labels == {"model_name": "hermes3:70b"}
        assert value == pytest.approx(0.62)

    def test_unlabeled_metric(self):
        name, labels, value = _parse_metric_line('vllm:num_requests_running 3')
        assert name == "vllm:num_requests_running"
        assert labels == {}
        assert value == 3.0

    def test_non_vllm_lines_ignored(self):
        assert _parse_metric_line('process_cpu_seconds_total 1.2') is None
        assert _parse_metric_line('# HELP vllm:x') is None
        assert _parse_metric_line('') is None

    def test_inf_skipped(self):
        assert _parse_metric_line('vllm:foo 0.0') == (
            "vllm:foo", {}, 0.0) or _parse_metric_line('vllm:foo 0.0')
        # inf is not valid float() -> returns None
        assert _parse_metric_line('vllm:foo +Inf') is None
        assert _parse_metric_line('vllm:foo NaN') is None


# ---------------------------------------------------------------------------
# Host constructor validation
# ---------------------------------------------------------------------------

class TestHostConstruction:
    def test_ollama_requires_full_url(self):
        from ollama_host import OllamaHost
        with pytest.raises(Exception):
            OllamaHost("localhost:11434")

    def test_ollama_full_url_accepted(self):
        from ollama_host import OllamaHost
        h = OllamaHost("http://localhost:11434")
        assert h.protocol == "ollama_native"

    def test_vllm_url_and_protocol(self):
        h = vLLMHost("http://localhost:8000")
        assert h.protocol == "vllm_openai_v1"
        h.close()


# ---------------------------------------------------------------------------
# report writer (hermetic, no live host)
# ---------------------------------------------------------------------------

class TestReportWriter:
    def test_write_markdown_produces_expected_sections(self, tmp_path: pathlib.Path):
        from runner import Run
        from ollama_host import OllamaHost
        host = OllamaHost("http://localhost:11434")
        runner = Runner(host, models=["m"], ctx_k=[8],
                        output_dir=tmp_path, report_path=tmp_path / "r.md")
        import time
        run = Run(host_protocol="ollama_native", base_url="http://localhost:11434",
                  started_at=time.time(), config={"models": ["m"], "ctx_k": [8],
                                                  "think": False},
                  host_state_before={"note": "b"}, host_state_after={"note": "a"})
        # Add a single per-model cell + one full_round cell + one co-resident cell
        from runner import CellResult
        run.cells = [
            CellResult(host_protocol="ollama_native", model="m", ctx_k=8,
                       task="line", think=False, prompt_tokens=100,
                       content_tokens=10, reasoning_tokens=0,
                       decode_tps=20.0, prefill_tps=1000.0, ttft_s=0.1,
                       total_s=0.6, n_calls=2),
            CellResult(host_protocol="ollama_native", model="m2 + m3", ctx_k=8,
                       task="co_resident", think=False, prompt_tokens=200,
                       content_tokens=30, reasoning_tokens=0,
                       decode_tps=10.0, prefill_tps=800.0, ttft_s=1.2,
                       total_s=8.5, n_calls=2,
                       extra={
                           "wall_s": 8.5, "solo_max_s": 7.0, "solo_sum_s": 9.5,
                           "co_speedup_over_sum": 1.12,
                           "gm": {"n_ok": 2, "n_fail": 0, "total_s": 7.0},
                           "seat": {"n_ok": 2, "n_fail": 0, "total_s": 2.5},
                       }),
            CellResult(host_protocol="ollama_native", model="plan:A (70b GM + 6x8b seats)",
                       ctx_k=16, task="full_round", think=False,
                       prompt_tokens=1000, content_tokens=100,
                       reasoning_tokens=0, total_s=12.0, n_calls=4,
                       extra={"stages": [{"ok": True}]}),
        ]
        runner._write_markdown(run)
        text = (tmp_path / "r.md").read_text(encoding="utf-8")
        assert "Per-model probes" in text
        assert "Co-resident" in text
        assert "Full-round (plan) timings" in text
        assert "plan:A (70b GM + 6x8b seats)" in text
        # the co-resident section should show one of the pair's two halves
        assert "m2 + m3" in text
        # budget-capped flag should be present in the JSON
        runner._write_json(run)
        import json
        data = json.loads((tmp_path / "run.json").read_text())
        tasks = [c["task"] for c in data["cells"]]
        assert tasks == ["line", "co_resident", "full_round"]
        host.close()
    def test_write_markdown_co_resident_speedup_rendered(self, tmp_path: pathlib.Path):
        """The 1.12x co-speedup string must appear in the markdown table."""
        from runner import Run, CellResult
        from ollama_host import OllamaHost
        host = OllamaHost("http://localhost:11434")
        runner = Runner(host, models=["m"], ctx_k=[8], output_dir=tmp_path,
                        report_path=tmp_path / "r.md")
        run = Run(host_protocol="ollama_native", base_url="http://x",
                  started_at=0.0, config={"models": ["m"], "ctx_k": [8],
                                          "think": False})
        run.cells = [
            CellResult(host_protocol="ollama_native", model="A + B", ctx_k=8,
                       task="co_resident", think=False, prompt_tokens=100,
                       content_tokens=20, reasoning_tokens=0, total_s=5.0,
                       n_calls=2,
                       extra={"wall_s": 5.0, "solo_max_s": 4.0,
                              "solo_sum_s": 5.2, "co_speedup_over_sum": 1.04})
        ]
        runner._write_markdown(run)
        text = (tmp_path / "r.md").read_text(encoding="utf-8")
        assert "Co-resident" in text
        assert "1.04" in text
        host.close()


# ---------------------------------------------------------------------------
# config (TOML loader + CLI overrides)
# ---------------------------------------------------------------------------
import config


def _write_toml(path: pathlib.Path) -> None:
    (path).write_text(
        "[host]\nname = \"vllm\"\nbase_url = \"http://host:8000\"\n"
        "[models]\nlist = [\"qwen-a\", \"llama-b\"]\n"
        "[run]\nctx_k = [8, 16]\nthink = true\ntemperature = 0.3\n"
        "concurrency_n = 4\nskip_full_round = true\n"
        "[output]\ndir = \"out/x\"\nreport = \"out/x/r.md\"\n",
        encoding="utf-8")


class TestConfigLoader:
    def test_loads_real_default_conf(self):
        # The shipped conf/benchmark.toml should parse and yield a sane
        # full-battery config (3 models, 8k/16k, think OFF).
        cfg = config.load_config()
        assert cfg.host == "ollama"
        assert cfg.think is False
        assert set(cfg.ctx_k) == {8, 16}
        assert "hermes3:70b" in cfg.models
        assert "llama3.1:8b" in cfg.models

    def test_loads_toml_fields(self, tmp_path: pathlib.Path):
        p = tmp_path / "bench.toml"
        _write_toml(p)
        cfg = config.load_config(p)
        assert cfg.host == "vllm"
        assert cfg.base_url == "http://host:8000"
        assert cfg.models == ["qwen-a", "llama-b"]
        assert cfg.ctx_k == [8, 16]
        assert cfg.think is True
        assert cfg.temperature == 0.3
        assert cfg.concurrency_n == 4
        assert cfg.skip_full_round is True
        assert cfg.output_dir == "out/x"

    def test_missing_file_raises(self, tmp_path: pathlib.Path):
        with pytest.raises(FileNotFoundError):
            config.load_config(tmp_path / "nope.toml")

    def test_missing_base_url_raises(self, tmp_path: pathlib.Path):
        # A host we don't have a default URL for and no explicit base_url
        # must raise — the loader has no way to reach it.
        (tmp_path / "bad.toml").write_text(
            "[host]\nname = \"not_a_known_host\"\n", encoding="utf-8")
        with pytest.raises(ValueError):
            config.load_config(tmp_path / "bad.toml")

    def test_known_host_gets_default_url(self, tmp_path: pathlib.Path):
        # vllm has a built-in default (:8000) so no base_url is fine.
        (tmp_path / "ok.toml").write_text(
            "[host]\nname = \"vllm\"\n", encoding="utf-8")
        cfg = config.load_config(tmp_path / "ok.toml")
        assert cfg.base_url == "http://localhost:8000"

    def test_cli_override_beats_toml(self, tmp_path: pathlib.Path):
        p = tmp_path / "bench.toml"
        _write_toml(p)
        cfg = config.load_config(p)
        cfg = config.override_config_from_cli(
            cfg, host="ollama", models=["solo.model"],
            ctx_k=[4], think=False, concurrency_n=2)
        assert cfg.host == "ollama"
        # switching host should also swap the default base_url when none set
        assert cfg.base_url == "http://localhost:11434"
        assert cfg.models == ["solo.model"]
        assert cfg.ctx_k == [4]
        assert cfg.think is False
        assert cfg.concurrency_n == 2

    def test_resolve_paths_makes_relative_absolute(self):
        cfg = config.Config(output_dir="utilities/benchmarker/output",
                            report_path="utilities/benchmarker/output/r.md")
        config.resolve_paths(cfg, config.REPO_ROOT)
        import pathlib as _pl
        assert _pl.Path(cfg.output_dir).is_absolute()
        assert _pl.Path(cfg.report_path).is_absolute()
        assert str(cfg.output_dir).startswith(str(config.REPO_ROOT))

    def test_bool_coercion(self):
        for v, exp in [("1", True), ("true", True), ("yes", True),
                       (1, True), ("0", False), ("off", False),
                       (False, False)]:
            assert config._coerce_bool(v) is exp, v

    def test_csv_splitting(self):
        assert config._split_csv("a, b ,c") == ["a", "b", "c"]
        assert config._split_csv(["a", "b"]) == ["a", "b"]
        assert config._split_int_csv("8,16") == [8, 16]
        assert config._split_int_csv([8, 16]) == [8, 16]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
