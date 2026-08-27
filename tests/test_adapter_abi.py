from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from valp_cli.adapter_abi import (
    ABI_VERSION,
    AdapterCapability,
    AdapterManifest,
    AdapterMode,
    AdapterObservation,
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    CapabilityStatus,
    CompositeProofPolicy,
    ProofKind,
    ProvenanceSegment,
    assess_composite_proof,
    validate_observation,
)


DIGEST_A = "sha256:" + "1" * 64
DIGEST_B = "sha256:" + "2" * 64
DIGEST_C = "sha256:" + "3" * 64


class AdapterAbiTests(unittest.TestCase):
    def manifest(self):
        return AdapterManifest(
            adapter_id="runtime-a",
            adapter_class="hosted_runtime",
            abi_version=ABI_VERSION,
            capabilities=tuple(
                AdapterCapability(operation, CapabilityStatus.SUPPORTED)
                for operation in AdapterOperation
            ),
        )

    def request(self, operation=AdapterOperation.SUBMIT):
        return AdapterRequest(
            request_id="request-1",
            operation=operation,
            installation_id="installation-1",
            leader_epoch=2,
            task_id="task-1",
            work_item_id="work-1",
            attempt_id="attempt-1",
            dispatch_id="dispatch-1",
            dispatch_generation=0,
            payload_digest=DIGEST_A,
            expected_evidence_refs=("evidence/result.md",),
        )

    def segments(self):
        return (
            ProvenanceSegment(
                segment_id="segment-0", sequence=0, adapter_id="transport-a",
                abi_version=ABI_VERSION, input_identity="request-1",
                input_digest=DIGEST_A, output_identity="run-1",
                output_digest=DIGEST_B, proof_kind=ProofKind.PROCESS_BOUND,
                evidence_refs=("runtime/process.json",), acknowledged=True,
            ),
            ProvenanceSegment(
                segment_id="segment-1", sequence=1, adapter_id="runtime-a",
                abi_version=ABI_VERSION, input_identity="run-1",
                input_digest=DIGEST_B, output_identity="output-1",
                output_digest=DIGEST_C, proof_kind=ProofKind.CONTENT_BOUND,
                evidence_refs=("runtime/content.json",), acknowledged=True,
            ),
        )

    def observation(self, **changes):
        values = {
            "observation_id": "observation-1",
            "request": self.request(),
            "sequence": 0,
            "status": AdapterStatus.COMPLETED,
            "runtime_identity": "run-1",
            "provenance": self.segments(),
            "evidence_refs": ("evidence/result.md",),
        }
        values.update(changes)
        return AdapterObservation(**values)

    def test_manifest_requires_exact_closed_capability_table(self) -> None:
        manifest = self.manifest()
        self.assertEqual(len(manifest.capabilities), len(AdapterOperation))
        self.assertEqual(manifest.canonical()["abi_version"], "1.0")

        with self.assertRaises(ValueError):
            replace(manifest, capabilities=manifest.capabilities[:-1])
        with self.assertRaises(ValueError):
            AdapterManifest("runtime-a", "hosted", "2.0", manifest.capabilities)

    def test_unsupported_capability_requires_reason_and_explicit_observation(self) -> None:
        with self.assertRaises(ValueError):
            AdapterCapability(AdapterOperation.CANCEL, CapabilityStatus.UNSUPPORTED)
        capability = AdapterCapability(
            AdapterOperation.CANCEL, CapabilityStatus.UNSUPPORTED,
            reason="runtime has no cancellation API",
        )
        manifest = replace(self.manifest(), capabilities=tuple(
            capability if item.operation == AdapterOperation.CANCEL else item
            for item in self.manifest().capabilities
        ))
        request = self.request(AdapterOperation.CANCEL)
        observation = AdapterObservation(
            "observation-unsupported", request, 0, AdapterStatus.UNSUPPORTED,
            None, (), (), "runtime has no cancellation API",
        )
        validate_observation(manifest, observation)

    def test_composite_chain_binds_every_adjacent_identity_and_digest(self) -> None:
        validate_observation(self.manifest(), self.observation())
        broken = replace(
            self.segments()[1], input_digest=DIGEST_A,
        )
        with self.assertRaises(ValueError):
            self.observation(provenance=(self.segments()[0], broken))

    def test_full_proof_requires_process_content_and_complete_evidence(self) -> None:
        policy = CompositeProofPolicy(
            AdapterMode.FULL,
            (ProofKind.PROCESS_BOUND, ProofKind.CONTENT_BOUND),
        )
        passed = assess_composite_proof(self.observation(), policy)
        self.assertTrue(passed.passed)
        self.assertEqual(passed.reasons, ())

        missing_evidence = self.observation(evidence_refs=())
        result = assess_composite_proof(missing_evidence, policy)
        self.assertFalse(result.passed)
        self.assertIn("missing_expected_evidence", result.reasons)

    def test_transport_only_segment_cannot_be_hidden_by_stronger_downstream_proof(self) -> None:
        weak = replace(self.segments()[0], proof_kind=ProofKind.TRANSPORT_ONLY)
        observation = self.observation(provenance=(weak, self.segments()[1]))
        policy = CompositeProofPolicy(
            AdapterMode.FULL,
            (ProofKind.PROCESS_BOUND, ProofKind.CONTENT_BOUND),
        )
        result = assess_composite_proof(observation, policy)
        self.assertFalse(result.passed)
        self.assertIn("transport_only_segment", result.reasons)
        self.assertIn("missing_proof:process_bound", result.reasons)

    def test_manual_attestation_cannot_be_relabelled_as_full(self) -> None:
        manual = replace(
            self.segments()[0], proof_kind=ProofKind.MANUAL_ATTESTED,
        )
        observation = self.observation(provenance=(manual,))
        manual_result = assess_composite_proof(
            observation,
            CompositeProofPolicy(AdapterMode.MANUAL, (ProofKind.MANUAL_ATTESTED,)),
        )
        full_result = assess_composite_proof(
            observation,
            CompositeProofPolicy(
                AdapterMode.FULL,
                (ProofKind.PROCESS_BOUND, ProofKind.CONTENT_BOUND),
            ),
        )
        self.assertTrue(manual_result.passed)
        self.assertFalse(full_result.passed)

    def test_canonical_abi_artifacts_match_machine_schema(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / "adapter-abi.schema.json").read_text()
        )
        validator = Draft202012Validator(schema)
        observation = self.observation()
        policy = CompositeProofPolicy(
            AdapterMode.FULL,
            (ProofKind.PROCESS_BOUND, ProofKind.CONTENT_BOUND),
        )
        assessment = assess_composite_proof(observation, policy)
        for artifact in (
            self.manifest().canonical(), self.request().canonical(),
            observation.canonical(), policy.canonical(), assessment.canonical(),
        ):
            validator.validate(artifact)


if __name__ == "__main__":
    unittest.main()
