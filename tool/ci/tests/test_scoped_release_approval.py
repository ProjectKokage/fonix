from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
CI_DIRECTORY = REPOSITORY / "tool/ci"
if str(CI_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(CI_DIRECTORY))

import generate_release_sbom as release_evidence
import validate_scoped_release_approval as approval_validator
import validate_scoped_release_scope as scope_validator


SCRIPT = CI_DIRECTORY / "validate_scoped_release_approval.py"
SCHEMA = REPOSITORY / "templates/ci/scoped_release_approval.schema.json"
SCOPE = REPOSITORY / "release/scoped-pre-1.0-v1.json"
SOURCE_MANIFEST = REPOSITORY / "MANIFEST.sha256"
DART_LOCK = REPOSITORY / "pubspec.lock"
NATIVE_LOCK = REPOSITORY / "native/versions.lock.yaml"
SHERPA_LOCK = REPOSITORY / "templates/android/sherpa_reference_app/pubspec.lock"

CLAIM_BOUNDARY = (
    "This bundle binds candidate evidence and detached approval artifacts only. "
    "It does not itself establish readiness, verify signatures, authorize "
    "publication or distribution, or weaken the global five-platform release gate."
)
EVIDENCE_FIELDS = (
    "targetExecutionRecords",
    "providerAssignmentRecords",
    "finalPackageRecords",
    "sbomRecords",
    "auditRecords",
    "reproducibilityRecords",
    "noticesRecords",
    "signingRecords",
)
SHARED_CATEGORIES = (
    "source-closure",
    "dart-analysis-tests",
    "bindings-reproduction",
    "native-tests-sanitizers",
    "lifecycle-cancellation-stress",
    "android-qnn-contract-tamper",
    "windows-source-cross-build-loader-security",
)
APPROVAL_CATEGORIES = (
    "api-abi",
    "licensing",
    "security",
    "signing",
    "publication",
)
REPORT_KEYS = frozenset(
    {
        "schemaVersion",
        "result",
        "claimStatus",
        "ready",
        "policyId",
        "bundleId",
        "bundleSha256",
        "candidateSubjectSha256",
        "referencedEvidenceSha256",
        "validatorSha256",
        "schemaSha256",
        "scopeValidatorSha256",
        "sourceManifestValidatorSha256",
        "scopeValidationRecordSha256",
        "sourceBaseline",
        "compositionIds",
        "requiredEvidenceSlotIds",
        "satisfiedEvidenceSlotIds",
        "missingEvidenceSlotIds",
        "requiredSharedRegressionCategoryIds",
        "presentSharedRegressionCategoryIds",
        "missingSharedRegressionCategoryIds",
        "requiredApprovalCategoryIds",
        "approvalResults",
        "approvedApprovalCategoryIds",
        "rejectedApprovalCategoryIds",
        "missingApprovalCategoryIds",
        "evidenceReferenceCount",
        "evidenceBytes",
        "blockerIds",
        "signatureVerificationMode",
        "claimBoundary",
    }
)

COMPOSITION_CONTRACTS = (
    {
        "id": "ios-arm64-device-cpu-linked",
        "target": {
            "os": "ios",
            "architecture": "arm64",
            "variant": "device",
            "flavor": "cpu",
            "minimumOs": "15.1",
        },
        "runtime": {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-ios-arm64-device-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "linked",
        },
    },
    {
        "id": "macos-arm64-default-cpu-bundled",
        "target": {
            "os": "macos",
            "architecture": "arm64",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "14.0",
        },
        "runtime": {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-macos-arm64-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
    },
    {
        "id": "android-arm64-v8a-default-cpu-bundled",
        "target": {
            "os": "android",
            "architecture": "arm64-v8a",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "24",
        },
        "runtime": {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-android-arm64-v8a-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
    },
    {
        "id": "android-arm64-v8a-default-cpu-sherpa-process",
        "target": {
            "os": "android",
            "architecture": "arm64-v8a",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "24",
        },
        "runtime": {
            "kind": "android-sherpa-process",
            "artifactId": None,
            "runtimeOwner": "sherpa",
            "runtimeMode": "process",
            "runtimeIdentity": {
                "ownerPackage": "sherpa_onnx",
                "ownerPackageVersion": "1.13.4",
                "onnxRuntimeVersion": "1.27.0",
            },
        },
    },
    {
        "id": "linux-x86_64-default-cpu-bundled",
        "target": {
            "os": "linux",
            "architecture": "x86_64",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "glibc-2.27",
        },
        "runtime": {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-linux-x86_64-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
    },
)

COMPOSITION_EVIDENCE_INVENTORY = {
    "ios-arm64-device-cpu-linked": {
        "targetExecutionRecords": (
            ("ios-device-target-execution", "application/json"),
        ),
        "providerAssignmentRecords": (
            ("ios-device-cpu-full-assignment", "application/json"),
        ),
        "finalPackageRecords": (
            ("ios-device-release-ipa", "application/zip"),
        ),
        "sbomRecords": (("ios-device-spdx-sbom", "application/spdx+json"),),
        "auditRecords": (
            ("ios-device-final-package-audit", "application/json"),
        ),
        "reproducibilityRecords": (
            ("ios-device-reproducibility", "application/json"),
        ),
        "noticesRecords": (
            ("ios-device-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            ("ios-device-distribution-signing", "application/json"),
        ),
    },
    "macos-arm64-default-cpu-bundled": {
        "targetExecutionRecords": (
            ("macos-arm64-clean-machine-execution", "application/json"),
        ),
        "providerAssignmentRecords": (
            ("macos-arm64-cpu-full-assignment", "application/json"),
        ),
        "finalPackageRecords": (
            ("macos-arm64-release-archive", "application/zip"),
        ),
        "sbomRecords": (
            ("macos-arm64-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            ("macos-arm64-final-package-audit", "application/json"),
        ),
        "reproducibilityRecords": (
            ("macos-arm64-reproducibility", "application/json"),
        ),
        "noticesRecords": (
            ("macos-arm64-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            ("macos-arm64-distribution-signing", "application/json"),
        ),
    },
    "android-arm64-v8a-default-cpu-bundled": {
        "targetExecutionRecords": (
            ("android-bundled-api24-device-execution", "application/json"),
            (
                "android-bundled-aab-split-install-execution",
                "application/json",
            ),
        ),
        "providerAssignmentRecords": (
            ("android-bundled-cpu-full-assignment", "application/json"),
        ),
        "finalPackageRecords": (
            (
                "android-bundled-release-apk",
                "application/vnd.android.package-archive",
            ),
            ("android-bundled-release-aab", "application/zip"),
        ),
        "sbomRecords": (
            ("android-bundled-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            ("android-bundled-apk-audit", "application/json"),
            ("android-bundled-aab-audit", "application/json"),
        ),
        "reproducibilityRecords": (
            ("android-bundled-reproducibility", "application/json"),
        ),
        "noticesRecords": (
            ("android-bundled-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            ("android-bundled-distribution-signing", "application/json"),
        ),
    },
    "android-arm64-v8a-default-cpu-sherpa-process": {
        "targetExecutionRecords": (
            ("android-sherpa-dart-first-4k-target", "application/json"),
            ("android-sherpa-sherpa-first-4k-target", "application/json"),
            ("android-sherpa-dart-first-16k-target", "application/json"),
            ("android-sherpa-sherpa-first-16k-target", "application/json"),
        ),
        "providerAssignmentRecords": (
            (
                "android-sherpa-cpu-full-assignment-aggregate",
                "application/json",
            ),
        ),
        "finalPackageRecords": (
            (
                "android-sherpa-release-apk",
                "application/vnd.android.package-archive",
            ),
            ("android-sherpa-release-aab", "application/zip"),
        ),
        "sbomRecords": (
            ("android-sherpa-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            ("android-sherpa-apk-aab-static-audit", "application/json"),
            (
                "android-sherpa-four-record-validation-aggregate",
                "application/json",
            ),
        ),
        "reproducibilityRecords": (
            ("android-sherpa-reproducibility", "application/json"),
        ),
        "noticesRecords": (
            ("android-sherpa-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            ("android-sherpa-distribution-signing", "application/json"),
        ),
    },
    "linux-x86_64-default-cpu-bundled": {
        "targetExecutionRecords": (
            ("linux-x86_64-clean-machine-execution", "application/json"),
        ),
        "providerAssignmentRecords": (
            ("linux-x86_64-cpu-full-assignment", "application/json"),
        ),
        "finalPackageRecords": (
            ("linux-x86_64-release-archive", "application/gzip"),
        ),
        "sbomRecords": (
            ("linux-x86_64-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            ("linux-x86_64-final-package-audit", "application/json"),
        ),
        "reproducibilityRecords": (
            ("linux-x86_64-reproducibility", "application/json"),
        ),
        "noticesRecords": (
            ("linux-x86_64-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            ("linux-x86_64-distribution-signing", "application/json"),
        ),
    },
}


def _required_evidence_slot_ids() -> tuple[str, ...]:
    composition_slots = tuple(
        f"composition:{composition['id']}:{field}:{identifier}"
        for composition in COMPOSITION_CONTRACTS
        for field in EVIDENCE_FIELDS
        for identifier, _media_type in COMPOSITION_EVIDENCE_INVENTORY[
            composition["id"]
        ][field]
    )
    shared_slots = tuple(
        f"shared:{category}:shared-{category}" for category in SHARED_CATEGORIES
    )
    return composition_slots + shared_slots


REQUIRED_EVIDENCE_SLOT_IDS = _required_evidence_slot_ids()


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_candidate_statement(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _source_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class _ApprovalFixture:
    def __init__(self, root: Path, *, ready: bool) -> None:
        self.root = root.resolve()
        self.evidence_root = self.root / "evidence"
        self.evidence_root.mkdir(parents=True)
        self.bundle_path = self.root / "candidate-bundle.json"
        self.output_index = 0

        scope_record = scope_validator.validate_scope(REPOSITORY, SCOPE)
        scope_reference = self.add_bytes(
            "scope-validation",
            "scope/scope-validation.json",
            _canonical_json(scope_record),
            "application/json",
        )
        source = {
            "sourceRevision": _source_revision(),
            "sourceArchive": self.add_bytes(
                "source-archive",
                "source/fonix-source.tar.gz",
                b"synthetic closed source archive\n",
                "application/gzip",
            ),
            "sourceManifest": self.add_bytes(
                "source-manifest",
                "MANIFEST.sha256",
                SOURCE_MANIFEST.read_bytes(),
                "text/plain",
            ),
            "dartPubspecLock": self.add_bytes(
                "dart-pubspec-lock",
                "pubspec.lock",
                DART_LOCK.read_bytes(),
                "text/plain",
            ),
            "nativeLock": self.add_bytes(
                "native-lock",
                "native/versions.lock.yaml",
                NATIVE_LOCK.read_bytes(),
                "application/json",
            ),
            "sherpaPubspecLock": self.add_bytes(
                "sherpa-pubspec-lock",
                "templates/android/sherpa_reference_app/pubspec.lock",
                SHERPA_LOCK.read_bytes(),
                "text/plain",
            ),
            "packageName": "fonix",
            "packageVersion": "0.1.0-dev.1",
        }
        compositions = []
        for contract in COMPOSITION_CONTRACTS:
            evidence: dict[str, list[dict[str, object]]] = {}
            for field in EVIDENCE_FIELDS:
                evidence[field] = (
                    [
                        self._composition_evidence(
                            contract["id"], field, identifier, media_type
                        )
                        for identifier, media_type in COMPOSITION_EVIDENCE_INVENTORY[
                            contract["id"]
                        ][field]
                    ]
                    if ready
                    else []
                )
            compositions.append(
                {
                    **copy.deepcopy(contract),
                    "provider": {
                        "id": "cpu",
                        "requirement": "full-assignment",
                    },
                    "evidence": evidence,
                }
            )
        shared = []
        if ready:
            for category in SHARED_CATEGORIES:
                shared.append(
                    {
                        "category": category,
                        "records": [self._shared_evidence(category)],
                    }
                )
        self.candidate_statement: dict[str, object] = {
            "bundleId": "fonix-scoped-pre-1.0-candidate-v1",
            "scope": {
                "policyId": "scoped-pre-1.0-cpu-v1",
                "scopePath": "release/scoped-pre-1.0-v1.json",
                "scopeSha256": _sha256(SCOPE.read_bytes()),
                "validationRecord": scope_reference,
            },
            "source": source,
            "compositions": compositions,
            "sharedRegressionEvidence": shared,
        }
        self.bundle: dict[str, object] = {
            "schemaVersion": 1,
            "claimStatus": "candidate-evidence-only",
            "candidateStatement": self.candidate_statement,
            "candidateSubjectSha256": self.subject_sha256(),
            "detachedApprovals": [],
            "claimBoundary": CLAIM_BOUNDARY,
        }
        if ready:
            self.rebuild_approvals()
        self.write_bundle(recompute_subject=False)

    def add_bytes(
        self,
        identifier: str,
        relative_path: str,
        contents: bytes,
        media_type: str,
    ) -> dict[str, object]:
        path = self.evidence_root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return {
            "id": identifier,
            "path": relative_path,
            "sha256": _sha256(contents),
            "sizeBytes": len(contents),
            "mediaType": media_type,
        }

    def subject_sha256(self) -> str:
        return _sha256(_canonical_candidate_statement(self.candidate_statement))

    def write_bundle(self, *, recompute_subject: bool = True) -> str:
        if recompute_subject:
            self.bundle["candidateSubjectSha256"] = self.subject_sha256()
        raw = _canonical_json(self.bundle)
        self.bundle_path.write_bytes(raw)
        return _sha256(raw)

    def rebind_candidate_and_approvals(
        self,
        *,
        decisions: dict[str, str] | None = None,
    ) -> None:
        self.bundle["candidateSubjectSha256"] = self.subject_sha256()
        self.rebuild_approvals(decisions=decisions)
        self.write_bundle(recompute_subject=False)

    def rebuild_approvals(
        self,
        *,
        decisions: dict[str, str] | None = None,
    ) -> None:
        approvals = []
        subject = self.subject_sha256()
        for category in APPROVAL_CATEGORIES:
            approvals.append(
                self._approval_entry(
                    category,
                    subject,
                    decision=(decisions or {}).get(category, "approved"),
                )
            )
        self.bundle["detachedApprovals"] = approvals

    def _composition_evidence(
        self,
        composition_id: str,
        field: str,
        identifier: str,
        media_type: str,
    ) -> dict[str, object]:
        if media_type in {
            "application/zip",
            "application/vnd.android.package-archive",
        }:
            contents = b"PK\x03\x04synthetic final package\n"
        elif media_type == "application/gzip":
            contents = b"\x1f\x8b\x08synthetic final package\n"
        elif media_type == "application/spdx+json":
            contents = _canonical_json(
                {
                    "spdxVersion": "SPDX-2.3",
                    "compositionId": composition_id,
                    "evidenceId": identifier,
                }
            )
        elif media_type == "text/plain":
            contents = f"notices for {composition_id}\n".encode("utf-8")
        else:
            contents = _canonical_json(
                {
                    "schemaVersion": 1,
                    "result": "passed",
                    "compositionId": composition_id,
                    "evidenceKind": field,
                    "evidenceId": identifier,
                }
            )
        return self.add_bytes(
            identifier,
            f"compositions/{composition_id}/{field}/{identifier}",
            contents,
            media_type,
        )

    def _shared_evidence(self, category: str) -> dict[str, object]:
        return self.add_bytes(
            f"shared-{category}",
            f"shared/{category}.json",
            _canonical_json(
                {"schemaVersion": 1, "result": "passed", "category": category}
            ),
            "application/json",
        )

    def _approval_entry(
        self,
        category: str,
        subject: str,
        *,
        decision: str,
    ) -> dict[str, object]:
        approver_id = f"fonix-{category}-authority"
        key_id = f"fonix-{category}-key-v1"
        algorithm = "ed25519"
        statement = {
            "schemaVersion": 1,
            "category": category,
            "candidateSubjectSha256": subject,
            "decision": decision,
            "decidedAt": "2026-08-08T00:00:00Z",
            "approverId": approver_id,
            "keyId": key_id,
            "signatureAlgorithm": algorithm,
        }
        statement_raw = _canonical_json(statement)
        signature_raw = f"detached signature for {category}\n".encode("ascii")
        statement_reference = self.add_bytes(
            f"approval-{category}-statement",
            f"approvals/{category}/statement.json",
            statement_raw,
            "application/json",
        )
        signature_reference = self.add_bytes(
            f"approval-{category}-signature",
            f"approvals/{category}/signature.bin",
            signature_raw,
            "application/octet-stream",
        )
        receipt = {
            "schemaVersion": 1,
            "category": category,
            "candidateSubjectSha256": subject,
            "approvalStatementSha256": _sha256(statement_raw),
            "signatureSha256": _sha256(signature_raw),
            "approverId": approver_id,
            "keyId": key_id,
            "signatureAlgorithm": algorithm,
            "verified": True,
            "verifiedAt": "2026-08-08T00:01:00Z",
            "verifierId": "fonix-external-signature-verifier-v1",
        }
        receipt_reference = self.add_bytes(
            f"approval-{category}-verification",
            f"approvals/{category}/verification.json",
            _canonical_json(receipt),
            "application/json",
        )
        return {
            "category": category,
            "approverId": approver_id,
            "keyId": key_id,
            "signatureAlgorithm": algorithm,
            "statement": statement_reference,
            "signature": signature_reference,
            "verificationReceipt": receipt_reference,
        }

    def composition(self, identifier: str) -> dict[str, object]:
        return next(
            value
            for value in self.candidate_statement["compositions"]
            if value["id"] == identifier
        )

    def approval(self, category: str) -> dict[str, object]:
        return next(
            value
            for value in self.bundle["detachedApprovals"]
            if value["category"] == category
        )

    def referenced_path(self, reference: dict[str, object]) -> Path:
        return self.evidence_root.joinpath(*str(reference["path"]).split("/"))

    def replace_reference_contents(
        self,
        reference: dict[str, object],
        contents: bytes,
    ) -> None:
        self.referenced_path(reference).write_bytes(contents)
        reference["sha256"] = _sha256(contents)
        reference["sizeBytes"] = len(contents)


class ScopedReleaseApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output_index = 0

    def fixture(self, *, ready: bool) -> _ApprovalFixture:
        self.output_index += 1
        return _ApprovalFixture(self.root / f"fixture-{self.output_index}", ready=ready)

    def run_validator(
        self,
        fixture: _ApprovalFixture,
        *,
        require_ready: bool = False,
        bundle: Path | None = None,
        bundle_sha256: str | None = None,
        evidence_root: Path | None = None,
        output: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        self.output_index += 1
        selected_output = output or self.root / f"approval-{self.output_index}.json"
        selected_bundle = bundle or fixture.bundle_path
        arguments = [
            sys.executable,
            str(SCRIPT),
            "--repository",
            str(REPOSITORY),
            "--scope",
            str(SCOPE),
            "--bundle",
            str(selected_bundle),
            "--bundle-sha256",
            bundle_sha256 or _sha256(selected_bundle.read_bytes()),
            "--evidence-root",
            str(evidence_root or fixture.evidence_root),
            "--output",
            str(selected_output),
        ]
        if require_ready:
            arguments.append("--require-scoped-ready")
        result = subprocess.run(
            arguments,
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        return result, selected_output

    def assert_malformed(
        self,
        fixture: _ApprovalFixture,
        **arguments: object,
    ) -> subprocess.CompletedProcess[str]:
        requested_output = arguments.get("output")
        output_before: bytes | None = None
        if isinstance(requested_output, Path) and os.path.lexists(requested_output):
            output_before = requested_output.read_bytes()
        result, output = self.run_validator(fixture, **arguments)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("scoped release approval error:", result.stderr)
        if output_before is None:
            self.assertFalse(os.path.lexists(output))
        else:
            self.assertEqual(output.read_bytes(), output_before)
        return result

    def test_no_evidence_is_deterministic_not_ready_and_flag_fails_closed(self) -> None:
        first = self.fixture(ready=False)
        first_result, first_output = self.run_validator(first)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        first_report = json.loads(first_output.read_bytes())
        self.assertEqual(set(first_report), REPORT_KEYS)
        self.assertEqual(first_report["result"], "blocked")
        self.assertEqual(first_report["claimStatus"], "scoped-release-readiness")
        self.assertIs(first_report["ready"], False)
        self.assertEqual(first_report["evidenceReferenceCount"], 6)
        expected_blockers = [
            f"missing-evidence:{slot}" for slot in REQUIRED_EVIDENCE_SLOT_IDS
        ]
        expected_blockers.extend(
            f"missing-approval:{category}" for category in APPROVAL_CATEGORIES
        )
        self.assertEqual(first_report["blockerIds"], expected_blockers)
        self.assertNotIn(str(self.root), first_output.read_text(encoding="utf-8"))

        second = self.fixture(ready=False)
        second_result, second_output = self.run_validator(second)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        self.assertEqual(first_output.read_bytes(), second_output.read_bytes())

        gated = self.fixture(ready=False)
        gated_result, gated_output = self.run_validator(gated, require_ready=True)
        self.assertNotEqual(gated_result.returncode, 0)
        self.assertTrue(gated_output.is_file())
        self.assertEqual(first_output.read_bytes(), gated_output.read_bytes())

    def test_exact_external_bundle_is_ready_with_scoped_flag(self) -> None:
        fixture = self.fixture(ready=True)
        ordinary, ordinary_output = self.run_validator(fixture)
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        ordinary_report = json.loads(ordinary_output.read_bytes())
        self.assertEqual(set(ordinary_report), REPORT_KEYS)
        self.assertEqual(ordinary_report["result"], "ready")
        self.assertEqual(
            ordinary_report["signatureVerificationMode"],
            "external-verification-receipts-only",
        )
        self.assertIs(ordinary_report["ready"], True)
        self.assertEqual(ordinary_report["blockerIds"], [])
        self.assertEqual(
            ordinary_report["candidateSubjectSha256"], fixture.subject_sha256()
        )
        self.assertEqual(
            ordinary_report["requiredEvidenceSlotIds"],
            list(REQUIRED_EVIDENCE_SLOT_IDS),
        )
        self.assertEqual(ordinary_report["evidenceReferenceCount"], 76)

        gated, gated_output = self.run_validator(fixture, require_ready=True)
        self.assertEqual(gated.returncode, 0, gated.stderr)
        self.assertEqual(ordinary_output.read_bytes(), gated_output.read_bytes())

        independent_fixture = self.fixture(ready=True)
        independent_result, independent_output = self.run_validator(
            independent_fixture
        )
        self.assertEqual(independent_result.returncode, 0, independent_result.stderr)
        self.assertEqual(ordinary_output.read_bytes(), independent_output.read_bytes())
        self.assertNotIn(
            str(self.root), independent_output.read_text(encoding="utf-8")
        )

    def test_git_revision_binding_ignores_hostile_environment(self) -> None:
        hostile_bin = self.root / "hostile-bin"
        hostile_bin.mkdir()
        fake_git = hostile_bin / "git"
        fake_git.write_text(
            "#!/bin/sh\nprintf '0000000000000000000000000000000000000000\\n'\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        hostile_git_directory = self.root / "hostile-git-dir"
        hostile_git_directory.mkdir()
        hostile_work_tree = self.root / "hostile-work-tree"
        hostile_work_tree.mkdir()

        cases = (
            {"PATH": str(hostile_bin)},
            {
                "GIT_DIR": str(hostile_git_directory),
                "GIT_WORK_TREE": str(hostile_work_tree),
            },
            {
                "PATH": str(hostile_bin),
                "GIT_DIR": str(hostile_git_directory),
                "GIT_WORK_TREE": str(hostile_work_tree),
            },
        )
        for index, hostile_values in enumerate(cases):
            with self.subTest(case=index):
                fixture = self.fixture(ready=True)
                environment = os.environ.copy()
                environment.update(hostile_values)
                result, output = self.run_validator(
                    fixture, environment=environment
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIs(json.loads(output.read_bytes())["ready"], True)

    def test_schema_exactly_matches_fixture_and_validator_contracts(self) -> None:
        def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate key: {key}")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON constant: {value}")

        with self.assertRaisesRegex(ValueError, "duplicate key"):
            json.loads('{"key":1,"key":2}', object_pairs_hook=strict_object)
        schema = json.loads(
            SCHEMA.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
        definitions = schema["$defs"]

        identity_schema = definitions["identityToken"]
        identity_pattern = re.compile(identity_schema["pattern"])
        self.assertIsNotNone(identity_pattern.fullmatch("fonix-security-key-v1"))
        self.assertIsNotNone(identity_pattern.fullmatch("a" * 128))
        self.assertIsNone(identity_pattern.fullmatch("a" * 129))
        for unsafe_identity in (
            "C:/Users/alice/private-key",
            "authority/private-key",
            "authority\\private-key",
        ):
            with self.subTest(identity=unsafe_identity):
                self.assertIsNone(identity_pattern.fullmatch(unsafe_identity))

        def reference_contract(reference: str) -> tuple[str, str]:
            definition_name = reference.removeprefix("#/$defs/")
            pending = [definitions[definition_name]]
            identifier: str | None = None
            media_type: str | None = None
            while pending:
                value = pending.pop()
                properties = value.get("properties", {})
                identifier = properties.get("id", {}).get("const", identifier)
                media_type = properties.get("mediaType", {}).get(
                    "const", media_type
                )
                for part in value.get("allOf", []):
                    if "$ref" in part:
                        pending.append(
                            definitions[part["$ref"].removeprefix("#/$defs/")]
                        )
                    else:
                        pending.append(part)
            self.assertIsNotNone(identifier)
            self.assertIsNotNone(media_type)
            return identifier, media_type

        composition_definition_names = (
            "iosComposition",
            "macosComposition",
            "androidBundledComposition",
            "androidSherpaComposition",
            "linuxComposition",
        )
        fixture = self.fixture(ready=True)
        schema_composition_records: list[tuple[str, str, str, str]] = []
        fixture_composition_records: list[tuple[str, str, str, str]] = []
        validator_composition_records: list[tuple[str, str, str, str]] = []
        for composition, definition_name in zip(
            COMPOSITION_CONTRACTS, composition_definition_names
        ):
            composition_id = composition["id"]
            definition = definitions[definition_name]
            evidence_reference = definition["allOf"][1]["properties"][
                "evidence"
            ]["$ref"]
            evidence_definition = definitions[
                evidence_reference.removeprefix("#/$defs/")
            ]["allOf"][1]["properties"]
            fixture_composition = fixture.composition(composition_id)
            validator_evidence = (
                approval_validator.COMPOSITION_EVIDENCE_CONTRACTS[composition_id]
            )
            for field in EVIDENCE_FIELDS:
                items = evidence_definition[field]["items"]
                references = (
                    [items["$ref"]]
                    if "$ref" in items
                    else [item["$ref"] for item in items["oneOf"]]
                )
                schema_composition_records.extend(
                    (composition_id, field, *reference_contract(reference))
                    for reference in references
                )
                fixture_composition_records.extend(
                    (
                        composition_id,
                        field,
                        record["id"],
                        record["mediaType"],
                    )
                    for record in fixture_composition["evidence"][field]
                )
                validator_composition_records.extend(
                    (
                        composition_id,
                        field,
                        record.identifier,
                        record.media_type,
                    )
                    for record in validator_evidence[field]
                )

        self.assertEqual(len(schema_composition_records), 48)
        self.assertEqual(schema_composition_records, fixture_composition_records)
        self.assertEqual(schema_composition_records, validator_composition_records)

        shared_definition_names = (
            "sourceClosureRegression",
            "dartRegression",
            "bindingsRegression",
            "nativeRegression",
            "lifecycleRegression",
            "qnnRegression",
            "windowsRegression",
        )
        schema_shared_records = []
        for category, definition_name in zip(
            SHARED_CATEGORIES, shared_definition_names
        ):
            reference = definitions[definition_name]["allOf"][1]["properties"][
                "records"
            ]["items"]["$ref"]
            schema_shared_records.append((category, *reference_contract(reference)))
        fixture_shared_records = [
            (
                entry["category"],
                entry["records"][0]["id"],
                entry["records"][0]["mediaType"],
            )
            for entry in fixture.candidate_statement["sharedRegressionEvidence"]
        ]
        validator_shared_records = [
            (category, f"shared-{category}", "application/json")
            for category in approval_validator.SHARED_CATEGORIES
        ]
        self.assertEqual(len(schema_shared_records), 7)
        self.assertEqual(schema_shared_records, fixture_shared_records)
        self.assertEqual(schema_shared_records, validator_shared_records)

        approval_definition_names = (
            "apiAbiApproval",
            "licensingApproval",
            "securityApproval",
            "signingApproval",
            "publicationApproval",
        )
        for category, definition_name in zip(
            APPROVAL_CATEGORIES, approval_definition_names
        ):
            signature_reference = definitions[definition_name]["allOf"][1][
                "properties"
            ]["signature"]["$ref"]
            signature_id, signature_media = reference_contract(signature_reference)
            self.assertEqual(signature_id, f"approval-{category}-signature")
            self.assertEqual(signature_media, "application/octet-stream")

    def test_each_composition_evidence_omission_is_a_specific_blocker(self) -> None:
        for composition_contract in COMPOSITION_CONTRACTS:
            for field in EVIDENCE_FIELDS:
                inventory = COMPOSITION_EVIDENCE_INVENTORY[
                    composition_contract["id"]
                ][field]
                for record_index, (identifier, _media_type) in enumerate(inventory):
                    with self.subTest(
                        composition=composition_contract["id"],
                        field=field,
                        record=identifier,
                    ):
                        fixture = self.fixture(ready=True)
                        records = fixture.composition(composition_contract["id"])[
                            "evidence"
                        ][field]
                        records.pop(record_index)
                        fixture.rebind_candidate_and_approvals()
                        result, output = self.run_validator(fixture)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        report = json.loads(output.read_bytes())
                        self.assertIs(report["ready"], False)
                        self.assertIn(
                            "missing-evidence:composition:"
                            f"{composition_contract['id']}:{field}:{identifier}",
                            report["blockerIds"],
                        )

    def test_each_evidence_file_tamper_is_malformed(self) -> None:
        for field in EVIDENCE_FIELDS:
            with self.subTest(field=field):
                fixture = self.fixture(ready=True)
                reference = fixture.composition(COMPOSITION_CONTRACTS[0]["id"])[
                    "evidence"
                ][field][0]
                path = fixture.referenced_path(reference)
                path.write_bytes(path.read_bytes() + b"tamper")
                self.assert_malformed(fixture)

    def test_exact_evidence_inventory_rejects_unknown_duplicate_and_reordering(
        self,
    ) -> None:
        fixture = self.fixture(ready=True)
        unknown = fixture.add_bytes(
            "unknown-evidence-record",
            "compositions/unknown-evidence-record.json",
            _canonical_json({"schemaVersion": 1, "result": "passed"}),
            "application/json",
        )
        fixture.composition(COMPOSITION_CONTRACTS[0]["id"])["evidence"][
            "auditRecords"
        ].append(unknown)
        fixture.rebind_candidate_and_approvals()
        self.assert_malformed(fixture)

        fixture = self.fixture(ready=True)
        records = fixture.composition(COMPOSITION_CONTRACTS[0]["id"])["evidence"][
            "auditRecords"
        ]
        records.append(copy.deepcopy(records[0]))
        fixture.rebind_candidate_and_approvals()
        self.assert_malformed(fixture)

        for composition_contract in COMPOSITION_CONTRACTS:
            for field in EVIDENCE_FIELDS:
                inventory = COMPOSITION_EVIDENCE_INVENTORY[
                    composition_contract["id"]
                ][field]
                if len(inventory) < 2:
                    continue
                with self.subTest(
                    reordered=composition_contract["id"], field=field
                ):
                    fixture = self.fixture(ready=True)
                    records = fixture.composition(composition_contract["id"])[
                        "evidence"
                    ][field]
                    records[0], records[1] = records[1], records[0]
                    fixture.rebind_candidate_and_approvals()
                    self.assert_malformed(fixture)

    def test_each_exact_evidence_media_type_substitution_is_malformed(self) -> None:
        for composition_contract in COMPOSITION_CONTRACTS:
            for field in EVIDENCE_FIELDS:
                inventory = COMPOSITION_EVIDENCE_INVENTORY[
                    composition_contract["id"]
                ][field]
                for record_index, (identifier, media_type) in enumerate(inventory):
                    with self.subTest(
                        composition=composition_contract["id"],
                        field=field,
                        record=identifier,
                    ):
                        fixture = self.fixture(ready=True)
                        reference = fixture.composition(composition_contract["id"])[
                            "evidence"
                        ][field][record_index]
                        reference["mediaType"] = (
                            "application/json"
                            if media_type != "application/json"
                            else "text/plain"
                        )
                        fixture.rebind_candidate_and_approvals()
                        self.assert_malformed(fixture)

    def test_shared_evidence_requires_exact_singleton_identity_and_media(self) -> None:
        for mutation in ("duplicate", "identifier", "media"):
            with self.subTest(mutation=mutation):
                fixture = self.fixture(ready=True)
                entry = fixture.candidate_statement["sharedRegressionEvidence"][0]
                if mutation == "duplicate":
                    entry["records"].append(copy.deepcopy(entry["records"][0]))
                elif mutation == "identifier":
                    entry["records"][0]["id"] = "unknown-shared-record"
                else:
                    entry["records"][0]["mediaType"] = "text/plain"
                fixture.rebind_candidate_and_approvals()
                self.assert_malformed(fixture)

    def test_shared_and_approval_omissions_are_blockers(self) -> None:
        for category in SHARED_CATEGORIES:
            with self.subTest(shared=category):
                fixture = self.fixture(ready=True)
                fixture.candidate_statement["sharedRegressionEvidence"] = [
                    entry
                    for entry in fixture.candidate_statement[
                        "sharedRegressionEvidence"
                    ]
                    if entry["category"] != category
                ]
                fixture.rebind_candidate_and_approvals()
                result, output = self.run_validator(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(output.read_bytes())
                self.assertIs(report["ready"], False)
                self.assertIn(
                    f"missing-evidence:shared:{category}:shared-{category}",
                    report["blockerIds"],
                )

            with self.subTest(shared_records=category):
                fixture = self.fixture(ready=True)
                entry = next(
                    value
                    for value in fixture.candidate_statement[
                        "sharedRegressionEvidence"
                    ]
                    if value["category"] == category
                )
                entry["records"] = []
                fixture.rebind_candidate_and_approvals()
                result, output = self.run_validator(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(output.read_bytes())
                self.assertIs(report["ready"], False)
                self.assertIn(
                    f"missing-evidence:shared:{category}:shared-{category}",
                    report["blockerIds"],
                )

        for category in APPROVAL_CATEGORIES:
            with self.subTest(approval=category):
                fixture = self.fixture(ready=True)
                fixture.bundle["detachedApprovals"] = [
                    entry
                    for entry in fixture.bundle["detachedApprovals"]
                    if entry["category"] != category
                ]
                fixture.write_bundle(recompute_subject=False)
                result, output = self.run_validator(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(output.read_bytes())
                self.assertIs(report["ready"], False)
                self.assertIn(
                    f"missing-approval:{category}", report["blockerIds"]
                )

    def test_scope_source_and_lock_identity_tamper_is_malformed(self) -> None:
        mutations = (
            lambda fixture: fixture.candidate_statement["scope"].update(
                {"scopeSha256": "0" * 64}
            ),
            lambda fixture: fixture.candidate_statement["scope"].update(
                {"policyId": "other-policy"}
            ),
            lambda fixture: fixture.candidate_statement["source"].update(
                {"packageName": "other"}
            ),
            lambda fixture: fixture.candidate_statement["source"].update(
                {"packageVersion": "0.1.0-dev.2"}
            ),
            lambda fixture: fixture.candidate_statement["source"].update(
                {"sourceRevision": "0" * 40}
            ),
            lambda fixture: fixture.candidate_statement["source"][
                "nativeLock"
            ].update({"sha256": "0" * 64}),
            lambda fixture: fixture.candidate_statement["source"][
                "sherpaPubspecLock"
            ].update({"sha256": "0" * 64}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                fixture = self.fixture(ready=True)
                mutate(fixture)
                fixture.rebind_candidate_and_approvals()
                self.assert_malformed(fixture)

        for field in ("validationRecord",):
            with self.subTest(reference=field):
                fixture = self.fixture(ready=True)
                reference = fixture.candidate_statement["scope"][field]
                path = fixture.referenced_path(reference)
                path.write_bytes(path.read_bytes() + b"tamper")
                self.assert_malformed(fixture)

        for field in (
            "sourceArchive",
            "sourceManifest",
            "dartPubspecLock",
            "nativeLock",
            "sherpaPubspecLock",
        ):
            with self.subTest(source=field):
                fixture = self.fixture(ready=True)
                reference = fixture.candidate_statement["source"][field]
                path = fixture.referenced_path(reference)
                if field == "sourceArchive":
                    path.write_bytes(path.read_bytes() + b"tamper")
                else:
                    fixture.replace_reference_contents(
                        reference,
                        path.read_bytes() + b"tamper",
                    )
                    fixture.rebind_candidate_and_approvals()
                self.assert_malformed(fixture)

    def test_composition_inventory_and_identity_mutations_are_malformed(self) -> None:
        def missing(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"].pop()

        def duplicate(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][-1] = copy.deepcopy(
                fixture.candidate_statement["compositions"][0]
            )

        def reordered(fixture: _ApprovalFixture) -> None:
            compositions = fixture.candidate_statement["compositions"]
            compositions[0], compositions[1] = compositions[1], compositions[0]

        def provider(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][0]["provider"]["id"] = (
                "coreml"
            )

        def target(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][0]["target"]["os"] = (
                "windows"
            )

        def architecture(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][0]["target"][
                "architecture"
            ] = "x64"

        def runtime_mode(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][0]["runtime"][
                "runtimeMode"
            ] = "process"

        def artifact(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][0]["runtime"][
                "artifactId"
            ] = "onnxruntime-1.27.1-linux-x86_64-cpu"

        def qnn(fixture: _ApprovalFixture) -> None:
            fixture.candidate_statement["compositions"][2]["provider"]["id"] = (
                "qnn"
            )

        for name, mutate in (
            ("missing", missing),
            ("duplicate", duplicate),
            ("reordered", reordered),
            ("provider", provider),
            ("target", target),
            ("architecture", architecture),
            ("runtime-mode", runtime_mode),
            ("artifact", artifact),
            ("qnn", qnn),
        ):
            with self.subTest(name=name):
                fixture = self.fixture(ready=True)
                mutate(fixture)
                fixture.rebind_candidate_and_approvals()
                self.assert_malformed(fixture)

    def test_shared_and_approval_duplicates_reordering_and_unknown_are_malformed(
        self,
    ) -> None:
        for inventory in ("shared", "approvals"):
            for mutation in ("duplicate", "reordered", "unknown"):
                with self.subTest(inventory=inventory, mutation=mutation):
                    fixture = self.fixture(ready=True)
                    values = (
                        fixture.candidate_statement["sharedRegressionEvidence"]
                        if inventory == "shared"
                        else fixture.bundle["detachedApprovals"]
                    )
                    if mutation == "duplicate":
                        values[-1] = copy.deepcopy(values[0])
                    elif mutation == "reordered":
                        values[0], values[1] = values[1], values[0]
                    else:
                        values[0]["category"] = "unknown-category"
                    if inventory == "shared":
                        fixture.rebind_candidate_and_approvals()
                    else:
                        fixture.write_bundle(recompute_subject=False)
                    self.assert_malformed(fixture)

    def test_rejected_approval_is_a_blocker_but_detached_tamper_is_malformed(
        self,
    ) -> None:
        fixture = self.fixture(ready=True)
        fixture.rebuild_approvals(decisions={"security": "rejected"})
        fixture.write_bundle(recompute_subject=False)
        result, output = self.run_validator(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_bytes())
        self.assertIs(report["ready"], False)
        self.assertIn("security", "\n".join(report["blockerIds"]))

        mutators = (
            ("category", lambda value: value.update({"category": "licensing"})),
            ("subject", lambda value: value.update({"candidateSubjectSha256": "0" * 64})),
            ("approver", lambda value: value.update({"approverId": "other"})),
            ("key", lambda value: value.update({"keyId": "other-key"})),
            (
                "algorithm",
                lambda value: value.update(
                    {"signatureAlgorithm": "rsa-pss-sha256"}
                ),
            ),
            ("timestamp", lambda value: value.update({"decidedAt": "not-a-time"})),
        )
        for name, mutate in mutators:
            with self.subTest(statement=name):
                fixture = self.fixture(ready=True)
                entry = fixture.approval("security")
                statement_path = fixture.referenced_path(entry["statement"])
                statement = json.loads(statement_path.read_bytes())
                mutate(statement)
                statement_raw = _canonical_json(statement)
                fixture.replace_reference_contents(entry["statement"], statement_raw)
                receipt_path = fixture.referenced_path(entry["verificationReceipt"])
                receipt = json.loads(receipt_path.read_bytes())
                receipt["approvalStatementSha256"] = _sha256(statement_raw)
                for field in (
                    "category",
                    "candidateSubjectSha256",
                    "approverId",
                    "keyId",
                    "signatureAlgorithm",
                ):
                    if field in statement:
                        receipt[field] = statement[field]
                fixture.replace_reference_contents(
                    entry["verificationReceipt"], _canonical_json(receipt)
                )
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

        fixture = self.fixture(ready=True)
        signature = fixture.approval("security")["signature"]
        signature_path = fixture.referenced_path(signature)
        fixture.replace_reference_contents(
            signature, signature_path.read_bytes() + b"tamper"
        )
        fixture.write_bundle(recompute_subject=False)
        self.assert_malformed(fixture)

        fixture = self.fixture(ready=True)
        entry = fixture.approval("security")
        statement_path = fixture.referenced_path(entry["statement"])
        statement = json.loads(statement_path.read_bytes())
        statement["decidedAt"] = "2026-08-08T00:00:01Z"
        fixture.replace_reference_contents(
            entry["statement"], _canonical_json(statement)
        )
        fixture.write_bundle(recompute_subject=False)
        self.assert_malformed(fixture)

    def test_detached_approval_references_have_exact_ids_and_media(self) -> None:
        for reference_field in ("statement", "signature", "verificationReceipt"):
            with self.subTest(reference=reference_field, mutation="identifier"):
                fixture = self.fixture(ready=True)
                reference = fixture.approval("security")[reference_field]
                reference["id"] = "unknown-approval-record"
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

            with self.subTest(reference=reference_field, mutation="media"):
                fixture = self.fixture(ready=True)
                reference = fixture.approval("security")[reference_field]
                reference["mediaType"] = (
                    "application/json"
                    if reference_field == "signature"
                    else "text/plain"
                )
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

    def test_verification_receipt_tamper_is_malformed(self) -> None:
        mutations = (
            lambda value: value.update({"verified": False}),
            lambda value: value.update({"signatureSha256": "0" * 64}),
            lambda value: value.update({"approvalStatementSha256": "0" * 64}),
            lambda value: value.update({"verifiedAt": "not-a-time"}),
            lambda value: value.update(
                {"verifiedAt": "2026-08-07T23:59:59Z"}
            ),
            lambda value: value.update({"verifierId": "\u001bverifier"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                fixture = self.fixture(ready=True)
                entry = fixture.approval("publication")
                path = fixture.referenced_path(entry["verificationReceipt"])
                receipt = json.loads(path.read_bytes())
                mutate(receipt)
                fixture.replace_reference_contents(
                    entry["verificationReceipt"], _canonical_json(receipt)
                )
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

    def test_path_like_approval_identities_are_malformed(self) -> None:
        path_like_identity = "C:/Users/alice/private-key"
        for identity_field in ("approverId", "keyId", "verifierId"):
            with self.subTest(identity=identity_field):
                fixture = self.fixture(ready=True)
                entry = fixture.approval("security")
                statement_path = fixture.referenced_path(entry["statement"])
                statement = json.loads(statement_path.read_bytes())
                receipt_path = fixture.referenced_path(
                    entry["verificationReceipt"]
                )
                receipt = json.loads(receipt_path.read_bytes())

                if identity_field in {"approverId", "keyId"}:
                    entry[identity_field] = path_like_identity
                    statement[identity_field] = path_like_identity
                    receipt[identity_field] = path_like_identity
                    fixture.replace_reference_contents(
                        entry["statement"], _canonical_json(statement)
                    )
                    receipt["approvalStatementSha256"] = entry["statement"][
                        "sha256"
                    ]
                else:
                    receipt["verifierId"] = path_like_identity

                fixture.replace_reference_contents(
                    entry["verificationReceipt"], _canonical_json(receipt)
                )
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

    def test_bundle_digest_and_strict_json_fail_closed(self) -> None:
        fixture = self.fixture(ready=True)
        self.assert_malformed(fixture, bundle_sha256="0" * 64)

        for field, value in (
            ("candidateSubjectSha256", "0" * 64),
            ("claimStatus", "release-ready"),
            ("claimBoundary", "approval implies publication"),
        ):
            with self.subTest(field=field):
                fixture = self.fixture(ready=True)
                fixture.bundle[field] = value
                fixture.write_bundle(recompute_subject=False)
                self.assert_malformed(fixture)

        fixture = self.fixture(ready=True)
        fixture.bundle["unknown"] = True
        fixture.write_bundle(recompute_subject=False)
        self.assert_malformed(fixture)

        raw_cases = (
            b'{"schemaVersion":1,"schemaVersion":1}\n',
            b'{"schemaVersion":NaN}\n',
            (b'{"nested":' * 64) + b"null" + (b"}" * 64),
            br'{"schemaVersion":1,"claimStatus":"\ud800"}' + b"\n",
        )
        for index, raw in enumerate(raw_cases):
            with self.subTest(index=index):
                fixture = self.fixture(ready=True)
                fixture.bundle_path.write_bytes(raw)
                self.assert_malformed(fixture)

    def test_detached_json_is_strict_and_bounded(self) -> None:
        raw_cases = (
            b'{"schemaVersion":1,"schemaVersion":1}\n',
            b'{"schemaVersion":NaN}\n',
            (b'{"nested":' * 64) + b"null" + (b"}" * 64),
            br'{"schemaVersion":1,"verifierId":"\ud800"}' + b"\n",
        )
        for reference_field in ("statement", "verificationReceipt"):
            for index, raw in enumerate(raw_cases):
                with self.subTest(reference=reference_field, case=index):
                    fixture = self.fixture(ready=True)
                    reference = fixture.approval("security")[reference_field]
                    fixture.replace_reference_contents(reference, raw)
                    fixture.write_bundle(recompute_subject=False)
                    self.assert_malformed(fixture)

    def test_unsafe_external_paths_symlinks_and_output_overwrite_fail_closed(
        self,
    ) -> None:
        for unsafe in (
            "../outside",
            "/absolute/file",
            "a//b",
            "a\\b",
            "control/\u001bpath",
        ):
            with self.subTest(path=unsafe):
                fixture = self.fixture(ready=True)
                reference = fixture.composition(COMPOSITION_CONTRACTS[0]["id"])[
                    "evidence"
                ]["auditRecords"][0]
                reference["path"] = unsafe
                fixture.rebind_candidate_and_approvals()
                self.assert_malformed(fixture)

        fixture = self.fixture(ready=True)
        existing = self.root / "existing-output.json"
        existing.write_text("must remain\n", encoding="utf-8")
        self.assert_malformed(fixture, output=existing)
        self.assertEqual(existing.read_text(encoding="utf-8"), "must remain\n")

        self.assert_malformed(
            fixture,
            output=fixture.evidence_root / "output.json",
        )
        self.assert_malformed(
            fixture,
            output=REPOSITORY / "scoped-release-output.json",
        )
        self.assert_malformed(
            fixture,
            bundle=SCOPE,
            bundle_sha256=_sha256(SCOPE.read_bytes()),
        )
        self.assert_malformed(fixture, evidence_root=REPOSITORY / "tool")

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows")
    def test_symlink_substitution_is_rejected(self) -> None:
        bundle_fixture = self.fixture(ready=True)
        bundle_link = self.root / "bundle-link.json"
        bundle_link.symlink_to(bundle_fixture.bundle_path)
        self.assert_malformed(bundle_fixture, bundle=bundle_link)

        root_fixture = self.fixture(ready=True)
        root_link = self.root / "evidence-link"
        root_link.symlink_to(root_fixture.evidence_root, target_is_directory=True)
        self.assert_malformed(root_fixture, evidence_root=root_link)

        file_fixture = self.fixture(ready=True)
        reference = file_fixture.composition(COMPOSITION_CONTRACTS[0]["id"])[
            "evidence"
        ]["auditRecords"][0]
        evidence_path = file_fixture.referenced_path(reference)
        target = self.root / "real-evidence.json"
        target.write_bytes(evidence_path.read_bytes())
        evidence_path.unlink()
        evidence_path.symlink_to(target)
        self.assert_malformed(file_fixture)

        output_fixture = self.fixture(ready=True)
        output_target = self.root / "real-output.json"
        output_target.write_text("must remain\n", encoding="utf-8")
        output_link = self.root / "approval-output-link.json"
        output_link.symlink_to(output_target)
        self.assert_malformed(output_fixture, output=output_link)
        self.assertEqual(
            output_target.read_text(encoding="utf-8"), "must remain\n"
        )

    def test_atomic_output_race_preserves_existing_owner(self) -> None:
        output = self.root / "racing-output.json"
        real_link = approval_validator.os.link

        def occupy_destination(
            source: str | Path,
            destination: str | Path,
            *,
            follow_symlinks: bool,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(
                destination,
                flags,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(descriptor, b"racing owner\n")
            finally:
                os.close(descriptor)
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            approval_validator.os,
            "link",
            side_effect=occupy_destination,
        ):
            with self.assertRaisesRegex(
                approval_validator.ScopedReleaseApprovalError,
                "already exist",
            ):
                approval_validator.write_validation_record(
                    output, {"result": "ready"}
                )

        self.assertEqual(output.read_bytes(), b"racing owner\n")
        self.assertEqual(list(self.root.glob(f".{output.name}.*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows")
    def test_output_parent_symlink_race_cannot_redirect_publication(self) -> None:
        fixture = self.fixture(ready=True)
        output_parent = self.root / "validated-output-parent"
        output_parent.mkdir()
        output = output_parent / "validation.json"
        validated_output = approval_validator._validate_output_location(
            output,
            REPOSITORY,
            fixture.evidence_root,
        )

        displaced_parent = self.root / "displaced-output-parent"
        redirected_parent = self.root / "redirected-output-parent"
        redirected_parent.mkdir()
        output_parent.rename(displaced_parent)
        output_parent.symlink_to(redirected_parent, target_is_directory=True)

        with self.assertRaises(approval_validator.ScopedReleaseApprovalError):
            approval_validator.write_validation_record(
                validated_output, {"result": "ready"}
            )

        self.assertFalse((redirected_parent / output.name).exists())
        self.assertFalse((displaced_parent / output.name).exists())
        self.assertEqual(
            list(redirected_parent.glob(f".{output.name}.*.tmp")), []
        )
        self.assertEqual(
            list(displaced_parent.glob(f".{output.name}.*.tmp")), []
        )

    def test_same_inode_same_size_output_corruption_is_not_published(self) -> None:
        output = self.root / "corrupted-output.json"
        record = {"result": "ready"}
        real_link = approval_validator.os.link

        def corrupt_after_link(
            source: str | Path,
            destination: str | Path,
            *,
            follow_symlinks: bool,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            descriptor = os.open(
                destination,
                os.O_RDWR,
                dir_fd=dst_dir_fd,
            )
            try:
                expected = os.read(descriptor, 4096)
                corrupted = expected.replace(b'"ready"', b'"pwned"')
                self.assertNotEqual(corrupted, expected)
                self.assertEqual(len(corrupted), len(expected))
                os.lseek(descriptor, 0, os.SEEK_SET)
                self.assertEqual(os.write(descriptor, corrupted), len(corrupted))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

        with mock.patch.object(
            approval_validator.os,
            "link",
            side_effect=corrupt_after_link,
        ):
            with self.assertRaises(approval_validator.ScopedReleaseApprovalError):
                approval_validator.write_validation_record(output, record)

        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(f".{output.name}.*.tmp")), [])

    def test_evidence_aba_substitution_race_is_rejected(self) -> None:
        fixture = self.fixture(ready=True)
        raw_reference = fixture.composition(COMPOSITION_CONTRACTS[0]["id"])[
            "evidence"
        ]["auditRecords"][0]
        reference = approval_validator._reference(raw_reference, "test evidence")
        evidence_path = fixture.referenced_path(raw_reference)
        displaced = evidence_path.with_name(f"{evidence_path.name}.displaced")

        with approval_validator.EvidenceReader(fixture.evidence_root) as reader:
            real_open = reader._open_relative
            open_count = 0

            def substitute_before_reopen(relative: str) -> int:
                nonlocal open_count
                open_count += 1
                if open_count == 2:
                    contents = evidence_path.read_bytes()
                    evidence_path.rename(displaced)
                    evidence_path.write_bytes(contents)
                return real_open(relative)

            with mock.patch.object(
                reader,
                "_open_relative",
                side_effect=substitute_before_reopen,
            ):
                with self.assertRaisesRegex(
                    approval_validator.ScopedReleaseApprovalError,
                    "changed after being read",
                ):
                    reader.read(reference)

        self.assertEqual(open_count, 2)

    def test_global_release_ready_flag_remains_fail_closed(self) -> None:
        documents = SimpleNamespace(
            metadata={
                "sbom": {"sha256": "0" * 64},
                "releaseReadiness": {
                    "ready": False,
                    "blockers": ["five-platform-release-remains-unapproved"],
                },
            }
        )
        arguments = [
            "--repository",
            str(REPOSITORY),
            "--staged-directory",
            str(self.root),
            "--artifact-id",
            "synthetic-artifact",
            "--sbom-output",
            str(self.root / "global.spdx.json"),
            "--metadata-output",
            str(self.root / "global-audit.json"),
        ]
        with mock.patch.object(
            release_evidence, "generate_evidence", return_value=documents
        ), mock.patch.object(release_evidence, "write_evidence"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(release_evidence.main(arguments), 0)
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                self.assertEqual(
                    release_evidence.main([*arguments, "--require-release-ready"]),
                    1,
                )
            self.assertIn(
                "five-platform-release-remains-unapproved", error.getvalue()
            )


if __name__ == "__main__":
    unittest.main()
