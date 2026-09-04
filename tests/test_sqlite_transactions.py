import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hunter import (
    actions,
    applications,
    contacts,
    chat_history,
    companies,
    discovery,
    paths,
    repository,
    schema,
    sqlite_store,
    workflow,
)


class SQLiteTransactionFoundationTest(unittest.TestCase):
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

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_overlapping_application_edits_preserve_both_rows_and_fields(self):
        first = applications.create_application({"company": "Synthetic One", "role": "Lead"})
        second = applications.create_application({"company": "Synthetic Two", "role": "Lead"})
        original_read = repository.read_applications
        barrier = threading.Barrier(2)

        def synchronized_read():
            rows = original_read()
            barrier.wait(timeout=5)
            return rows

        for ids, updates in [
            ([first["id"], second["id"]], [{"notes": "First note"}, {"notes": "Second note"}]),
            ([first["id"], first["id"]], [{"notes": "Revised note"}, {"priority": "high"}]),
        ]:
            with patch.object(repository, "read_applications", synchronized_read), ThreadPoolExecutor(2) as executor:
                futures = [executor.submit(applications.update_application, row_id, update) for row_id, update in zip(ids, updates)]
                for future in futures:
                    future.result(timeout=5)
        saved = {row["id"]: row for row in original_read()}
        self.assertEqual(saved[first["id"]]["notes"], "Revised note")
        self.assertEqual(saved[first["id"]]["priority"], "high")
        self.assertEqual(saved[second["id"]]["notes"], "Second note")

    def test_stale_field_conflict_rolls_back_other_submitted_changes(self):
        first = applications.create_application({"company": "Synthetic One", "role": "Lead"})
        applications.create_application({"company": "Synthetic Two", "role": "Lead"})
        stale = repository.read_applications()
        applications.update_application(first["id"], {"notes": "Saved elsewhere"})
        stale[0]["notes"] = "Stale edit"
        stale[1]["notes"] = "Must roll back"
        revision = repository.data_revision()
        with self.assertRaisesRegex(ValueError, "changed while you were editing"):
            repository.write_applications(stale)
        self.assertEqual(repository.data_revision(), revision)
        self.assertEqual(repository.read_applications()[0]["notes"], "Saved elsewhere")
        self.assertEqual(repository.read_applications()[1]["notes"], "")

    def test_concurrent_contact_creation_allocates_distinct_persisted_ids(self):
        original_read = repository.read_contacts
        barrier = threading.Barrier(2)

        def synchronized_read():
            rows = original_read()
            barrier.wait(timeout=5)
            return rows

        with patch.object(repository, "read_contacts", synchronized_read), ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(contacts.upsert_contact, updates={"name": name}) for name in ["Synthetic One", "Synthetic Two"]]
            saved = [future.result(timeout=5) for future in futures]
        self.assertEqual(len({row["id"] for row in saved}), 2)
        self.assertEqual({row["name"] for row in original_read()}, {"Synthetic One", "Synthetic Two"})

    def test_stale_action_save_preserves_completed_action_and_new_rows(self):
        app = applications.create_application({"company": "Synthetic", "role": "Lead"})
        action_type = next(iter(workflow.active_action_type_ids()))
        first = actions.create_action(app["id"], {"title": "First action", "type": action_type})
        second = actions.create_action(app["id"], {"title": "Second action", "type": action_type})
        stale = repository.read_actions()
        actions.update_action_status(first["id"], "done")
        third = actions.create_action(app["id"], {"title": "Third action", "type": action_type})
        next(row for row in stale if row["id"] == second["id"])["title"] = "Edited action"
        repository.write_actions(stale)
        saved = {row["id"]: row for row in repository.read_actions()}
        self.assertEqual(saved[first["id"]]["status"], "done")
        self.assertIn(third["id"], saved)
        self.assertEqual(saved[second["id"]]["title"], "Edited action")
        self.assertEqual(repository.read_applications()[0]["next_action"], "Edited action")

    def test_application_and_related_action_identity_roll_back_together(self):
        app = applications.create_application({"company": "Synthetic", "role": "Lead"})
        action = actions.create_action(app["id"], {"title": "Follow up", "type": next(iter(workflow.active_action_type_ids()))})
        with patch.object(sqlite_store, "_sync_next_action", side_effect=RuntimeError("sync failed")):
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                applications.update_application(app["id"], {"role": "Changed", "stage": "closed", "outcome": "archived"})
        self.assertEqual(repository.read_applications()[0]["role"], "Lead")
        self.assertEqual(next(row for row in repository.read_actions() if row["id"] == action["id"])["role"], "Lead")

    def test_schema_21_migration_is_numbered_idempotent_and_preserves_revision(self):
        with sqlite_store.connect() as connection:
            connection.execute("DELETE FROM meta WHERE key = 'data_revision'")
            connection.execute(
                "UPDATE meta SET value = '20' WHERE key = 'schema_version'"
            )
            connection.execute("PRAGMA user_version = 20")

        sqlite_store.initialize()

        with sqlite_store.connect() as connection:
            self.assertEqual(sqlite_store.schema_version(connection), 21)
            self.assertEqual(sqlite_store.data_revision(connection), 0)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 21)

        repository.replace_companies_for_import([company_row("CO0001")])
        self.assertEqual(repository.data_revision(), 1)

        sqlite_store.initialize()

        self.assertEqual(repository.data_revision(), 1)
        self.assertEqual(repository.read_company("CO0001")["name"], "Company CO0001")

    def test_schema_migration_rolls_back_version_and_revision_together(self):
        with sqlite_store.connect() as connection:
            connection.execute("DELETE FROM meta WHERE key = 'data_revision'")
            connection.execute(
                "UPDATE meta SET value = '20' WHERE key = 'schema_version'"
            )
            connection.execute("PRAGMA user_version = 20")

        original_migration = sqlite_store.SCHEMA_MIGRATIONS[21]

        def failing_migration(connection):
            original_migration(connection)
            raise RuntimeError("migration rollback")

        sqlite_store.SCHEMA_MIGRATIONS[21] = failing_migration
        try:
            with self.assertRaisesRegex(RuntimeError, "migration rollback"):
                sqlite_store.initialize()
        finally:
            sqlite_store.SCHEMA_MIGRATIONS[21] = original_migration

        with sqlite_store.connect() as connection:
            self.assertEqual(sqlite_store.schema_version(connection), 20)
            self.assertEqual(sqlite_store.data_revision(connection), 0)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 20)

        sqlite_store.initialize()

    def test_failed_write_transaction_rolls_back_data_and_revision(self):
        repository.replace_discovery_candidates_for_import(
            [discovery_candidate_row("DC0001")]
        )
        before_revision = repository.data_revision()

        with self.assertRaisesRegex(RuntimeError, "audit rollback"):
            with sqlite_store.write_transaction() as connection:
                connection.execute(
                    "UPDATE discovery_candidates SET status = 'ignored' WHERE id = 'DC0001'"
                )
                raise RuntimeError("audit rollback")

        self.assertEqual(repository.data_revision(), before_revision)
        self.assertEqual(repository.read_discovery_candidate("DC0001")["status"], "new")

    def test_concurrent_updates_to_different_discovery_rows_both_survive(self):
        repository.replace_discovery_candidates_for_import(
            [
                discovery_candidate_row("DC0001"),
                discovery_candidate_row("DC0002"),
            ]
        )
        barrier = threading.Barrier(2)

        def update(candidate_id, status):
            barrier.wait()
            return repository.update_discovery_candidate_fields(
                candidate_id,
                {"status": status},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(update, "DC0001", "ignored")
            second = executor.submit(update, "DC0002", "pursued")
            first.result(timeout=5)
            second.result(timeout=5)

        self.assertEqual(repository.read_discovery_candidate("DC0001")["status"], "ignored")
        self.assertEqual(repository.read_discovery_candidate("DC0002")["status"], "pursued")

    def test_status_and_detail_updates_to_same_discovery_row_both_survive(self):
        repository.replace_discovery_candidates_for_import(
            [discovery_candidate_row("DC0001")]
        )
        barrier = threading.Barrier(2)

        def update_status():
            barrier.wait()
            return repository.update_discovery_candidate_statuses(
                ["DC0001"],
                "ignored",
                ignore_reason="wrong-role",
            )

        def update_details():
            barrier.wait()
            return repository.update_discovery_candidate_fields(
                "DC0001",
                {
                    "description_text": "Detailed role description",
                    "notes": "Keep this note verbatim.\nSecond line.",
                    "processing_status": "ready",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            status = executor.submit(update_status)
            details = executor.submit(update_details)
            status.result(timeout=5)
            details.result(timeout=5)

        candidate = repository.read_discovery_candidate("DC0001")
        self.assertEqual(candidate["status"], "ignored")
        self.assertEqual(candidate["ignore_reason"], "wrong-role")
        self.assertEqual(candidate["description_text"], "Detailed role description")
        self.assertEqual(candidate["notes"], "Keep this note verbatim.\nSecond line.")
        self.assertEqual(candidate["processing_status"], "ready")

    def test_concurrent_updates_to_different_company_candidates_both_survive(self):
        repository.replace_company_posting_candidates_for_import(
            [
                company_candidate_row("CP0001", "CO0001"),
                company_candidate_row("CP0002", "CO0002"),
            ]
        )
        barrier = threading.Barrier(2)

        def update(candidate_id, status):
            barrier.wait()
            return repository.update_company_posting_candidate_fields(
                candidate_id,
                {"status": status},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(update, "CP0001", "ignored")
            second = executor.submit(update, "CP0002", "pursued")
            first.result(timeout=5)
            second.result(timeout=5)

        self.assertEqual(
            repository.read_company_posting_candidate("CP0001")["status"],
            "ignored",
        )
        self.assertEqual(
            repository.read_company_posting_candidate("CP0002")["status"],
            "pursued",
        )

    def test_concurrent_updates_to_different_companies_both_survive(self):
        repository.replace_companies_for_import(
            [company_row("CO0001"), company_row("CO0002")]
        )
        barrier = threading.Barrier(2)

        def update(company_id, status):
            barrier.wait()
            return repository.update_company_fields(
                company_id,
                {"company_evaluation_status": status},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(update, "CO0001", "pending")
            second = executor.submit(update, "CO0002", "ready")
            first.result(timeout=5)
            second.result(timeout=5)

        self.assertEqual(
            repository.read_company("CO0001")["company_evaluation_status"],
            "pending",
        )
        self.assertEqual(
            repository.read_company("CO0002")["company_evaluation_status"],
            "ready",
        )

    def test_read_transaction_does_not_advance_revision(self):
        before = repository.data_revision()
        with sqlite_store.read_transaction() as connection:
            self.assertEqual(sqlite_store.data_revision(connection), before)
            connection.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()
        self.assertEqual(repository.data_revision(), before)

    def test_chat_and_workflow_mutations_advance_revision(self):
        before = repository.data_revision()

        chat_history.record_exchange("Question", "Answer")
        self.assertEqual(repository.data_revision(), before + 1)

        workflow.upsert_stage(
            {
                "id": "portfolio-review",
                "label": "Portfolio Review",
                "sort_order": "55",
            }
        )
        self.assertEqual(repository.data_revision(), before + 2)

    def test_stale_background_enrichment_preserves_concurrent_user_status(self):
        repository.replace_discovery_candidates_for_import(
            [discovery_candidate_row("DC0001")]
        )
        background_read = threading.Event()
        user_saved = threading.Event()

        def background_enrichment():
            stale = repository.read_discovery_candidate("DC0001")
            enriched = dict(stale)
            enriched.update(
                {
                    "description_text": "Background posting detail",
                    "description_excerpt": "Background posting detail",
                    "processing_status": "ready",
                    "status": "screened",
                    "warnings": "Screened from New: automated rule.",
                }
            )
            background_read.set()
            self.assertTrue(user_saved.wait(timeout=5))
            discovery.persist_discovery_candidate_changes([stale], [enriched])

        def user_decision():
            self.assertTrue(background_read.wait(timeout=5))
            repository.update_discovery_candidate_fields(
                "DC0001",
                {
                    "status": "ignored",
                    "ignore_reason": "wrong-role",
                    "ignore_reason_detail": "User decision",
                },
            )
            user_saved.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            background = executor.submit(background_enrichment)
            user = executor.submit(user_decision)
            background.result(timeout=10)
            user.result(timeout=10)

        saved = repository.read_discovery_candidate("DC0001")
        self.assertEqual(saved["status"], "ignored")
        self.assertEqual(saved["ignore_reason"], "wrong-role")
        self.assertEqual(saved["ignore_reason_detail"], "User decision")
        self.assertEqual(saved["description_text"], "Background posting detail")
        self.assertEqual(saved["processing_status"], "ready")
        self.assertEqual(saved["warnings"], "")

    def test_concurrent_company_scans_keep_both_candidate_sets(self):
        first_company = companies.upsert_company(
            "",
            {
                "name": "First Example",
                "careers_url": "https://first.example/careers",
            },
        )
        second_company = companies.upsert_company(
            "",
            {
                "name": "Second Example",
                "careers_url": "https://second.example/careers",
            },
        )
        barrier = threading.Barrier(2)

        def scan(company, host, slug, title):
            waited = False

            def fetch(_url):
                nonlocal waited
                if not waited:
                    waited = True
                    barrier.wait()
                return {
                    "status": 200,
                    "final_url": f"https://{host}/careers",
                    "html": f'<a href="/jobs/{slug}">{title}</a>',
                    "error": "",
                }

            return companies.check_company_postings(company["id"], fetcher=fetch)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                scan,
                first_company,
                "first.example",
                "first-role",
                "First Role",
            )
            second = executor.submit(
                scan,
                second_company,
                "second.example",
                "second-role",
                "Second Role",
            )
            first.result(timeout=10)
            second.result(timeout=10)

        candidates = repository.read_company_posting_candidates()
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len({row["id"] for row in candidates}), 2)
        self.assertEqual(
            {row["company_id"] for row in candidates},
            {first_company["id"], second_company["id"]},
        )

    def test_concurrent_same_company_scans_and_inserts_converge_without_overwrite(self):
        company = companies.upsert_company(
            "",
            {
                "name": "Shared Example",
                "careers_url": "https://shared.example/careers",
            },
        )
        barrier = threading.Barrier(2)

        def scan():
            waited = False

            def fetch(_url):
                nonlocal waited
                if not waited:
                    waited = True
                    barrier.wait()
                return {
                    "status": 200,
                    "final_url": "https://shared.example/careers",
                    "html": '<a href="/jobs/shared-role">Shared Role</a>',
                    "error": "",
                }

            return companies.check_company_postings(company["id"], fetcher=fetch)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_scan = executor.submit(scan)
            second_scan = executor.submit(scan)
            first_scan.result(timeout=10)
            second_scan.result(timeout=10)

        candidates = repository.read_company_posting_candidates()
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        repository.update_company_posting_candidate_fields(
            candidate["id"],
            {"status": "ignored", "notes": "User decision note"},
        )
        stale_insert = company_candidate_row("CP9999", company["id"])
        stale_insert["url"] = candidate["url"]
        stale_insert["title"] = "Stale scanner title"
        insert_barrier = threading.Barrier(2)

        def insert_stale():
            insert_barrier.wait()
            return repository.insert_company_posting_candidates([stale_insert])[0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_insert = executor.submit(insert_stale)
            second_insert = executor.submit(insert_stale)
            self.assertEqual(first_insert.result(timeout=5)["id"], candidate["id"])
            self.assertEqual(second_insert.result(timeout=5)["id"], candidate["id"])

        saved = repository.read_company_posting_candidate(candidate["id"])
        self.assertEqual(saved["status"], "ignored")
        self.assertEqual(saved["notes"], "User decision note")
        self.assertEqual(saved["title"], candidate["title"])

    def test_atomic_company_merge_rolls_back_every_change_on_failure(self):
        repository.replace_companies_for_import(
            [company_row("CO0001"), company_row("CO0002")]
        )
        repository.replace_company_posting_candidates_for_import(
            [company_candidate_row("CP0001", "CO0002")]
        )
        before_revision = repository.data_revision()
        original_helper = sqlite_store._merge_company_references

        def failing_helper(connection, keep_id, merge_id, company_name):
            original_helper(connection, keep_id, merge_id, company_name)
            raise RuntimeError("injected merge failure")

        with patch.object(
            sqlite_store,
            "_merge_company_references",
            side_effect=failing_helper,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected merge failure"):
                companies.merge_companies("CO0001", "CO0002")

        self.assertEqual(repository.data_revision(), before_revision)
        self.assertEqual(
            {row["id"] for row in repository.read_companies()},
            {"CO0001", "CO0002"},
        )
        self.assertEqual(
            repository.read_company_posting_candidate("CP0001")["company_id"],
            "CO0002",
        )
        self.assertEqual(repository.read_company("CO0001")["aliases"], "")

    def test_atomic_company_merge_never_exposes_partial_state_to_reader(self):
        repository.replace_companies_for_import(
            [company_row("CO0001"), company_row("CO0002")]
        )
        repository.replace_company_posting_candidates_for_import(
            [company_candidate_row("CP0001", "CO0002")]
        )
        references_moved = threading.Event()
        allow_commit = threading.Event()
        original_helper = sqlite_store._merge_company_references

        def paused_helper(connection, keep_id, merge_id, company_name):
            original_helper(connection, keep_id, merge_id, company_name)
            references_moved.set()
            self.assertTrue(allow_commit.wait(timeout=5))

        with patch.object(
            sqlite_store,
            "_merge_company_references",
            side_effect=paused_helper,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                merge = executor.submit(companies.merge_companies, "CO0001", "CO0002")
                self.assertTrue(references_moved.wait(timeout=5))
                visible_companies = repository.read_companies()
                visible_candidate = repository.read_company_posting_candidate("CP0001")
                allow_commit.set()
                merge.result(timeout=5)

        self.assertEqual(
            {row["id"] for row in visible_companies},
            {"CO0001", "CO0002"},
        )
        self.assertEqual(visible_candidate["company_id"], "CO0002")
        self.assertEqual(
            {row["id"] for row in repository.read_companies()},
            {"CO0001"},
        )
        self.assertEqual(
            repository.read_company_posting_candidate("CP0001")["company_id"],
            "CO0001",
        )


def company_row(company_id):
    row = {field: "" for field in schema.COMPANY_FIELDS}
    row.update(
        {
            "id": company_id,
            "name": f"Company {company_id}",
            "interest_status": "neutral",
            "tracking_status": "tracked",
        }
    )
    return row


def discovery_candidate_row(candidate_id):
    row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
    row.update(
        {
            "id": candidate_id,
            "title": f"Role {candidate_id}",
            "url": f"https://example.com/jobs/{candidate_id.lower()}",
            "status": "new",
            "processing_status": "needs-details",
        }
    )
    return row


def company_candidate_row(candidate_id, company_id):
    row = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
    row.update(
        {
            "id": candidate_id,
            "company_id": company_id,
            "title": f"Role {candidate_id}",
            "url": f"https://example.com/jobs/{candidate_id.lower()}",
            "status": "new",
        }
    )
    return row


if __name__ == "__main__":
    unittest.main()
