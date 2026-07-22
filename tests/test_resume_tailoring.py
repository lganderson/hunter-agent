import base64
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from hunter import paths, repository, resumes, schema, settings, sqlite_store


class ResumeTailoringTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in [
                "ROOT",
                "DATA_DIR",
                "SETTINGS_FILE",
                "SQLITE_DB",
                "APPLICATIONS",
                "CONTACTS",
                "INTERVIEWS",
                "ACTIONS",
            ]
        }
        paths.ROOT = self.root
        paths.DATA_DIR = self.root / "data"
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"
        paths.APPLICATIONS = paths.DATA_DIR / "applications.csv"
        paths.CONTACTS = paths.DATA_DIR / "contacts.csv"
        paths.INTERVIEWS = paths.DATA_DIR / "interviews.csv"
        paths.ACTIONS = paths.DATA_DIR / "actions.csv"
        sqlite_store.initialize()
        application = {field: "" for field in schema.APPLICATION_FIELDS}
        application.update({
            "id": "A0001",
            "company": "Example",
            "role": "Senior Platform Program Manager",
            "stage": "resume-tailoring",
            "priority": "high",
        })
        repository.write_applications([application])
        repository.write_posting_note(
            "A0001",
            "postings/A0001.md",
            "Seeking a cross-functional program leader for platform launches and developer workflows.",
        )

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def make_docx(self):
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Alex Example</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Led platform launches across </w:t></w:r>'
            '<w:r><w:rPr><w:i/></w:rPr><w:t>product and engineering teams.</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Improved developer workflows while coordinating release readiness.</w:t></w:r></w:p>'
            '<w:sectPr/></w:body></w:document>'
        )
        source = self.root / "resume.docx"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        return source

    def upload_docx(self):
        source = self.make_docx()
        settings.save_resume_upload("Alex Resume.docx", base64.b64encode(source.read_bytes()).decode("ascii"))
        return settings.resume_dir_path() / "current.docx"

    def test_apply_changes_preserves_runs_and_original(self):
        source = self.make_docx()
        destination = self.root / "tailored.docx"
        old_source = source.read_bytes()

        resumes.apply_changes_to_docx(source, destination, [{
            "old_text": "Led platform launches across product and engineering teams.",
            "new_text": "Led cross-functional platform launches across product and engineering teams.",
        }])

        self.assertEqual(source.read_bytes(), old_source)
        with zipfile.ZipFile(destination) as archive:
            updated_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("Led cross-functional platform launches", updated_xml)
        self.assertIn("<w:i", updated_xml)
        self.assertIn("<w:b", updated_xml)
        self.assertRegex(
            updated_xml,
            r'<w:rPr><w:i/></w:rPr><w:t(?: xml:space="preserve")?>product and engineering teams\.</w:t>',
        )

    def test_propose_changes_uses_structured_output_and_rejects_new_numbers(self):
        self.upload_docx()
        captured = {}
        response_plan = {
            "summary": "Use the posting's cross-functional platform language.",
            "matched_keywords": ["platform launches"],
            "missing_keywords": ["cross-functional"],
            "changes": [
                {
                    "old_text": "Led platform launches across product and engineering teams.",
                    "new_text": "Led cross-functional platform launches across product and engineering teams.",
                    "reason": "Matches the posting's supported phrasing.",
                    "keywords": ["cross-functional", "platform launches"],
                },
                {
                    "old_text": "Improved developer workflows while coordinating release readiness.",
                    "new_text": "Improved 99 developer workflows while coordinating release readiness.",
                    "reason": "Unsafe invented number.",
                    "keywords": ["developer workflows"],
                },
            ],
        }

        def fake_request(_url, _token, payload):
            captured["payload"] = payload
            return {"output_text": json.dumps(response_plan), "output": []}

        with patch("hunter.resumes.agent._settings", return_value={"token": "token", "model": "gpt-5.5", "api_base": "https://example.test"}), \
             patch("hunter.resumes.agent._request_json", side_effect=fake_request), \
             patch("hunter.resumes.agent.log_usage"):
            plan = resumes.propose_changes("A0001", "Keep the changes conservative.")

        self.assertEqual(len(plan["changes"]), 1)
        self.assertIn("cross-functional", plan["changes"][0]["new_text"])
        self.assertEqual(captured["payload"]["text"]["format"]["type"], "json_schema")
        self.assertTrue(captured["payload"]["text"]["format"]["strict"])
        self.assertFalse(captured["payload"]["store"])

    def test_create_version_writes_docx_pdf_metadata_and_application_link(self):
        source = self.upload_docx()
        change = {
            "old_text": "Led platform launches across product and engineering teams.",
            "new_text": "Led cross-functional platform launches across product and engineering teams.",
            "reason": "Keyword alignment.",
            "keywords": ["cross-functional"],
        }

        def fake_pdf(docx_path):
            pdf_path = docx_path.with_suffix(".pdf")
            pdf_path.write_bytes(b"%PDF-1.4 synthetic test")
            return pdf_path, ""

        with patch("hunter.resumes.convert_to_pdf", side_effect=fake_pdf):
            version = resumes.create_version(
                "A0001",
                "Keep the wording close to the original.",
                resumes._source_hash(source),
                [change],
            )

        self.assertTrue(version["docx_available"])
        self.assertTrue(version["pdf_available"])
        self.assertEqual(len(version["changes"]), 1)
        self.assertEqual(resumes.version_download(version["id"], "docx").suffix, ".docx")
        self.assertEqual(resumes.version_download(version["id"], "pdf").suffix, ".pdf")
        application = repository.read_applications()[0]
        self.assertEqual(application["resume_version"], version["id"])

    def test_replacing_base_resume_preserves_saved_versions(self):
        self.upload_docx()
        version_dir = settings.resume_dir_path() / "versions" / "A0001" / "existing"
        version_dir.mkdir(parents=True)
        saved = version_dir / "saved.docx"
        saved.write_bytes(b"saved version")

        replacement = self.make_docx()
        settings.save_resume_upload("Replacement.docx", base64.b64encode(replacement.read_bytes()).decode("ascii"))

        self.assertEqual(saved.read_bytes(), b"saved version")

    def test_non_docx_source_is_rejected_for_format_preserving_tailoring(self):
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Led platform launches across product and engineering teams.").decode("ascii"),
        )

        with self.assertRaisesRegex(ValueError, "requires a DOCX"):
            resumes.propose_changes("A0001", "")


if __name__ == "__main__":
    unittest.main()
