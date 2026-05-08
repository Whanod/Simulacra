"""BE-005: ui_schema lint — every referenced field must exist in
the corresponding JSON Schema, and specialEditor keys for world and
historical/composite feeds must match the frontend's expectations.
"""

from __future__ import annotations

from defi_sim.engine.specs import (
    _CATEGORY_TABLES,
    get_registry_metadata,
    iter_registry_categories,
)


class TestUiSchemaLint:
    def test_every_ui_field_references_a_schema_property(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                if meta.ui_schema is None or meta.schema is None:
                    continue
                schema_props: dict = meta.schema.get("properties", {}) or {}
                for ui_field in (meta.ui_schema.get("fields") or {}):
                    assert ui_field in schema_props, (
                        f"{category}/{spec_type} ui_schema.fields.{ui_field} "
                        f"references unknown schema property"
                    )

    def test_sections_only_reference_known_fields(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                if meta.ui_schema is None:
                    continue
                sections = meta.ui_schema.get("sections") or []
                ui_fields = meta.ui_schema.get("fields") or {}
                for section in sections:
                    for field_key in section.get("fields", []):
                        assert field_key in ui_fields, (
                            f"{category}/{spec_type} section "
                            f"{section.get('key')!r} references unknown field "
                            f"{field_key!r}"
                        )


class TestSpecialEditors:
    def test_world_market_uses_world_markets_graph(self):
        meta = get_registry_metadata("markets", "world")
        assert meta is not None
        assert meta.ui_schema is not None
        assert meta.ui_schema.get("specialEditor") == "world-markets-graph"

    def test_historical_feed_uses_code_editor(self):
        meta = get_registry_metadata("feeds", "historical")
        assert meta is not None
        assert meta.ui_schema is not None
        assert meta.ui_schema.get("specialEditor") == "code-editor"

    def test_composite_feed_uses_code_editor(self):
        meta = get_registry_metadata("feeds", "composite")
        assert meta is not None
        assert meta.ui_schema is not None
        assert meta.ui_schema.get("specialEditor") == "code-editor"


class TestUiSchemaCoverage:
    def test_every_builtin_has_a_ui_schema(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                assert meta.ui_schema is not None, (
                    f"{category}/{spec_type} missing ui_schema"
                )
