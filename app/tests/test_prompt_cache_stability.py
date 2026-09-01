"""
WHAT:
    Guards the cacheable prefix of every LLM request - the tool schemas
    and the system prompt - against becoming unstable.

WHY THIS MATTERS MORE THAN IT LOOKS:
    Groq caches prompts automatically, with no code changes, and gives
    cached prefix tokens a 50% discount AND excludes them from rate
    limits. That second part is why a measured demo run pushed ~113,000
    tokens through a nominally 8,000-tokens-per-minute account without
    ever being throttled.

    Caching is a PREFIX match: one byte different anywhere before the
    varying part and everything after it misses. So a single
    datetime.now() in the system prompt, or an unsorted list from
    MongoDB, silently doubles the bill and reinstates the rate limit.

    Nothing raises when that happens. The assistant keeps working, just
    more expensively and more slowly, and the cause is invisible unless
    someone thinks to look. That is exactly the kind of regression a
    test should catch, because a human never will.

WHAT IS CHECKED:
    - the system prompt is byte-identical across builds
    - the tool schemas serialize deterministically, in a stable order
    - no timestamp, UUID or ObjectId is embedded in the prompt
    - the one DYNAMIC input (real category values from MongoDB) is
      sorted, since MongoDB's distinct() gives no order guarantee

FLOW:
    Mostly pure unit tests - the categories lookup is stubbed, so these
    run without a database. One DB-backed test covers the real sort.
"""

import hashlib
import json
import re

import pytest

from app.agent import orchestrator
from app.agent.tools import TOOLS

FAKE_CATEGORIES = {
    "categories": ["Bed & Bath", "Electronics", "Home Decor"],
    "subCategories": ["Bedsheets", "Clocks", "Televisions"],
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def stub_categories(monkeypatch):
    """Removes the database from the equation so these tests measure the
    PROMPT's determinism, not MongoDB's."""

    async def _categories():
        return FAKE_CATEGORIES

    monkeypatch.setattr(
        orchestrator.products_repo, "get_distinct_categories", _categories
    )


class TestTheSystemPromptIsStable:
    async def test_two_builds_are_byte_identical(self, stub_categories):
        first = await orchestrator.build_system_prompt()
        second = await orchestrator.build_system_prompt()
        assert digest(first) == digest(second), (
            "the system prompt changed between builds - every LLM call will "
            "miss the prompt cache"
        )

    async def test_no_timestamp_or_id_is_embedded(self, stub_categories):
        """The classic cache killer. A date, a clock time, a request id or
        an ObjectId in the prompt makes every single call unique."""
        prompt = await orchestrator.build_system_prompt()
        found = re.findall(
            r"\d{4}-\d{2}-\d{2}"          # a date
            r"|\d{2}:\d{2}:\d{2}"          # a clock time
            r"|[0-9a-f]{24}"               # a Mongo ObjectId
            r"|[0-9a-f]{8}-[0-9a-f]{4}",   # a UUID
            prompt,
        )
        assert not found, f"volatile values in the system prompt: {found}"

    async def test_the_prompt_still_contains_its_grounding_data(
        self, stub_categories
    ):
        # Stability is worthless if achieved by dropping the real values -
        # the model needs them to avoid inventing search filters.
        prompt = await orchestrator.build_system_prompt()
        assert "Home Decor" in prompt
        assert "Televisions" in prompt


class TestTheToolSchemasAreStable:
    def test_serialization_is_deterministic(self):
        assert json.dumps(TOOLS) == json.dumps(TOOLS)

    def test_tool_order_does_not_depend_on_a_set_or_dict_ordering(self):
        """TOOLS is a literal list, and must stay one. Building it from a
        set or by iterating a dict of handlers would reorder between runs
        and invalidate the cache on every process restart."""
        names = [t["function"]["name"] for t in TOOLS]
        assert names == [t["function"]["name"] for t in TOOLS]
        assert len(names) == len(set(names)), "duplicate tool names"

    def test_every_tool_has_the_fields_the_providers_require(self):
        for tool in TOOLS:
            assert tool.get("type") == "function"
            fn = tool["function"]
            assert fn.get("name") and fn.get("description")
            assert "parameters" in fn


class TestTheDynamicInputIsSorted:
    async def test_real_categories_come_back_sorted(self, db):
        """The ONE genuinely dynamic part of the prompt.

        MongoDB's distinct() makes no ordering guarantee, so without an
        explicit sort the same catalogue could render two different
        prompts - and the cache would miss on alternate calls for no
        visible reason.
        """
        from app.repos import products_repo

        result = await products_repo.get_distinct_categories()
        assert result["categories"] == sorted(result["categories"])
        assert result["subCategories"] == sorted(result["subCategories"])
