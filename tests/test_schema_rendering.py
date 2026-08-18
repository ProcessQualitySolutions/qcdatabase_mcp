"""Rendering tests: API rows pass through the MCP server unmolested.

QC Database treats data transparency as a design goal, so the browse/list tools
must surface EVERY field of every row - not a curated name/id/status subset. These
regression-cover the general principle plus the two originally-reported gaps:

1. Form-schema field definitions must reach the agent (it was guessing form fields).
2. List-item field VALUES (e.g. a welder's full_name) and the list's field
   definitions must reach the agent (so "is <person> on the <x> list?" is answerable).

They exercise the pure renderers with the documented response shapes, so no network
or auth is needed. Run: ``pytest`` (from the repo root).
"""

from __future__ import annotations

from qcdatabase_mcp import server


# ---------------------------------------------------------------------------
# The shared row dumper + list renderer - dump every field, hide nothing
# ---------------------------------------------------------------------------
def test_render_row_dumps_every_field():
    row = {
        "id": "x1",
        "name": "Widget",
        "status": "active",
        "heat_number": "H-42",
        "nested": {"grade": "A106"},
    }
    out = server._render_row(1, row)
    assert "Widget" in out and "[active]" in out  # scannable header
    for token in ("id", "x1", "heat_number", "H-42", "grade", "A106"):
        assert token in out  # nothing dropped


def test_render_list_dumps_all_fields_and_fences():
    # A map-item schema row: its custom field definitions must come through.
    rows = [{"id": "s1", "name": "Weld", "schema_definition": {"joint": "BW"}}]
    out = server._render_list("Map item schemas:", rows)
    assert out.startswith(server._DATA_FENCE)
    assert "Map item schemas:" in out
    for token in ("schema_definition", "joint", "BW"):
        assert token in out


def test_render_list_empty_uses_custom_message_without_fence():
    out = server._render_list("Projects:", [], empty="No projects found.")
    assert "No projects found." in out
    assert not out.startswith(server._DATA_FENCE)  # no data => no fence


def test_render_list_handles_non_dict_rows():
    out = server._render_list("Codes:", ["R", "C"])
    assert "R" in out and "C" in out


# ---------------------------------------------------------------------------
# Issue 1 - form-schema field definitions reach the agent (via full dump)
# ---------------------------------------------------------------------------
def test_form_schema_rows_expose_full_definition():
    rows = [
        {
            "id": "fs-1",
            "name": "Hydro Test",
            "is_active": True,
            "schema": {
                "properties": {
                    "test_pressure": {"type": "number"},
                    "medium": {"type": "string", "enum": ["water", "air"]},
                },
                "required": ["test_pressure"],
            },
            "ui_schema": {"ui:order": ["test_pressure"]},
        }
    ]
    out = server._render_list("Form schemas:", rows)
    for token in (
        "Hydro Test",
        "fs-1",
        "test_pressure",
        "medium",
        "water",
        "air",
        "required",
        "ui_schema",  # even secondary fields are not hidden
    ):
        assert token in out


# ---------------------------------------------------------------------------
# Issue 2 - list-item data + the list's schema reach the agent
# ---------------------------------------------------------------------------
def _welder_envelope():
    return {
        "list_schema": {"id": "list-w", "name": "Welders"},
        "schema": {
            "properties": {
                "full_name": {"type": "string"},
                "certification": {"type": "string"},
            }
        },
        "items": [
            {
                "id": "w-1",
                "name": "W-01",
                "pseudo_code": "[[item_name:W-01|item_id:w-1|project_id:p1]]",
                "status": "active",
                "data": {"full_name": "Larry Smith", "certification": "6G"},
                "web_url": "https://qcdatabase.ai/lists/list-w/w-1",
            },
            {
                "id": "w-2",
                "name": "W-02",
                "status": "active",
                "data": {"full_name": "Dana Lee"},
            },
        ],
    }


def test_list_items_expose_data_values_and_schema():
    out = server._render_list_items(_welder_envelope())
    # The failing query ("was Larry added to the welder list?") is now answerable.
    assert "Larry Smith" in out and "6G" in out and "Dana Lee" in out
    # The list's own field definitions are present.
    assert "List field schema:" in out
    assert "full_name" in out and "certification" in out
    # The pill token the map-item workflow depends on survives.
    assert "[[item_name:W-01" in out
    assert out.startswith(server._DATA_FENCE)


def test_list_items_accept_bare_list():
    out = server._render_list_items(
        [{"id": "x", "name": "X-1", "data": {"full_name": "Pat Roe"}}]
    )
    assert "Pat Roe" in out


def test_list_items_tolerate_paginated_results_envelope():
    # If the API ever returns DRF-style pagination, items must still render
    # instead of silently looking empty.
    out = server._render_list_items(
        {"results": [{"id": "r1", "name": "R-1", "data": {"full_name": "Sam Poe"}}], "next": None}
    )
    assert "Sam Poe" in out
    assert "no items" not in out.lower()


def test_list_items_empty():
    assert "no items" in server._render_list_items({"items": []}).lower()


def test_list_items_schema_only_still_shows_schema():
    out = server._render_list_items(
        {"schema": {"properties": {"a": {"type": "string"}}}, "items": []}
    )
    assert "List field schema:" in out
    assert "properties" in out


# ---------------------------------------------------------------------------
# Semantic search - dump full rows, keep the relevance metadata
# ---------------------------------------------------------------------------
def test_search_dumps_full_rows_with_relevance():
    payload = {
        "query": "welder",
        "count": 1,
        "results": [
            {
                "id": "m1",
                "name": "W-12",
                "similarity": 0.87,
                "data": {"full_name": "Larry Smith"},
            }
        ],
    }
    out = server._render_search("Semantic search:", payload)
    assert "relevance 0.87" in out
    assert "Larry Smith" in out  # full row, not just name/id
    assert server._DATA_FENCE in out


# ---------------------------------------------------------------------------
# Bounded output - long values are trimmed (with a marker), never dropped
# ---------------------------------------------------------------------------
def test_fmt_value_truncates_oversized_scalar():
    out = server._fmt_value("x" * 5000)
    assert "truncated" in out and len(out) < 700


def test_pretty_marks_truncated_long_arrays():
    # Bounding a huge array must leave a visible marker, not silently drop the tail.
    out = server._pretty({"items": list(range(120))})
    assert "more items truncated" in out
