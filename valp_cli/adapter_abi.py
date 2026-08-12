"""Provider-neutral VALP Adapter ABI 1.0 machine contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping, Optional, Tuple


ABI_VERSION = "1.0"


class AdapterOperation(str, Enum):
    PROBE = "probe"
    SUBMIT = "submit"
    OBSERVE = "observe"
    CANCEL = "cancel"
    RESUME = "resume"
    PROVE = "prove"


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class AdapterStatus(str, Enum):
    ACCEPTED = "accepted"
    WAITING = "waiting"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


class ProofKind(str, Enum):
    PROCESS_BOUND = "process_bound"
    CONTENT_BOUND = "content_bound"
    MANUAL_ATTESTED = "manual_attested"
    TRANSPORT_ONLY = "transport_only"


class AdapterMode(str, Enum):
    FULL = "full"
    REMOTE = "remote"
    MANUAL = "manual"


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _safe_ref(value: object) -> bool:
    if not _nonempty(value) or value.startswith(("/", "\\")) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts)


@dataclass(frozen=True)
class AdapterCapability:
    operation: AdapterOperation
    status: CapabilityStatus
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdapterOperation) or not isinstance(self.status, CapabilityStatus):
            raise ValueError("Adapter capability vocabulary is closed")
        if self.status == CapabilityStatus.UNSUPPORTED and not _nonempty(self.reason):
            raise ValueError("unsupported Adapter capability requires a reason")
        if self.status == CapabilityStatus.SUPPORTED and self.reason is not None:
            raise ValueError("supported Adapter capability cannot carry an unsupported reason")

    def canonical(self) -> Mapping[str, object]:
        value = {"operation": self.operation.value, "status": self.status.value}
        if self.reason is not None:
            value["reason"] = self.reason
        return value


@dataclass(frozen=True)
class AdapterManifest:
    adapter_id: str
    adapter_class: str
    abi_version: str
    capabilities: Tuple[AdapterCapability, ...]

    def __post_init__(self) -> None:
        capabilities = tuple(self.capabilities)
        if not _nonempty(self.adapter_id) or not _nonempty(self.adapter_class):
            raise ValueError("Adapter identity and class are required")
        if self.abi_version != ABI_VERSION:
            raise ValueError("unsupported Adapter ABI version")
        operations = [item.operation for item in capabilities if isinstance(item, AdapterCapability)]
        if len(capabilities) != len(AdapterOperation) or set(operations) != set(AdapterOperation):
            raise ValueError("Adapter manifest requires one capability per operation")
        object.__setattr__(
            self,
            "capabilities",
            tuple(sorted(capabilities, key=lambda item: list(AdapterOperation).index(item.operation))),
        )

    def capability(self, operation: AdapterOperation) -> AdapterCapability:
        return next(item for item in self.capabilities if item.operation == operation)

    def canonical(self) -> Mapping[str, object]:
        return {
            "schema_version": "valp-adapter-manifest.v1",
            "adapter_id": self.adapter_id,
            "adapter_class": self.adapter_class,
            "abi_version": self.abi_version,
            "capabilities": [item.canonical() for item in self.capabilities],
        }


@dataclass(frozen=True)
class AdapterRequest:
    request_id: str
    operation: AdapterOperation
    installation_id: str
    leader_epoch: int
    task_id: str
    work_item_id: str
    attempt_id: str
    dispatch_id: str
    dispatch_generation: int
    payload_digest: str
    expected_evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        refs = tuple(self.expected_evidence_refs)
        if (
            not all(_nonempty(value) for value in (
                self.request_id, self.installation_id, self.task_id, self.work_item_id,
                self.attempt_id, self.dispatch_id,
            ))
            or not isinstance(self.operation, AdapterOperation)
            or not isinstance(self.leader_epoch, int) or isinstance(self.leader_epoch, bool) or self.leader_epoch < 0
            or not isinstance(self.dispatch_generation, int) or isinstance(self.dispatch_generation, bool) or self.dispatch_generation < 0
            or not _digest(self.payload_digest)
            or len(refs) != len(set(refs))
            or not all(_safe_ref(ref) for ref in refs)
        ):
            raise ValueError("invalid Adapter request")
        object.__setattr__(self, "expected_evidence_refs", refs)

    def canonical(self) -> Mapping[str, object]:
        return {
            "schema_version": "valp-adapter-request.v1",
            "request_id": self.request_id,
            "operation": self.operation.value,
            "installation_id": self.installation_id,
            "leader_epoch": self.leader_epoch,
            "task_id": self.task_id,
            "work_item_id": self.work_item_id,
            "attempt_id": self.attempt_id,
            "dispatch_id": self.dispatch_id,
            "dispatch_generation": self.dispatch_generation,
            "payload_digest": self.payload_digest,
            "expected_evidence_refs": list(self.expected_evidence_refs),
        }


@dataclass(frozen=True)
class ProvenanceSegment:
    segment_id: str
    sequence: int
    adapter_id: str
    abi_version: str
    input_identity: str
    input_digest: str
    output_identity: str
    output_digest: str
    proof_kind: ProofKind
    evidence_refs: Tuple[str, ...]
    acknowledged: bool
    failure_code: Optional[str] = None

    def __post_init__(self) -> None:
        refs = tuple(self.evidence_refs)
        if (
            not all(_nonempty(value) for value in (
                self.segment_id, self.adapter_id, self.input_identity, self.output_identity,
            ))
            or not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0
            or self.abi_version != ABI_VERSION
            or not _digest(self.input_digest) or not _digest(self.output_digest)
            or not isinstance(self.proof_kind, ProofKind)
            or not refs or len(refs) != len(set(refs)) or not all(_safe_ref(ref) for ref in refs)
            or not isinstance(self.acknowledged, bool)
            or (self.acknowledged and self.failure_code is not None)
            or (not self.acknowledged and not _nonempty(self.failure_code))
        ):
            raise ValueError("invalid Adapter provenance segment")
        object.__setattr__(self, "evidence_refs", refs)

    def canonical(self) -> Mapping[str, object]:
        value = {
            "segment_id": self.segment_id,
            "sequence": self.sequence,
            "adapter_id": self.adapter_id,
            "abi_version": self.abi_version,
            "input_identity": self.input_identity,
            "input_digest": self.input_digest,
            "output_identity": self.output_identity,
            "output_digest": self.output_digest,
            "proof_kind": self.proof_kind.value,
            "evidence_refs": list(self.evidence_refs),
            "acknowledged": self.acknowledged,
        }
        if self.failure_code is not None:
            value["failure_code"] = self.failure_code
        return value


@dataclass(frozen=True)
class AdapterObservation:
    observation_id: str
    request: AdapterRequest
    sequence: int
    status: AdapterStatus
    runtime_identity: Optional[str]
    provenance: Tuple[ProvenanceSegment, ...]
    evidence_refs: Tuple[str, ...]
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        segments = tuple(self.provenance)
        refs = tuple(self.evidence_refs)
        if (
            not _nonempty(self.observation_id)
            or not isinstance(self.request, AdapterRequest)
            or not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0
            or not isinstance(self.status, AdapterStatus)
            or (self.runtime_identity is not None and not _nonempty(self.runtime_identity))
            or len(refs) != len(set(refs)) or not all(_safe_ref(ref) for ref in refs)
            or len({item.segment_id for item in segments}) != len(segments)
        ):
            raise ValueError("invalid Adapter observation")
        for index, segment in enumerate(segments):
            if not isinstance(segment, ProvenanceSegment) or segment.sequence != index:
                raise ValueError("Adapter provenance sequence is not contiguous")
            if index and (
                segments[index - 1].output_identity != segment.input_identity
                or segments[index - 1].output_digest != segment.input_digest
            ):
                raise ValueError("Adapter provenance chain is discontinuous")
        object.__setattr__(self, "provenance", segments)
        object.__setattr__(self, "evidence_refs", refs)

    def canonical(self) -> Mapping[str, object]:
        value = {
            "schema_version": "valp-adapter-observation.v1",
            "observation_id": self.observation_id,
            "request": self.request.canonical(),
            "sequence": self.sequence,
            "status": self.status.value,
            "provenance": [item.canonical() for item in self.provenance],
            "evidence_refs": list(self.evidence_refs),
        }
        if self.runtime_identity is not None:
            value["runtime_identity"] = self.runtime_identity
        if self.reason is not None:
            value["reason"] = self.reason
        return value


@dataclass(frozen=True)
class CompositeProofPolicy:
    mode: AdapterMode
    required_proof_kinds: Tuple[ProofKind, ...]
    remote_issuer: Optional[str] = None
    remote_host: Optional[str] = None

    def __post_init__(self) -> None:
        kinds = tuple(self.required_proof_kinds)
        if (
            not isinstance(self.mode, AdapterMode)
            or not kinds or len(kinds) != len(set(kinds))
            or not all(isinstance(kind, ProofKind) for kind in kinds)
            or (self.mode == AdapterMode.REMOTE and not all(
                _nonempty(value) for value in (self.remote_issuer, self.remote_host)
            ))
            or (self.mode != AdapterMode.REMOTE and (
                self.remote_issuer is not None or self.remote_host is not None
            ))
        ):
            raise ValueError("invalid Composite proof policy")
        object.__setattr__(self, "required_proof_kinds", kinds)

    def canonical(self) -> Mapping[str, object]:
        value = {
            "schema_version": "valp-composite-proof-policy.v1",
            "mode": self.mode.value,
            "required_proof_kinds": [kind.value for kind in self.required_proof_kinds],
        }
        if self.remote_issuer is not None:
            value["remote_issuer"] = self.remote_issuer
            value["remote_host"] = self.remote_host
        return value


@dataclass(frozen=True)
class CompositeProofAssessment:
    passed: bool
    reasons: Tuple[str, ...]

    def canonical(self) -> Mapping[str, object]:
        return {
            "schema_version": "valp-composite-proof-assessment.v1",
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


def validate_observation(manifest: AdapterManifest, observation: AdapterObservation) -> None:
    if not isinstance(manifest, AdapterManifest) or not isinstance(observation, AdapterObservation):
        raise ValueError("manifest and observation are required")
    capability = manifest.capability(observation.request.operation)
    if capability.status == CapabilityStatus.UNSUPPORTED:
        if observation.status != AdapterStatus.UNSUPPORTED or observation.reason != capability.reason:
            raise ValueError("unsupported capability observation mismatch")
        if observation.provenance or observation.runtime_identity is not None:
            raise ValueError("unsupported operation cannot claim runtime proof")
    elif observation.status == AdapterStatus.UNSUPPORTED:
        raise ValueError("supported capability cannot return unsupported")
    if observation.provenance and observation.provenance[-1].adapter_id != manifest.adapter_id:
        raise ValueError("final provenance segment must belong to observing Adapter")


def assess_composite_proof(
    observation: AdapterObservation,
    policy: CompositeProofPolicy,
) -> CompositeProofAssessment:
    if not isinstance(observation, AdapterObservation) or not isinstance(policy, CompositeProofPolicy):
        raise ValueError("observation and policy are required")
    reasons = []
    kinds = {segment.proof_kind for segment in observation.provenance}
    for required in policy.required_proof_kinds:
        if required not in kinds:
            reasons.append(f"missing_proof:{required.value}")
    if any(segment.proof_kind == ProofKind.TRANSPORT_ONLY for segment in observation.provenance):
        reasons.append("transport_only_segment")
    if any(not segment.acknowledged for segment in observation.provenance):
        reasons.append("unacknowledged_segment")
    if policy.mode in {AdapterMode.FULL, AdapterMode.REMOTE} and ProofKind.MANUAL_ATTESTED in kinds:
        reasons.append("manual_proof_relabelled")
    if policy.mode == AdapterMode.MANUAL and any(
        kind != ProofKind.MANUAL_ATTESTED for kind in kinds
    ):
        reasons.append("non_manual_proof_in_manual_mode")
    if observation.status == AdapterStatus.COMPLETED and not set(
        observation.request.expected_evidence_refs
    ).issubset(observation.evidence_refs):
        reasons.append("missing_expected_evidence")
    return CompositeProofAssessment(not reasons, tuple(reasons))


__all__ = [
    "ABI_VERSION", "AdapterCapability", "AdapterManifest", "AdapterMode",
    "AdapterObservation", "AdapterOperation", "AdapterRequest", "AdapterStatus",
    "CapabilityStatus", "CompositeProofAssessment", "CompositeProofPolicy",
    "ProofKind", "ProvenanceSegment", "assess_composite_proof",
    "validate_observation",
]
