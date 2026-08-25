from __future__ import annotations

import unittest

from valp_cli.conformance import (
    PROFILE_REQUIRED_CHECKS,
    UNIMPLEMENTED_RFC_PROFILES,
    run_conformance,
)


class ConformanceProfileTests(unittest.TestCase):
    def test_each_profile_runs_its_declared_required_checks(self) -> None:
        for profile, required_checks in PROFILE_REQUIRED_CHECKS.items():
            with self.subTest(profile=profile):
                report = run_conformance(profile)
                self.assertEqual(report["profile"], profile)
                self.assertEqual(report["claim_scope"], "reference-smoke")
                self.assertFalse(report["conformance_claim"])
                self.assertEqual(
                    report["unimplemented_rfc_profiles"],
                    list(UNIMPLEMENTED_RFC_PROFILES),
                )
                self.assertEqual(report["required_checks"], list(required_checks))
                self.assertEqual(
                    [check["name"] for check in report["checks"]],
                    list(required_checks),
                )
                self.assertEqual(report["pass_count"], len(required_checks))
                self.assertEqual(report["fail_count"], 0)

    def test_profiles_have_distinct_required_check_sets(self) -> None:
        profiles = list(PROFILE_REQUIRED_CHECKS)
        required_sets = [frozenset(PROFILE_REQUIRED_CHECKS[profile]) for profile in profiles]

        self.assertEqual(len(required_sets), len(set(required_sets)))
        self.assertNotEqual(
            set(PROFILE_REQUIRED_CHECKS["core-reader"]),
            set(PROFILE_REQUIRED_CHECKS["core-writer"]),
        )

    def test_profiles_preserve_the_existing_smoke_coverage_as_a_union(self) -> None:
        required_checks = set().union(*PROFILE_REQUIRED_CHECKS.values())

        self.assertEqual(
            required_checks,
            {
                "bootstrap-selection-epoch",
                "fixed-hello",
                "bootstrap-epoch-fencing",
                "revision-cas",
                "capability-layered-registry",
                "content-addressed-claim-review",
                "task-done-gate-reducer",
                "non-herdr-process-adapter",
                "event-replay-digest",
                "plugin-boundary",
                "legacy-migration-dry-run",
            },
        )

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported conformance profile"):
            run_conformance("all-profiles")


if __name__ == "__main__":
    unittest.main()
