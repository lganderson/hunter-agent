import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter import agent, mcp_server, paths, settings


class ResumeSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in [
                "ROOT",
                "DATA_DIR",
                "SETTINGS_FILE",
            ]
        }
        paths.ROOT = self.root
        paths.DATA_DIR = self.root / "data"
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_resume_upload_extracts_text_and_preserves_settings(self):
        settings.save_settings("openai", "gpt-5.5", "", "secret")
        encoded = base64.b64encode(b"Alex Example\nTechnical Program Manager\nAI platform work").decode("ascii")

        status = settings.save_resume_upload("Example Resume.txt", encoded)

        self.assertTrue(status["resume"]["configured"])
        self.assertEqual(status["resume"]["filename"], "Example Resume.txt")
        self.assertIn("Technical Program Manager", status["resume"]["preview"])
        self.assertEqual(status["resume"]["preview_char_count"], len(status["resume"]["preview"]))
        self.assertFalse(status["resume"]["preview_truncated"])
        self.assertIn("AI platform work", settings.resume_context())
        self.assertTrue((paths.DATA_DIR / "resume" / "current.txt").exists())
        self.assertTrue(settings.settings_status()["token_configured"])

    def test_search_goals_default_and_save_roundtrip(self):
        status = settings.settings_status()

        self.assertIn("Main thesis", status["search_goals"])
        self.assertIn("interactive customer experiences", status["search_goals"])
        self.assertIn("role_terms", status["fit_signals"])
        self.assertIn("technical program manager", status["fit_signals"]["role_terms"])

        next_status = settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nDeveloper productivity workflows",
            fit_signals={
                "role_terms": "game systems producer | 50",
                "domain_terms": "uefn | 20",
                "search_terms": "game systems producer",
            },
        )

        self.assertEqual(next_status["search_goals"], "Primary:\nDeveloper productivity workflows")
        self.assertEqual(next_status["fit_signals"]["role_terms"], "game systems producer | 50")
        self.assertEqual(next_status["fit_signals"]["search_terms"], "game systems producer")
        self.assertIn("Developer productivity workflows", settings.search_goals_context())
        self.assertIn("game systems producer", settings.search_goals_context())

    def test_mcp_settings_tools_update_search_goals_and_merge_fit_signals(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "secret",
            search_goals="Primary:\nOld goals",
            fit_signals={
                "role_terms": "technical program manager | 42",
                "domain_terms": "developer tools | 18",
                "search_terms": "technical program manager",
            },
        )

        result = mcp_server.call_named_tool(
            "hunter_update_settings",
            {
                "search_goals": "Primary:\nDeveloper platforms",
                "fit_signals": {"search_terms": "technical product manager"},
            },
        )
        payload = json.loads(result["content"][0]["text"])
        fetched = json.loads(mcp_server.call_named_tool("hunter_get_settings", {})["content"][0]["text"])

        self.assertIn("hunter_update_settings", mcp_server.TOOLS)
        self.assertIn("hunter_get_settings", mcp_server.TOOLS)
        self.assertEqual(payload["search_goals"], "Primary:\nDeveloper platforms")
        self.assertEqual(fetched["fit_signals"]["search_terms"], "technical product manager")
        self.assertEqual(fetched["fit_signals"]["role_terms"], "technical program manager | 42")
        self.assertEqual(fetched["fit_signals"]["domain_terms"], "developer tools | 18")
        self.assertTrue(fetched["token_configured"])

    def test_fit_context_combines_resume_and_search_goals(self):
        encoded = base64.b64encode(b"Senior Technical Program Manager").decode("ascii")
        settings.save_resume_upload("resume.txt", encoded)
        settings.save_settings("openai", "gpt-5.5", "", "", search_goals="Primary:\nGame developer tools")

        context = settings.fit_context()

        self.assertIn("Senior Technical Program Manager", context)
        self.assertIn("Game developer tools", context)

    def test_fit_profile_context_is_compact_and_points_to_full_resume_tool(self):
        resume = (
            "Senior Technical Product and Program Manager with AI platform, "
            "developer tools, roadmap, launch, and cross-functional execution. "
        ) * 80
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode("ascii"))
        settings.save_settings("openai", "gpt-5.5", "", "", search_goals="Primary:\nGame developer tools")

        profile = settings.fit_profile_context()

        self.assertLess(len(profile), len(settings.resume_context()))
        self.assertIn("Likely roles", profile)
        self.assertIn("technical product manager", profile)
        self.assertIn("developer tools", profile)
        self.assertIn("hunter_get_resume_text", profile)

    def test_resume_status_reports_truncated_preview_and_full_text_payload(self):
        text = "A" * 900
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")

        status = settings.save_resume_upload("resume.txt", encoded)
        payload = settings.resume_text_payload()

        self.assertEqual(status["resume"]["preview_char_count"], 800)
        self.assertTrue(status["resume"]["preview_truncated"])
        self.assertEqual(payload["text"], text)
        self.assertEqual(payload["text_char_count"], 900)

    def test_docx_resume_upload_extracts_document_text(self):
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Product leader</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>AI and developer tools</w:t></w:r></w:p></w:body></w:document>"
        )
        docx_path = self.root / "resume.docx"
        import zipfile
        with zipfile.ZipFile(docx_path, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        encoded = base64.b64encode(docx_path.read_bytes()).decode("ascii")

        status = settings.save_resume_upload("resume.docx", encoded)

        self.assertTrue(status["resume"]["configured"])
        self.assertIn("Product leader", status["resume"]["preview"])
        self.assertIn("AI and developer tools", settings.resume_context())

    def test_pdf_resume_upload_uses_optional_package_path(self):
        package_root = self.root / "packages"
        pdfplumber_package = package_root / "pdfplumber"
        pdfplumber_package.mkdir(parents=True)
        (pdfplumber_package / "__init__.py").write_text(
            "\n".join([
                "class Page:",
                "    def extract_text(self):",
                "        return 'PDF resume text from optional extractor'",
                "class Pdf:",
                "    pages = [Page()]",
                "    def __enter__(self):",
                "        return self",
                "    def __exit__(self, *args):",
                "        return False",
                "def open(_stream):",
                "    return Pdf()",
            ]),
            encoding="utf-8",
        )
        sys.modules.pop("pdfplumber", None)
        encoded = base64.b64encode(b"%PDF-1.4 fake fixture").decode("ascii")

        with patch("hunter.settings.optional_python_package_paths", return_value=[package_root]):
            status = settings.save_resume_upload("resume.pdf", encoded)

        sys.modules.pop("pdfplumber", None)
        self.assertTrue(status["resume"]["configured"])
        self.assertEqual(status["resume"]["extraction_status"], "ok")
        self.assertIn("PDF resume text", status["resume"]["preview"])

    def test_resume_delete_clears_context_and_files(self):
        encoded = base64.b64encode(b"Resume text").decode("ascii")
        settings.save_resume_upload("resume.txt", encoded)

        status = settings.delete_resume()

        self.assertFalse(status["resume"]["configured"])
        self.assertEqual(settings.resume_context(), "")
        self.assertFalse((paths.DATA_DIR / "resume").exists())

    def test_chat_includes_resume_context_in_instructions(self):
        captured = {}

        def fake_request(_url, _token, payload):
            captured["instructions"] = payload["instructions"]
            captured["payload"] = payload
            return {
                "output_text": "ok",
                "output": [],
                "usage": {
                    "input_tokens": 1200,
                    "input_tokens_details": {"cached_tokens": 896},
                    "output_tokens": 20,
                    "output_tokens_details": {"reasoning_tokens": 4},
                    "total_tokens": 1220,
                },
            }

        with patch("hunter.agent._settings", return_value={"token": "token", "model": "gpt-5.5", "api_base": "https://example.test"}), \
             patch("hunter.agent.settings_store.fit_profile_context", return_value="Compact fit profile:\nAI TPM\nGame developer tools"):
            with patch("hunter.agent._request_json", side_effect=fake_request):
                result = agent.chat(
                    [{"role": "user", "content": "Which postings fit me?"}],
                    {
                        "route": "posting-detail",
                        "pathname": "/postings/A0001",
                        "entity_type": "posting",
                        "entity_id": "A0001",
                        "label": "Example · Product Manager",
                        "query": {"stage": "posting-review"},
                    },
                )

        self.assertEqual(result["message"], "ok")
        self.assertIn("AI TPM", captured["instructions"])
        self.assertIn("Game developer tools", captured["instructions"])
        self.assertIn("evaluating job fit", captured["instructions"])
        self.assertIn("calm, candid job-search chief of staff", captured["instructions"])
        self.assertIn('\"entity_id\": \"A0001\"', captured["instructions"])
        self.assertIn("Verify record details through Hunter tools", captured["instructions"])
        self.assertIn("hunter_get_resume_text", [tool["name"] for tool in captured["payload"]["tools"]])
        self.assertEqual(captured["payload"]["prompt_cache_key"], agent.PROMPT_CACHE_KEY)
        self.assertEqual(captured["payload"]["prompt_cache_retention"], "24h")

        usage_log = paths.DATA_DIR / agent.USAGE_LOG_FILE
        self.assertTrue(usage_log.exists())
        usage = json.loads(usage_log.read_text(encoding="utf-8").strip())
        self.assertEqual(usage["input_tokens"], 1200)
        self.assertEqual(usage["cached_input_tokens"], 896)
        self.assertEqual(usage["uncached_input_tokens"], 304)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["reasoning_tokens"], 4)

    def test_mutating_tool_receipts_are_human_readable(self):
        receipt = agent._tool_receipt(
            "hunter_create_action",
            {
                "application_id": "A0001",
                "values": {"title": "Tailor resume"},
            },
        )

        self.assertEqual(receipt, 'Created "Tailor resume" for A0001.')


if __name__ == "__main__":
    unittest.main()
