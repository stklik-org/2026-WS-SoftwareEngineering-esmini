"""Contract: the committed docs/openapi.json equals the schema generated from the code (PLAN.md §8.1)."""

import json

from scripts.export_openapi import TARGET, render


def test_committed_openapi_is_current() -> None:
    assert TARGET.is_file(), "docs/openapi.json is missing; run `make openapi`"
    assert json.loads(TARGET.read_text()) == json.loads(render()), "docs/openapi.json is stale; run `make openapi`"


def test_every_operation_is_documented() -> None:
    schema = json.loads(render())
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"


def test_multipart_parts_render_as_form_fields_in_swagger_ui() -> None:
    """Swagger UI renders `anyOf [...]` arrays as text boxes and any `contentMediaType` as a file picker."""
    body = json.loads(render())["components"]["schemas"]["Body_create_simulation_v1_simulations_post"]
    props = body["properties"]
    assert props["files"]["type"] == "array" and props["files"]["items"] == {"type": "string", "format": "binary"}
    assert props["scenario"]["type"] == "string" and "contentMediaType" not in props["scenario"]
    assert props["config"]["type"] == "string" and "contentMediaType" not in props["config"]
    assert "anyOf" not in props["files"] and "anyOf" not in props["config"]
    assert body["required"] == ["files", "scenario"]


def test_road_preview_config_renders_as_text_field() -> None:
    schemas = json.loads(render())["components"]["schemas"]
    props = schemas["Body_create_road_preview_v1_road_previews_post"]["properties"]
    assert props["config"]["type"] == "string" and "anyOf" not in props["config"]
    assert props["road"]["type"] == "string" and props["road"].get("contentMediaType") == "application/octet-stream"


def test_config_schemas_are_documented() -> None:
    """The config parts are parsed from text, so their schemas must be added to the document explicitly."""
    components = json.loads(render())["components"]["schemas"]
    for name in ("SimulationConfig", "RoadPreviewConfig"):
        assert name in components, name
    assert set(components["RoadPreviewConfig"]["properties"]) >= {"width", "height", "roadIds", "samplingStep"}
    assert "additionalProperties" in components["RoadPreviewConfig"]  # extra="forbid" is visible to clients
    text = json.dumps(components)
    for ref in set(__import__("re").findall(r"#/components/schemas/(\w+)", text)):
        assert ref in components, f"dangling reference to {ref}"
