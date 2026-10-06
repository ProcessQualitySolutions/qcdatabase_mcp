"""Keep schema definitions read-only without removing record workflows."""

import ast
import inspect

from qcdatabase_mcp import server


def test_registered_definition_tools_are_read_only_and_no_generic_http_tool():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    for name, tool in tools.items():
        if "schema" in name or ("map_item" in name and "type" in name):
            assert tool.annotations.readOnlyHint, name
        # QR generation takes a URL as content, not a request destination.
        forbidden = {"method", "endpoint", "http_method"}
        if name != "generate_qr_code":
            forbidden.add("url")
        assert not forbidden & set(
            tool.parameters.get("properties", {})
        ), name
    for name in (
        "list_map_item_schemas", "get_map_item_schema", "create_map_item",
        "bulk_create_map_items", "bulk_update_map_items", "upload_zipmap",
    ):
        assert name in tools
    for name in (
        "create_map_item_type", "update_map_item_type", "promote_map_item_schema",
        "create_map_item_schema", "update_map_item_schema",
        "move_document_to_folder", "create_list_type", "create_document_type",
        "add_lfd_package_tag",
    ):
        assert name not in tools


def test_write_routes_are_fixed_and_never_schema_definition_routes():
    # Audit every write, including helper paths reached indirectly from tools.
    tree = ast.parse(inspect.getsource(server))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"post", "patch", "delete", "upload", "request"}:
            continue
        assert node.args, ast.unparse(node)
        path = node.args[0]
        assert isinstance(path, (ast.Constant, ast.JoinedStr)), ast.unparse(node)
        literals = [part.value for part in ast.walk(path)
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        assert literals and literals[0].startswith("/api/"), ast.unparse(node)
        assert not any("schemas/" in s or "promote" in s for s in literals)
