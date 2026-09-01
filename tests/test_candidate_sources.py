import json
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from hunter import candidate_sources


SEARCH = {
    "id": "DS0001",
    "name": "TPM",
    "keywords": "platform",
    "role_family_ids": ["technical-program"],
    "excluded_terms": ["marketing"],
    "lanes": [
        {
            "id": "remote-us",
            "label": "United States remote",
            "location": "United States",
            "work_modes": ["remote"],
        }
    ],
}
FAMILIES = [
    {
        "id": "technical-program",
        "label": "Technical program leadership",
        "terms": ["technical program manager", "technical project manager"],
        "strong_terms": ["technical program manager"],
    }
]


class CandidateSourcesTest(unittest.TestCase):
    def test_provider_bundle_keeps_ats_results_when_openai_times_out(self):
        ats_result = {
            "provider": "ats",
            "url": "https://jobs.example.com/jobs/123",
            "title": "Senior Technical Program Manager",
            "company": "Example",
        }

        with (
            patch("hunter.candidate_sources.ats_inventory_results", return_value=[ats_result]),
            patch("hunter.candidate_sources.settings.adzuna_credentials", return_value={"app_id": "", "app_key": ""}),
        ):
            bundle = candidate_sources.provider_bundle(
                SEARCH,
                FAMILIES,
                openai_requester=lambda *_args: (_ for _ in ()).throw(TimeoutError("read timed out")),
            )

        self.assertEqual(bundle["results"], [ats_result])
        self.assertIn("OpenAI web search: read timed out", bundle["errors"])
        self.assertEqual(
            [source["engine"] for source in bundle["sources"]],
            ["direct-ats", "not-configured", "openai-web-search"],
        )

    def test_provider_bundle_does_not_retry_a_timed_out_openai_request(self):
        ats_result = {"provider": "ats", "url": "https://jobs.example.com/jobs/123", "title": "TPM"}
        with (
            patch("hunter.candidate_sources.ats_inventory_results", return_value=[ats_result]),
            patch("hunter.candidate_sources.settings.adzuna_credentials", return_value={"app_id": "", "app_key": ""}),
            patch(
                "hunter.candidate_sources.openai_role_results",
                side_effect=TimeoutError("read timed out"),
            ) as search,
        ):
            bundle = candidate_sources.provider_bundle(SEARCH, FAMILIES)

        self.assertEqual(search.call_count, 1)
        self.assertEqual(bundle["results"], [ats_result])
        self.assertIn("OpenAI web search: read timed out", bundle["errors"])

    def test_provider_bundle_skips_openai_when_cheap_sources_cover_family(self):
        ats_results = [
            {
                "provider": "ats",
                "url": f"https://jobs.example.com/jobs/{index}",
                "title": "Technical Program Manager",
                "role_family_ids": ["technical-program"],
            }
            for index in range(candidate_sources.OPENAI_FALLBACK_MIN_RESULTS_PER_FAMILY)
        ]

        with (
            patch("hunter.candidate_sources.ats_inventory_results", return_value=ats_results),
            patch("hunter.candidate_sources.settings.adzuna_credentials", return_value={"app_id": "", "app_key": ""}),
            patch("hunter.candidate_sources.openai_role_results") as search,
        ):
            bundle = candidate_sources.provider_bundle(SEARCH, FAMILIES)

        search.assert_not_called()
        self.assertEqual(bundle["results"], ats_results)
        self.assertEqual(bundle["sources"][-1]["engine"], "skipped-sufficient-coverage")
        self.assertTrue(bundle["sources"][-1]["skipped"])

    def test_provider_bundle_uses_openai_only_for_undercovered_families(self):
        product_family = {
            "id": "product-platform",
            "label": "Product platform leadership",
            "terms": ["product manager"],
            "strong_terms": ["product manager"],
        }
        search_config = {
            **SEARCH,
            "role_family_ids": ["technical-program", "product-platform"],
        }
        ats_results = [
            {
                "provider": "ats",
                "url": f"https://jobs.example.com/jobs/{index}",
                "title": "Technical Program Manager",
                "role_family_ids": ["technical-program"],
            }
            for index in range(candidate_sources.OPENAI_FALLBACK_MIN_RESULTS_PER_FAMILY)
        ]

        with (
            patch("hunter.candidate_sources.ats_inventory_results", return_value=ats_results),
            patch("hunter.candidate_sources.settings.adzuna_credentials", return_value={"app_id": "", "app_key": ""}),
            patch("hunter.candidate_sources.openai_role_results", return_value=[]) as openai_results,
        ):
            bundle = candidate_sources.provider_bundle(
                search_config,
                [*FAMILIES, product_family],
            )

        fallback_search = openai_results.call_args.args[0]
        self.assertEqual(fallback_search["role_family_ids"], ["product-platform"])
        openai_sources = [source for source in bundle["sources"] if source["source"] == "openai-web"]
        self.assertEqual(
            [source["engine"] for source in openai_sources],
            ["skipped-sufficient-coverage", "openai-web-search"],
        )

    def test_ats_inventory_reuses_current_direct_candidates(self):
        candidates = [
            {
                "company_id": "CO0001",
                "title": "Senior Technical Program Manager",
                "url": "https://jobs.example.com/jobs/123",
                "location": "United States",
                "work_mode": "Remote",
                "description_excerpt": "Lead platform delivery across engineering teams.",
                "status": "new",
                "scan_state": "open",
            },
            {
                "company_id": "CO0001",
                "title": "Technical Program Manager, closed",
                "url": "https://jobs.example.com/jobs/old",
                "status": "unavailable",
            },
        ]
        with (
            patch("hunter.candidate_sources.repository.read_companies", return_value=[{"id": "CO0001", "name": "Example"}]),
            patch("hunter.candidate_sources.repository.read_company_posting_candidates", return_value=candidates),
        ):
            results = candidate_sources.ats_inventory_results(SEARCH, FAMILIES)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["company"], "Example")
        self.assertEqual(results[0]["provider"], "ats")

    def test_ats_inventory_omits_candidates_from_excluded_companies(self):
        candidates = [
            {
                "company_id": "CO0001",
                "title": "Senior Technical Program Manager",
                "url": "https://jobs.example.com/jobs/123",
                "location": "United States",
                "work_mode": "Remote",
                "description_excerpt": "Excluded posting content",
                "status": "new",
                "scan_state": "open",
            }
        ]
        with (
            patch(
                "hunter.candidate_sources.repository.read_companies",
                return_value=[
                    {"id": "CO0001", "name": "Example", "interest_status": "not-interested"}
                ],
            ),
            patch(
                "hunter.candidate_sources.repository.read_company_posting_candidates",
                return_value=candidates,
            ),
        ):
            results = candidate_sources.ats_inventory_results(SEARCH, FAMILIES)

        self.assertEqual(results, [])

    def test_openai_accepts_only_source_backed_direct_urls(self):
        direct_url = "https://boards.greenhouse.io/example/jobs/123456"
        response = {
            "model": "gpt-test",
            "output_text": json.dumps(
                {
                    "roles": [
                        {
                            "title": "Technical Program Manager",
                            "company": "Example",
                            "job_url": direct_url,
                            "location": "United States",
                            "work_mode": "remote",
                            "description_summary": "Leads platform delivery.",
                            "role_family_id": "technical-program",
                            "lane_id": "remote-us",
                        },
                        {
                            "title": "Technical Program Manager",
                            "company": "Uncited",
                            "job_url": "https://jobs.uncited.example/456",
                            "location": "United States",
                            "work_mode": "remote",
                            "description_summary": "Should be rejected.",
                            "role_family_id": "technical-program",
                            "lane_id": "remote-us",
                        },
                    ]
                }
            ),
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"sources": [{"url": direct_url, "title": "Example role"}]},
                }
            ],
        }
        captured = {}

        def requester(url, token, payload):
            captured.update({"url": url, "token": token, "payload": payload})
            return response

        with (
            patch("hunter.candidate_sources.agent._settings", return_value={"model": "gpt-test", "token": "secret", "api_base": "https://api.openai.com/v1"}),
            patch("hunter.candidate_sources.api_usage.log_usage") as log_usage,
        ):
            results = candidate_sources.openai_role_results(SEARCH, FAMILIES, requester=requester)

        self.assertEqual([row["url"] for row in results], [direct_url])
        self.assertEqual(results[0]["provider"], "openai")
        self.assertEqual(captured["payload"]["metadata"]["feature"], "candidate-discovery")
        self.assertEqual(captured["payload"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["payload"]["tools"][0]["search_context_size"], "low")
        self.assertEqual(captured["payload"]["max_tool_calls"], 4)
        self.assertEqual(captured["payload"]["max_output_tokens"], 5_000)
        self.assertIn("Return at most 10 roles", captured["payload"]["input"])
        log_usage.assert_called_once()
        self.assertEqual(
            log_usage.call_args.kwargs["context"],
            {"search_id": "DS0001", "role_family_id": "technical-program"},
        )

    def test_adzuna_queries_precisely_and_preserves_redirect_url(self):
        captured = {}
        redirect_url = "https://www.adzuna.com/details/123?utm_medium=api"

        def fetcher(url):
            captured.update(parse_qs(urlparse(url).query))
            return {
                "html": json.dumps(
                    {
                        "results": [
                            {
                                "id": "123",
                                "title": "Senior Technical Program Manager (Remote)",
                                "redirect_url": redirect_url,
                                "company": {"display_name": "Example"},
                                "location": {"display_name": "United States"},
                                "description": "Lead a platform program remotely.",
                            }
                        ]
                    }
                )
            }

        results, sources, errors = candidate_sources.adzuna_role_results(
            SEARCH,
            FAMILIES,
            {"app_id": "app-id", "app_key": "app-key"},
            fetcher=fetcher,
        )

        self.assertEqual(errors, [])
        self.assertEqual(results[0]["url"], redirect_url)
        self.assertEqual(results[0]["provider"], "adzuna")
        self.assertEqual(captured["what_phrase"], ["technical program manager"])
        self.assertEqual(captured["what_and"], ["remote"])
        self.assertEqual(captured["sort_by"], ["relevance"])
        self.assertEqual(sources[0]["label"], "Jobs by Adzuna")


if __name__ == "__main__":
    unittest.main()
