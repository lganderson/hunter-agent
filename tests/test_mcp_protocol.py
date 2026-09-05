import io
import json
import unittest
from unittest.mock import patch

from hunter import mcp_server


class McpProtocolTest(unittest.TestCase):
    def run_server(self, messages):
        input_stream = io.StringIO("\n".join(messages) + "\n")
        output_stream = io.StringIO()
        with patch("hunter.mcp_server.sqlite_store.initialize"):
            mcp_server.serve(input_stream=input_stream, output_stream=output_stream)
        return [json.loads(line) for line in output_stream.getvalue().splitlines()]

    def test_stdio_lifecycle_handles_parse_initialize_list_and_unknown_method(self):
        responses = self.run_server(
            [
                "not-json",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}}),
            ]
        )

        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(responses[1]["result"]["serverInfo"], mcp_server.SERVER_INFO)
        self.assertEqual(len(responses[2]["result"]["tools"]), len(mcp_server.TOOLS))
        self.assertEqual(responses[3]["error"]["code"], -32601)

    def test_every_tool_emits_complete_behavior_annotations(self):
        listed = mcp_server.list_tools_result()["tools"]

        self.assertEqual({tool["name"] for tool in listed}, set(mcp_server.TOOLS))
        for tool in listed:
            annotations = tool["annotations"]
            self.assertTrue(annotations["title"])
            self.assertIsInstance(annotations["readOnlyHint"], bool)
            self.assertIsInstance(annotations["destructiveHint"], bool)
            self.assertIsInstance(annotations["idempotentHint"], bool)
            self.assertIsInstance(annotations["openWorldHint"], bool)
            if annotations["readOnlyHint"]:
                self.assertFalse(annotations["destructiveHint"])
                self.assertFalse(annotations["idempotentHint"])

    def test_annotations_distinguish_local_reads_mutations_and_external_work(self):
        local_read = mcp_server.tool_annotations("hunter_list_postings")
        local_mutation = mcp_server.tool_annotations("hunter_update_application")
        additive_mutation = mcp_server.tool_annotations("hunter_create_action")
        external_mutation = mcp_server.tool_annotations("hunter_run_discovery_search")

        self.assertEqual(
            (local_read["readOnlyHint"], local_read["destructiveHint"], local_read["openWorldHint"]),
            (True, False, False),
        )
        self.assertEqual(
            (
                local_mutation["readOnlyHint"],
                local_mutation["destructiveHint"],
                local_mutation["idempotentHint"],
            ),
            (False, True, True),
        )
        self.assertEqual(
            (additive_mutation["destructiveHint"], additive_mutation["idempotentHint"]),
            (False, False),
        )
        self.assertEqual(
            (external_mutation["readOnlyHint"], external_mutation["openWorldHint"]),
            (False, True),
        )


if __name__ == "__main__":
    unittest.main()


class InvalidShapeTest(unittest.TestCase):
    def test_malformed_shape_does_not_stop_following_requests(self):
        import io
        from hunter import mcp_server
        output = io.StringIO()
        mcp_server.serve(io.StringIO('[]\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertIn("tools", responses[1]["result"])

    def test_tool_arguments_are_validated_before_dispatch(self):
        from hunter import mcp_server
        with self.assertRaisesRegex(ValueError, "arguments"):
            mcp_server.call_named_tool("hunter_list_postings", ["invalid"])
