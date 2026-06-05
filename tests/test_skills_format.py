"""
Structural lint tests for wsl/skills/ SKILL.md files.

Each SKILL.md must have:
  (a) YAML frontmatter with 'name' and 'description' fields
  (b) At least one section header (## or <b>...</b>)
  (c) File size > 200 bytes (not an empty stub)
"""

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "wsl" / "skills"


def _skill_files():
    """Return a list of (skill_name, path) tuples for all SKILL.md files found."""
    return [
        (p.parent.name, p)
        for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))
    ]


# Parametrize over discovered skill files so failures are reported per-skill.
@pytest.mark.parametrize("skill_name,skill_path", _skill_files())
class TestSkillFormat:

    def test_file_size_above_minimum(self, skill_name, skill_path):
        """SKILL.md must be more than 200 bytes — not an empty stub."""
        size = skill_path.stat().st_size
        assert size > 200, (
            f"{skill_name}/SKILL.md is only {size} bytes — appears to be an empty stub"
        )

    def test_has_yaml_frontmatter(self, skill_name, skill_path):
        """SKILL.md must begin with a YAML frontmatter block delimited by '---'."""
        text = skill_path.read_text(encoding="utf-8")
        assert text.startswith("---"), (
            f"{skill_name}/SKILL.md does not start with '---' (missing frontmatter)"
        )
        # Find the closing delimiter
        end = text.find("---", 3)
        assert end != -1, (
            f"{skill_name}/SKILL.md frontmatter is not closed with '---'"
        )

    def test_frontmatter_has_name_field(self, skill_name, skill_path):
        """Frontmatter must contain a 'name:' field."""
        text = skill_path.read_text(encoding="utf-8")
        frontmatter = _extract_frontmatter(text)
        assert frontmatter is not None, f"{skill_name}/SKILL.md has no parseable frontmatter"
        assert re.search(r"^name\s*:", frontmatter, re.MULTILINE), (
            f"{skill_name}/SKILL.md frontmatter is missing 'name:' field"
        )

    def test_frontmatter_has_description_field(self, skill_name, skill_path):
        """Frontmatter must contain a 'description:' field."""
        text = skill_path.read_text(encoding="utf-8")
        frontmatter = _extract_frontmatter(text)
        assert frontmatter is not None, f"{skill_name}/SKILL.md has no parseable frontmatter"
        assert re.search(r"^description\s*:", frontmatter, re.MULTILINE), (
            f"{skill_name}/SKILL.md frontmatter is missing 'description:' field"
        )

    def test_has_section_header(self, skill_name, skill_path):
        """Body must contain at least one section header (## or <b>...</b>)."""
        text = skill_path.read_text(encoding="utf-8")
        body = _extract_body(text)
        has_markdown_header = bool(re.search(r"^#{1,6}\s+\S", body, re.MULTILINE))
        has_bold_header = bool(re.search(r"<b>.+?</b>", body))
        assert has_markdown_header or has_bold_header, (
            f"{skill_name}/SKILL.md body has no section header (## or <b>...</b>)"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_frontmatter(text: str) -> str | None:
    """Return the YAML frontmatter block content (between the two --- lines), or None."""
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    return text[3:end]


def _extract_body(text: str) -> str:
    """Return the body of the SKILL.md (everything after the closing frontmatter ---)."""
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:]


# ---------------------------------------------------------------------------
# Sanity check: at least one SKILL.md file was found
# ---------------------------------------------------------------------------

def test_skills_directory_has_skill_files():
    """The skills directory must contain at least one SKILL.md file."""
    files = _skill_files()
    assert len(files) >= 1, (
        f"No SKILL.md files found under {SKILLS_DIR} — skills may not have been transferred"
    )
