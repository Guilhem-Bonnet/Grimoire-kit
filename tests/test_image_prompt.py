"""Tests for image-prompt.py — Image prompt generator."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("image_prompt", TOOLS / "image-prompt.py")
ip = importlib.util.module_from_spec(_spec)
sys.modules["image_prompt"] = ip
_spec.loader.exec_module(ip)


# ── Generate ─────────────────────────────────────────────────────────────────


class TestGenerate:
    def test_basic_generation(self):
        result = ip.generate_prompt("a beautiful sunset over mountains")
        assert result.final_prompt
        assert "sunset" in result.final_prompt
        assert result.style == "generic"

    def test_midjourney_style(self):
        result = ip.generate_prompt("a cat", style="midjourney")
        assert "--v 6.1" in result.final_prompt
        assert "--q 2" in result.final_prompt

    def test_stable_diffusion_negative(self):
        result = ip.generate_prompt("a portrait", style="stable-diffusion",
                                     negative="blurry, low quality")
        assert "Negative prompt:" in result.final_prompt

    def test_art_style(self):
        result = ip.generate_prompt("landscape", art_style="watercolor")
        assert "watercolor" in result.final_prompt

    def test_lighting(self):
        result = ip.generate_prompt("portrait", lighting="golden hour")
        assert "golden hour" in result.final_prompt

    def test_composition(self):
        result = ip.generate_prompt("cityscape", composition="bird's eye view")
        assert "bird's eye view" in result.final_prompt

    def test_aspect_ratio_midjourney(self):
        result = ip.generate_prompt("flag", style="midjourney", aspect_ratio="16:9")
        assert "--ar 16:9" in result.final_prompt

    def test_aspect_ratio_generic(self):
        result = ip.generate_prompt("photo", aspect_ratio="1:1")
        assert "1:1" in result.final_prompt

    def test_no_quality(self):
        result = ip.generate_prompt("cat", quality=False)
        assert "high quality" not in result.final_prompt

    def test_quality_modifiers(self):
        result = ip.generate_prompt("dog", quality=True)
        assert len(result.quality_modifiers) > 0

    def test_tips_populated(self):
        result = ip.generate_prompt("bird", style="dalle")
        assert len(result.tips) > 0


# ── Refine ───────────────────────────────────────────────────────────────────


class TestRefine:
    def test_basic_refine(self):
        result = ip.refine_prompt("a cat sitting on a chair")
        assert "cat" in result.final_prompt

    def test_refine_enhance(self):
        result = ip.refine_prompt("a tree", enhance=True)
        assert "detailed" in result.final_prompt
        assert "sharp focus" in result.final_prompt

    def test_refine_with_style(self):
        result = ip.refine_prompt("ocean", style="midjourney")
        assert "--v 6.1" in result.final_prompt


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_mcp_generate(self):
        result = ip.mcp_image_prompt(".", action="generate", description="sunset")
        assert result["status"] == "ok"
        assert "final_prompt" in result

    def test_mcp_generate_missing(self):
        result = ip.mcp_image_prompt(".", action="generate")
        assert result["status"] == "error"

    def test_mcp_refine(self):
        result = ip.mcp_image_prompt(".", action="refine", prompt="cat", enhance=True)
        assert result["status"] == "ok"

    def test_mcp_refine_missing(self):
        result = ip.mcp_image_prompt(".", action="refine")
        assert result["status"] == "error"

    def test_mcp_options(self):
        result = ip.mcp_image_prompt(".", action="options")
        assert result["status"] == "ok"
        assert "styles" in result
        assert "art_styles" in result

    def test_mcp_unknown(self):
        result = ip.mcp_image_prompt(".", action="nope")
        assert result["status"] == "error"


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_generate_cli(self, capsys):
        ret = ip.main(["--project-root", ".", "generate", "--description", "mountain"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Prompt" in out

    def test_generate_cli_json(self, capsys):
        ret = ip.main(["--project-root", ".", "--json", "generate", "--description", "ocean"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "final_prompt" in data

    def test_refine_cli(self, capsys):
        ret = ip.main(["--project-root", ".", "refine", "--prompt", "a dog", "--enhance"])
        assert ret == 0

    def test_options_cli(self, capsys):
        ret = ip.main(["--project-root", ".", "options"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Art styles" in out or "Styles" in out
