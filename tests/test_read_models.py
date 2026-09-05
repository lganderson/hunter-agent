import json
import time
import unittest
from unittest.mock import patch

from hunter import read_models


def company(company_id, *, interest_status="interested", tracking_status="active"):
    return {
        "id": company_id,
        "name": f"Company {company_id}",
        "aliases": "",
        "interest_status": interest_status,
        "tracking_status": tracking_status,
        "industry": "Software",
        "company_size": "201-500",
        "website": f"https://{company_id.lower()}.example",
        "careers_url": f"https://{company_id.lower()}.example/jobs",
        "notes": "private company note " * 100,
        "research_evidence": "large evidence " * 100,
    }


def company_candidate(candidate_id, company_id, *, fit_score=70, status="new", title=None):
    return {
        "id": candidate_id,
        "company_id": company_id,
        "title": title or f"Platform Lead {candidate_id}",
        "url": f"https://{company_id.lower()}.example/jobs/{candidate_id.lower()}",
        "location": "Remote, United States",
        "work_mode": "remote",
        "category": "product",
        "source_platform": "official_web_search",
        "source_job_id": candidate_id,
        "description_excerpt": "Detailed company role description. " * 80,
        "normalization_warnings": "internal warning",
        "scan_state": "current",
        "status": status,
        "fit_score": str(fit_score),
        "fit_summary": "Strong private fit evidence.",
        "notes": "private candidate note",
    }


def discovery_candidate(candidate_id, company_id, *, fit_score=75, status="new", search_ids=None):
    search_ids = search_ids or ["DS0001"]
    return {
        "id": candidate_id,
        "search_id": search_ids[0],
        "search_ids_json": json.dumps(search_ids),
        "company_id": company_id,
        "title": f"Technical Program Lead {candidate_id}",
        "url": f"https://network.example/jobs/{candidate_id.lower()}",
        "canonical_url": f"https://network.example/jobs/{candidate_id.lower()}",
        "location": "Chicago, IL",
        "work_mode": "hybrid",
        "source_platform": "linkedin",
        "captured_at": "2026-09-01T10:00:00",
        "last_seen_at": "2026-09-01T10:00:00",
        "status": status,
        "processing_status": "ready",
        "fit_score": str(fit_score),
        "fit_summary": "Strong private discovery evidence.",
        "description_text": "Full private posting description. " * 150,
        "description_excerpt": "Discovery role excerpt. " * 80,
        "warnings": "private warning",
        "source_urls_json": json.dumps([f"https://network.example/jobs/{candidate_id.lower()}"]),
        "freshness_status": "confirmed-open",
        "notes": "private discovery note",
    }


def searches():
    return [
        {"id": "DS0001", "name": "Primary", "lanes": [], "keywords": "program"},
        {"id": "DS0002", "name": "Secondary", "lanes": [], "keywords": "product"},
    ]


def shell_companies(shell):
    table = shell["companies"]
    return [dict(zip(table["fields"], values)) for values in table["rows"]]


def basic_context(revision=17):
    companies = [
        company("CO0001"),
        company("CO0002", tracking_status="watch"),
        company("CO0003", interest_status="archived"),
    ]
    return read_models.CandidateReadContext.from_rows(
        companies=companies,
        applications=[{"id": "AP0001", "company_id": "CO0001", "company": "Company CO0001", "role": "Existing"}],
        searches=searches(),
        company_candidates=[
            company_candidate("CP0001", "CO0001", fit_score=91),
            company_candidate("CP0002", "CO0002", fit_score=65),
            company_candidate("CP0003", "CO0003", fit_score=99),
        ],
        discovery_candidates=[
            discovery_candidate("DC0001", "CO0001", fit_score=88, search_ids=["DS0001", "DS0002"]),
            discovery_candidate("DC0002", "CO0002", fit_score=55, search_ids=["DS0002"]),
            discovery_candidate("DC0003", "CO0003", fit_score=97),
        ],
        revision=revision,
    )


class CandidateReadModelTest(unittest.TestCase):
    def test_discovery_filters_facets_and_sort_cover_rows_beyond_first_page(self):
        first = company("CO0001")
        last = {**company("CO0002"), "industry": "Health, Wellness", "company_size": "1,001-5,000"}
        rows = [discovery_candidate(f"DC{index:04d}", f"CO{index + 10:04d}", fit_score=90) for index in range(59)]
        other_companies = [company(f"CO{index + 10:04d}") for index in range(59)]
        target = discovery_candidate("DC0059", "CO0002", fit_score=1)
        target.update(title="AAA global sort target", source_platform="adzuna")
        rows.append(target)
        context = read_models.CandidateReadContext.from_rows(
            companies=[first, last, *other_companies], applications=[], searches=searches(),
            company_candidates=[], discovery_candidates=rows, revision=1,
        )
        page = read_models.discovery_candidate_page(context=context)
        self.assertEqual(len(page["items"]), 50)
        self.assertNotIn(target["id"], [row["id"] for row in page["items"]])
        self.assertIn("CO0002", [facet["value"] for facet in page["facets"]["companies"]])
        self.assertIn("1,001-5,000", [facet["value"] for facet in page["facets"]["sizes"]])
        for query in [
            {"company_id": ["CO0002"]}, {"industry": ["Health, Wellness"]},
            {"size": ["1,001-5,000"]}, {"source": ["Jobs by Adzuna"]},
            {"search": ["AAA global sort target"]},
        ]:
            with self.subTest(query=query):
                filtered = read_models.discovery_candidate_page(query, context=context)
                self.assertEqual([row["id"] for row in filtered["items"]], [target["id"]])
                self.assertEqual(filtered["counts"]["filtered"], 1)
                self.assertFalse(filtered["page"]["has_more"])
        sorted_page = read_models.discovery_candidate_page({"sort": ["candidate"], "direction": ["asc"]}, context=context)
        self.assertEqual(sorted_page["items"][0]["id"], target["id"])

    def test_compact_detail_routes_return_full_rows_with_stable_revision(self):
        cases = [
            (
                "application",
                read_models.build_application_detail,
                "read_applications",
                "AP0001",
                {"id": "AP0001", "company": "Example", "notes": "private application notes"},
            ),
            (
                "action",
                read_models.build_action_detail,
                "read_actions",
                "AC0001",
                {
                    "id": "AC0001",
                    "title": "Follow up",
                    "description": "full action description",
                    "related_url": "https://private.example/action",
                    "source": "manual",
                    "notes": "private action notes",
                },
            ),
            (
                "contact",
                read_models.build_contact_detail,
                "read_contacts",
                "CT0001",
                {"id": "CT0001", "name": "Person", "notes": "private contact notes"},
            ),
        ]
        for resource, builder, reader_name, entity_id, row in cases:
            with self.subTest(resource=resource):
                with (
                    patch.object(read_models.repository, "data_revision", side_effect=[41, 41]),
                    patch.object(read_models.repository, reader_name, return_value=[row]) as reader,
                ):
                    result = builder({"id": [entity_id.lower()]})

                self.assertEqual(result["api_version"], read_models.API_VERSION)
                self.assertEqual(result["resource"], resource)
                self.assertEqual(result["revision"], 41)
                self.assertEqual(result["item"], row)
                self.assertTrue(result["audit"]["stable_revision"])
                self.assertTrue(result["audit"]["includes_omitted_fields"])
                reader.assert_called_once_with()

    def test_company_detail_includes_full_career_source_or_null(self):
        company_row = {
            "id": "CO0001",
            "name": "Example",
            "notes": "private company notes",
            "company_metadata_suggestions_json": '[{"field":"industry"}]',
        }
        career_source = {
            "company_id": "CO0001",
            "url": "https://example.test/careers",
            "evidence": "full private career evidence",
            "notes": "private source note",
        }
        with (
            patch.object(read_models.repository, "data_revision", side_effect=[51, 51]),
            patch.object(read_models.repository, "read_companies", return_value=[company_row]),
            patch.object(
                read_models.repository,
                "read_company_career_sources",
                return_value=[career_source],
            ),
        ):
            result = read_models.build_company_detail({"id": ["CO0001"]})

        self.assertEqual(result["item"]["notes"], "private company notes")
        self.assertEqual(
            result["item"]["company_metadata_suggestions_json"],
            '[{"field":"industry"}]',
        )
        self.assertEqual(result["item"]["company_career_source"], career_source)

        with (
            patch.object(read_models.repository, "data_revision", side_effect=[52, 52]),
            patch.object(read_models.repository, "read_companies", return_value=[company_row]),
            patch.object(read_models.repository, "read_company_career_sources", return_value=[]),
        ):
            without_source = read_models.build_company_detail({"id": ["CO0001"]})
        self.assertIsNone(without_source["item"]["company_career_source"])

    def test_compact_detail_errors_are_structured_and_revision_safe(self):
        with self.assertRaises(read_models.ReadModelError) as missing:
            read_models.build_application_detail({})
        self.assertEqual(missing.exception.status, 400)

        with (
            patch.object(read_models.repository, "data_revision", side_effect=[61, 61]),
            patch.object(read_models.repository, "read_contacts", return_value=[]),
        ):
            with self.assertRaises(read_models.ReadModelError) as unknown:
                read_models.build_contact_detail({"id": ["CT9999"]})
        self.assertEqual(unknown.exception.status, 404)

        with (
            patch.object(read_models.repository, "data_revision", side_effect=[71, 72, 73, 74]),
            patch.object(
                read_models.repository,
                "read_actions",
                return_value=[{"id": "AC0001", "notes": "private"}],
            ) as reader,
        ):
            with self.assertRaises(read_models.ReadModelError) as unstable:
                read_models.build_action_detail({"id": ["AC0001"]})
        self.assertEqual(unstable.exception.status, 409)
        self.assertEqual(reader.call_count, 2)

    def test_compact_details_stay_within_120_kib_budget(self):
        large_note = "x" * (100 * 1024)
        cases = [
            (read_models.build_application_detail, "read_applications", "AP0001"),
            (read_models.build_action_detail, "read_actions", "AC0001"),
            (read_models.build_contact_detail, "read_contacts", "CT0001"),
        ]
        for builder, reader_name, entity_id in cases:
            with self.subTest(entity_id=entity_id):
                with (
                    patch.object(read_models.repository, "data_revision", side_effect=[81, 81]),
                    patch.object(
                        read_models.repository,
                        reader_name,
                        return_value=[{"id": entity_id, "notes": large_note}],
                    ),
                ):
                    result = builder({"id": [entity_id]})
                size = len(json.dumps(result, separators=(",", ":")).encode("utf-8"))
                self.assertLessEqual(size, 120 * 1024)

        with (
            patch.object(read_models.repository, "data_revision", side_effect=[82, 82]),
            patch.object(
                read_models.repository,
                "read_companies",
                return_value=[{"id": "CO0001", "notes": large_note[:50 * 1024]}],
            ),
            patch.object(
                read_models.repository,
                "read_company_career_sources",
                return_value=[{"company_id": "CO0001", "evidence": large_note[:50 * 1024]}],
            ),
        ):
            company_result = read_models.build_company_detail({"id": ["CO0001"]})
        company_size = len(json.dumps(company_result, separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(company_size, 120 * 1024)

    def test_compact_detail_routes_are_registered(self):
        self.assertEqual(
            {
                path for path in read_models.READ_MODEL_GET_ROUTES
                if path in {
                    "/api/applications/detail",
                    "/api/actions/detail",
                    "/api/contacts/detail",
                    "/api/companies/detail",
                }
            },
            {
                "/api/applications/detail",
                "/api/actions/detail",
                "/api/contacts/detail",
                "/api/companies/detail",
            },
        )

    def test_shell_is_revisioned_and_excludes_candidate_pools_and_private_notes(self):
        shell = read_models.app_shell_from_rows(
            revision=19,
            applications=[{"id": "AP0001", "company": "Example", "role": "Lead", "notes": "private"}],
            actions=[{
                "id": "AC0001",
                "title": "Follow up",
                "status": "open",
                "description": "private heavy action description",
                "related_url": "https://private.example/action",
                "source": "private-source",
                "notes": "private",
            }],
            workflow_payload={"stages": []},
            contacts=[{"id": "CT0001", "name": "Person", "notes": "private"}],
            application_contacts=[],
            companies=[{
                **company("CO0001"),
                "company_metadata_suggestions_json": json.dumps([{"field": "industry"}, {"field": "size"}]),
                "company_fit_summary": "Fit evidence " * 100,
            }],
            company_contacts=[],
            company_career_sources=[{"company_id": "CO0001", "url": "https://example.test", "evidence": "private", "notes": "private"}],
            discovery_searches=searches(),
            company_candidates=[company_candidate("CP0001", "CO0001")],
            discovery_candidates=[discovery_candidate("DC0001", "CO0001")],
            dismissed_suggestion_ids={"SG0001"},
        )

        self.assertEqual(shell["revision"], 19)
        self.assertNotIn("company_posting_candidates", shell)
        self.assertNotIn("discovery_candidates", shell)
        self.assertNotIn("notes", shell["applications"][0])
        self.assertNotIn("notes", shell["actions"][0])
        self.assertNotIn("description", shell["actions"][0])
        self.assertNotIn("related_url", shell["actions"][0])
        self.assertNotIn("source", shell["actions"][0])
        self.assertNotIn("notes", shell["contacts"][0])
        shell_company = shell_companies(shell)[0]
        self.assertNotIn("notes", shell_company)
        self.assertNotIn("company_metadata_suggestions_json", shell_company)
        self.assertEqual(shell_company["company_metadata_suggestion_count"], 2)
        self.assertLessEqual(
            len(shell_company["company_fit_summary"]),
            read_models.COMPANY_FIT_SUMMARY_LIMIT,
        )
        self.assertTrue(shell_company["company_fit_summary"].startswith("Fit evidence"))
        self.assertNotIn("evidence", shell["company_career_sources"][0])
        self.assertEqual(shell["candidate_counts"], {"company": 1, "discovery": 1})
        self.assertEqual(shell["company_merge_suggestions"], [])
        self.assertEqual(shell["discovery_preference_suggestions"], [])

    def test_shell_preserves_derived_company_counts_and_recommendations(self):
        companies = [
            {
                **company("CO0001"),
                "tracking_status": "discovered",
                "interest_status": "neutral",
            },
            {
                **company("CO0002"),
                "tracking_status": "tracked",
                "interest_status": "neutral",
            },
        ]
        discovery_rows = [
            {**discovery_candidate("DC0001", "CO0001"), "title": "Platform Architect"},
            {**discovery_candidate("DC0002", "CO0001"), "title": "Product Operations Lead"},
            {
                **discovery_candidate("DC0003", "CO0002"),
                "title": "Sales Executive",
                "status": "ignored",
            },
            {
                **discovery_candidate("DC0004", "CO0002"),
                "title": "Account Representative",
                "status": "ignored",
            },
        ]
        with patch.object(
            read_models.discovery_store,
            "recommendation_eligible",
            side_effect=lambda row, _company: row.get("company_id") == "CO0001",
        ):
            shell = read_models.app_shell_from_rows(
                revision=21,
                applications=[],
                actions=[],
                workflow_payload={},
                contacts=[],
                application_contacts=[],
                companies=companies,
                company_contacts=[],
                company_career_sources=[],
                discovery_searches=searches(),
                company_candidates=[],
                discovery_candidates=discovery_rows,
                dismissed_suggestion_ids=set(),
            )

        by_id = {row["id"]: row for row in shell_companies(shell)}
        self.assertEqual(by_id["CO0001"]["discovery_role_count"], 2)
        self.assertEqual(by_id["CO0001"]["recommended_discovery_role_count"], 2)
        self.assertEqual(by_id["CO0001"]["ignored_role_count"], 0)
        self.assertEqual(by_id["CO0001"]["pursued_role_count"], 0)
        self.assertTrue(
            by_id["CO0001"]["tracking_recommendation"].startswith("Hunter suggests tracking")
        )
        self.assertEqual(by_id["CO0002"]["ignored_role_count"], 2)
        self.assertIn("mark it Not interested", by_id["CO0002"]["decision_recommendation"])

    def test_shell_compacts_discovery_run_summary_to_ui_fields(self):
        search = searches()[0]
        search["last_run_summary"] = {
            "evaluated_count": 100,
            "qualified_count": 20,
            "known_count": 5,
            "associated_count": 4,
            "new_count": 6,
            "updated_count": 3,
            "lane_unmatched_count": 2,
            "duplicate_count": 1,
            "screened_count": 70,
            "limited_count": 7,
            "enriched_count": 8,
            "errors": ["source failed"],
            "sources": [{"payload": "x" * 20_000}],
            "skip_reasons": {"outside-scope": 70},
            "role_family_counts": {"technical-program": 20},
            "enrichment": {"large": "x" * 20_000},
        }
        shell = read_models.app_shell_from_rows(
            revision=22,
            applications=[],
            actions=[],
            workflow_payload={},
            contacts=[],
            application_contacts=[],
            companies=[],
            company_contacts=[],
            company_career_sources=[],
            discovery_searches=[search],
            company_candidates=[],
            discovery_candidates=[],
            dismissed_suggestion_ids=set(),
        )

        summary = shell["discovery_searches"][0]["last_run_summary"]
        self.assertEqual(summary["new_count"], 6)
        self.assertEqual(summary["associated_count"], 4)
        self.assertEqual(summary["errors"], ["source failed"])
        self.assertNotIn("sources", summary)
        self.assertNotIn("skip_reasons", summary)
        self.assertNotIn("role_family_counts", summary)
        self.assertNotIn("enrichment", summary)

    def test_shell_derives_compact_suggestions_from_loaded_rows(self):
        companies = [
            {**company("CO0001"), "name": "Acme"},
            {**company("CO0002"), "name": "Acme LLC"},
        ]
        ignored = [
            {
                **discovery_candidate("DC0001", "CO0001"),
                "title": "Sales Ninja",
                "status": "ignored",
                "ignore_reason": "wrong-role",
            },
            {
                **discovery_candidate("DC0002", "CO0002"),
                "title": "Sales Executive",
                "status": "ignored",
                "ignore_reason": "wrong-role",
            },
        ]
        shell = read_models.app_shell_from_rows(
            revision=20,
            applications=[],
            actions=[],
            workflow_payload={},
            contacts=[],
            application_contacts=[],
            companies=companies,
            company_contacts=[],
            company_career_sources=[],
            discovery_searches=searches(),
            company_candidates=[],
            discovery_candidates=ignored,
            dismissed_suggestion_ids=set(),
        )

        self.assertEqual(len(shell["company_merge_suggestions"]), 1)
        self.assertEqual(shell["company_merge_suggestions"][0]["match_key"], "acme")
        self.assertEqual(len(shell["discovery_preference_suggestions"]), 1)
        self.assertEqual(shell["discovery_preference_suggestions"][0]["term"], "sales")
        self.assertEqual(shell["discovery_preference_suggestions"][0]["ignored_count"], 2)

    def test_pages_are_bounded_and_lists_omit_large_private_fields(self):
        context = basic_context()
        result = read_models.discovery_candidate_page({"limit": ["1"]}, context)

        self.assertEqual(result["revision"], 17)
        self.assertEqual(result["counts"]["eligible"], 2)
        self.assertEqual(result["counts"]["returned"], 1)
        self.assertEqual(result["page"]["limit"], 1)
        self.assertTrue(result["page"]["has_more"])
        self.assertTrue(result["page"]["next_cursor"])
        item = result["items"][0]
        for omitted in ("description_text", "warnings", "notes", "source_urls_json"):
            self.assertNotIn(omitted, item)
        self.assertEqual(item["fit_summary"], "Strong private discovery evidence.")
        self.assertEqual(item["source_trust"], "network")
        self.assertTrue(item["source_trust_label"])
        self.assertTrue(item["source_confidence"])
        self.assertIn("detail_next_action", item)
        self.assertIn("review_next_action", item)
        self.assertLessEqual(len(item["description_excerpt"]), read_models.LIST_DESCRIPTION_LIMIT)

    def test_cursor_is_opaque_filter_bound_and_revision_bound(self):
        context = basic_context()
        first = read_models.company_candidate_page({"limit": ["1"]}, context)
        cursor = first["page"]["next_cursor"]
        second = read_models.company_candidate_page({"limit": ["1"], "cursor": [cursor]}, context)

        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        with self.assertRaisesRegex(read_models.ReadModelError, "filters") as mismatch:
            read_models.company_candidate_page(
                {"limit": ["1"], "cursor": [cursor], "minimum_fit_score": ["80"]}, context
            )
        self.assertEqual(mismatch.exception.status, 400)
        changed = basic_context(revision=18)
        with self.assertRaisesRegex(read_models.ReadModelError, "changed") as stale:
            read_models.company_candidate_page({"limit": ["1"], "cursor": [cursor]}, changed)
        self.assertEqual(stale.exception.status, 409)

    def test_server_side_filters_and_limit_cap(self):
        context = basic_context()
        result = read_models.company_candidate_page(
            {
                "search": ["platform"],
                "status": ["new"],
                "minimum_fit_score": ["80"],
                "tracking_status": ["active"],
                "company_id": ["co0001"],
                "limit": ["999"],
            },
            context,
        )

        self.assertEqual([item["id"] for item in result["items"]], ["CP0001"])
        self.assertEqual(result["page"]["limit"], read_models.MAX_PAGE_LIMIT)
        self.assertIn("statuses", result["facets"])
        self.assertIn("companies", result["facets"])
        self.assertEqual(result["audit"]["filters"]["company_id"], "CO0001")
        self.assertEqual(result["audit"]["filters"]["search"], "platform")
        self.assertEqual(result["audit"]["filters"]["minimum_fit_score"], 80)
        self.assertEqual(result["audit"]["filters"]["tracking_status"], "active")

    def test_company_status_facets_and_pages_share_the_same_filter_scope(self):
        company_row = {
            **company("CO0001"),
            "last_checked_at": "2026-09-04T09:00:00",
        }
        rows = [
            {
                **company_candidate("CP0001", "CO0001", fit_score=91, status="new"),
                "last_seen_at": "2026-09-04T09:00:00",
            },
            {
                **company_candidate("CP0002", "CO0001", fit_score=75, status="new"),
                "last_seen_at": "2026-09-04T09:00:00",
            },
            {
                **company_candidate("CP0003", "CO0001", fit_score=70, status="ignored"),
                "last_seen_at": "2026-09-04T09:00:00",
            },
            {
                **company_candidate("CP0004", "CO0001", fit_score=80, status="pursued"),
                "last_seen_at": "2026-09-04T09:00:00",
            },
        ]
        context = read_models.CandidateReadContext.from_rows(
            companies=[company_row],
            applications=[],
            searches=searches(),
            company_candidates=rows,
            discovery_candidates=[],
            revision=23,
        )

        page = read_models.company_candidate_page(
            {
                "status": ["new"],
                "tracking_status": ["active"],
                "interest_status": ["interested"],
                "fit_band": ["recommended"],
                "limit": ["1"],
            },
            context,
        )

        self.assertEqual(page["counts"]["filtered"], 2)
        self.assertEqual(page["counts"]["returned"], 1)
        self.assertTrue(page["page"]["has_more"])
        self.assertEqual(
            {item["value"]: item["count"] for item in page["facets"]["statuses"]},
            {"ignored": 1, "new": 2, "pursued": 1},
        )

    def test_transitional_filter_aliases_remain_supported_but_audit_is_canonical(self):
        result = read_models.company_candidate_page(
            {"q": ["platform"], "min_fit": ["80"], "tracking": ["active"]},
            basic_context(),
        )

        self.assertEqual([item["id"] for item in result["items"]], ["CP0001"])
        self.assertEqual(
            result["audit"]["filters"],
            {
                "search": "platform",
                "status": [],
                "minimum_fit_score": 80,
                "tracking_status": "active",
                "company_id": "",
                "company_ids": [],
                "interest_statuses": [],
                "industries": [],
                "sizes": [],
                "sources": [],
                "fit_band": "all",
                "latest_only": False,
                "lane_match_only": False,
                "reviewable_only": False,
                "sort": "fit",
                "direction": "desc",
                "include_excluded_companies": False,
                "include_out_of_scope": False,
                "search_id": "",
            },
        )

    def test_search_id_is_context_only_and_never_filters_global_discovery(self):
        context = basic_context()
        primary = read_models.discovery_candidate_page({"search_id": ["DS0001"]}, context)
        secondary = read_models.discovery_candidate_page({"search_id": ["DS0002"]}, context)

        self.assertEqual(
            [item["id"] for item in primary["items"]],
            [item["id"] for item in secondary["items"]],
        )
        self.assertEqual(primary["counts"]["filtered"], secondary["counts"]["filtered"])
        self.assertFalse(primary["audit"]["search_context"]["affects_rows"])
        stale = read_models.discovery_candidate_page({"search_id": ["DS9999"]}, context)
        self.assertEqual(stale["audit"]["search_context"]["id"], "DS9999")
        self.assertEqual(stale["audit"]["search_context"]["name"], "")
        self.assertEqual(
            [item["id"] for item in primary["items"]],
            [item["id"] for item in stale["items"]],
        )

    def test_search_context_can_change_without_invalidating_global_queue_cursor(self):
        context = basic_context()
        first = read_models.discovery_candidate_page(
            {"limit": ["1"], "search_id": ["DS0001"]},
            context,
        )
        second = read_models.discovery_candidate_page(
            {
                "limit": ["1"],
                "search_id": ["DS0002"],
                "cursor": [first["page"]["next_cursor"]],
            },
            context,
        )

        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(second["audit"]["search_context"]["id"], "DS0002")

    def test_shell_counts_match_visible_global_queue_rows(self):
        ignored_source = {
            **discovery_candidate("DC0001", "CO0001"),
            "url": "https://builtin.com/job/example",
            "canonical_url": "https://builtin.com/job/example",
        }
        shell = read_models.app_shell_from_rows(
            revision=21,
            applications=[],
            actions=[],
            workflow_payload={},
            contacts=[],
            application_contacts=[],
            companies=[company("CO0001")],
            company_contacts=[],
            company_career_sources=[],
            discovery_searches=searches(),
            company_candidates=[],
            discovery_candidates=[ignored_source],
            dismissed_suggestion_ids=set(),
        )

        self.assertEqual(shell["candidate_counts"]["discovery"], 0)

    def test_repeated_revision_races_fail_instead_of_minting_unsafe_cursor(self):
        with (
            patch.object(
                read_models.repository,
                "data_revision",
                side_effect=[1, 1, 1, 2, 3, 4],
            ),
            patch.object(read_models.repository, "read_companies", return_value=[]),
            patch.object(read_models.repository, "read_applications", return_value=[]),
            patch.object(read_models.discovery_store, "list_searches", return_value=[]),
            patch.object(read_models.repository, "read_company_posting_candidates", return_value=[]),
            patch.object(read_models.repository, "read_discovery_candidates", return_value=[]),
        ):
            with self.assertRaises(read_models.ReadModelError) as changed:
                read_models.CandidateReadContext.read()

        self.assertEqual(changed.exception.status, 409)
        self.assertIn("changed", changed.exception.message)

    def test_context_cache_reuses_a_stable_revision(self):
        previous_cache = read_models._candidate_context_cache
        read_models._candidate_context_cache = None
        try:
            with (
                patch.object(read_models.repository, "data_revision", return_value=81),
                patch.object(read_models.repository, "read_companies", return_value=[]) as companies_reader,
                patch.object(read_models.repository, "read_applications", return_value=[]),
                patch.object(read_models.discovery_store, "list_searches", return_value=[]),
                patch.object(read_models.repository, "read_company_posting_candidates", return_value=[]),
                patch.object(read_models.repository, "read_discovery_candidates", return_value=[]),
            ):
                first = read_models.CandidateReadContext.read()
                second = read_models.CandidateReadContext.read()

            self.assertIs(first, second)
            companies_reader.assert_called_once_with()
        finally:
            read_models._candidate_context_cache = previous_cache

    def test_excluded_companies_are_default_hidden_and_audit_only_opt_in(self):
        context = basic_context()
        normal = read_models.discovery_candidate_page({}, context)
        audit = read_models.discovery_candidate_page({"include_excluded_companies": ["true"]}, context)

        self.assertNotIn("DC0003", {item["id"] for item in normal["items"]})
        self.assertIn("DC0003", {item["id"] for item in audit["items"]})
        self.assertEqual(normal["counts"]["excluded_companies"], 1)
        self.assertTrue(audit["audit"]["filters"]["include_excluded_companies"])

    def test_details_retain_full_evidence_and_notes(self):
        context = basic_context()
        discovery = read_models.discovery_candidate_detail({"id": ["dc0001"]}, context)
        company_row = read_models.company_candidate_detail({"id": ["CP0001"]}, context)

        self.assertIn("Full private posting description", discovery["item"]["description_text"])
        self.assertEqual(discovery["item"]["warnings"], "private warning")
        self.assertEqual(discovery["item"]["notes"], "private discovery note")
        self.assertEqual(company_row["item"]["fit_summary"], "Strong private fit evidence.")
        self.assertEqual(company_row["item"]["notes"], "private candidate note")

    def test_context_resolves_each_discovery_candidate_once(self):
        rows = [
            discovery_candidate(f"DC{index:04d}", f"CO{index:04d}")
            for index in range(1, 8)
        ]
        from hunter import discovery

        with patch.object(
            discovery,
            "resolved_candidate_for_review",
            wraps=discovery.resolved_candidate_for_review,
        ) as resolver:
            read_models.CandidateReadContext.from_rows(
                companies=[company(f"CO{index:04d}") for index in range(1, 8)],
                applications=[],
                searches=searches(),
                company_candidates=[],
                discovery_candidates=rows,
                revision=1,
            )

        self.assertEqual(resolver.call_count, len(rows))

    def test_discovery_detail_reads_only_the_requested_candidate(self):
        selected = discovery_candidate("DC0001", "CO0001")
        with (
            patch.object(read_models.repository, "data_revision", side_effect=[23, 23]),
            patch.object(read_models.repository, "read_companies", return_value=[company("CO0001")]),
            patch.object(read_models.repository, "read_applications", return_value=[]),
            patch.object(read_models.discovery_store, "list_searches", return_value=searches()),
            patch.object(read_models.repository, "read_discovery_candidate", return_value=selected) as read_one,
            patch.object(read_models.repository, "read_discovery_candidates") as read_all,
            patch.object(read_models.repository, "read_company_posting_candidates") as read_company_pool,
            patch.object(read_models.repository, "read_company_posting_candidates_for_company", return_value=[]) as read_peers,
        ):
            result = read_models.build_discovery_candidate_detail({"id": ["DC0001"]})

        self.assertEqual(result["item"]["id"], "DC0001")
        read_one.assert_called_once_with("DC0001")
        read_all.assert_not_called()
        read_company_pool.assert_not_called()
        read_peers.assert_called_once_with("CO0001")

    def test_discovery_detail_uses_the_same_cross_pool_identity_as_the_list(self):
        selected = discovery_candidate("DC0001", "CO0001")
        peer = company_candidate("CP0001", "CO0001", status="ignored")
        peer["url"] = selected["url"]
        context = read_models.CandidateReadContext.from_rows(
            companies=[company("CO0001")], applications=[], searches=searches(),
            company_candidates=[peer], discovery_candidates=[selected], revision=23,
        )
        expected = read_models.discovery_candidate_detail({"id": ["DC0001"]}, context)["item"]
        with (
            patch.object(read_models.repository, "data_revision", return_value=23),
            patch.object(read_models.repository, "read_companies", return_value=[company("CO0001")]),
            patch.object(read_models.repository, "read_applications", return_value=[]),
            patch.object(read_models.discovery_store, "list_searches", return_value=searches()),
            patch.object(read_models.repository, "read_discovery_candidate", return_value=selected),
            patch.object(read_models.repository, "read_company_posting_candidates_for_company", return_value=[peer]),
        ):
            actual = read_models.build_discovery_candidate_detail({"id": ["DC0001"]})["item"]
        for field in ["is_canonical", "canonical_source", "canonical_status", "company_candidate_id"]:
            self.assertEqual(actual[field], expected[field], field)
        self.assertFalse(actual["is_canonical"])
        self.assertEqual(actual["canonical_status"], "ignored")



class ReadModelBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metadata_suggestions = json.dumps([
            {"field": f"field-{index}", "evidence": "x" * 300}
            for index in range(10)
        ])
        cls.companies = [
            {
                **company(f"CO{index:04d}"),
                "company_metadata_suggestions_json": metadata_suggestions,
                "company_fit_summary": "Current-scale fit evidence. " * 30,
            }
            for index in range(1, 601)
        ]
        cls.company_candidates = [
            company_candidate(
                f"CP{index:04d}",
                f"CO{((index - 1) % 600) + 1:04d}",
                fit_score=40 + index % 60,
            )
            for index in range(1, 4001)
        ]
        cls.discovery_candidates = [
            discovery_candidate(
                f"DC{index:04d}",
                f"CO{((index - 1) % 600) + 1:04d}",
                fit_score=40 + index % 60,
                search_ids=["DS0001", "DS0002"] if index % 2 else ["DS0002"],
            )
            for index in range(1, 651)
        ]

    def test_current_scale_shell_page_and_detail_meet_cold_budgets(self):
        budget_searches = searches()
        budget_searches[0]["last_run_summary"] = {
            "evaluated_count": 650,
            "new_count": 50,
            "errors": ["source failed"],
            "sources": [{"large": "x" * 100_000}],
            "enrichment": {"large": "x" * 100_000},
        }
        heavy_actions = [
            {
                "id": f"AC{index:04d}",
                "title": f"Action {index}",
                "status": "open",
                "description": "x" * 5_000,
                "related_url": f"https://private.example/{index}",
                "source": "private-source",
            }
            for index in range(1, 201)
        ]
        started = time.perf_counter()
        shell = read_models.app_shell_from_rows(
            revision=31,
            applications=[],
            actions=heavy_actions,
            workflow_payload={},
            contacts=[],
            application_contacts=[],
            companies=self.companies,
            company_contacts=[],
            company_career_sources=[],
            discovery_searches=budget_searches,
            company_candidates=self.company_candidates,
            discovery_candidates=self.discovery_candidates,
            dismissed_suggestion_ids=set(),
        )
        shell_ms = (time.perf_counter() - started) * 1000
        shell_bytes = len(json.dumps(shell, separators=(",", ":")).encode("utf-8"))
        self.assertIn("company_merge_suggestions", shell)
        self.assertIn("discovery_preference_suggestions", shell)

        started = time.perf_counter()
        context = read_models.CandidateReadContext.from_rows(
            companies=self.companies,
            applications=[],
            searches=searches(),
            company_candidates=self.company_candidates,
            discovery_candidates=self.discovery_candidates,
            revision=31,
        )
        page = read_models.company_candidate_page({}, context)
        page_ms = (time.perf_counter() - started) * 1000
        page_bytes = len(json.dumps(page, separators=(",", ":")).encode("utf-8"))

        started = time.perf_counter()
        detail_context = read_models.CandidateReadContext.from_rows(
            companies=self.companies,
            applications=[],
            searches=searches(),
            company_candidates=[],
            discovery_candidates=[self.discovery_candidates[0]],
            revision=31,
        )
        detail = read_models.discovery_candidate_detail({"id": ["DC0001"]}, detail_context)
        detail_ms = (time.perf_counter() - started) * 1000
        detail_bytes = len(json.dumps(detail, separators=(",", ":")).encode("utf-8"))

        self.assertLessEqual(shell_bytes, 500 * 1024, (shell_bytes, shell_ms))
        self.assertLessEqual(shell_ms, 500, (shell_bytes, shell_ms))
        self.assertLessEqual(page_bytes, 150 * 1024, (page_bytes, page_ms))
        self.assertLessEqual(page_ms, 400, (page_bytes, page_ms))
        self.assertLessEqual(detail_bytes, 120 * 1024, (detail_bytes, detail_ms))
        self.assertLessEqual(detail_ms, 250, (detail_bytes, detail_ms))


if __name__ == "__main__":
    unittest.main()
