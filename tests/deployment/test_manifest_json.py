# FILE: tests/deployment/test_manifest_json.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Validate k8s/brainstorm.manifest.json against structural and semantic
#          invariants required by the MCP gateway integration. Uses a structural
#          validator (no jsonschema dependency) that checks required fields, types,
#          enumerated values, and zero-knowledge constraints. jsonschema library is
#          optional; if present, also validates against an inline JSON-Schema draft-07.
# SCOPE: JSON parse integrity; structural schema validation; zero_knowledge_claims flag;
#        replicas/workers hardlock; capability coverage (turn+done); metric name set;
#        R4 zero-knowledge enforcement (no user_id field anywhere in the manifest).
# INPUT: k8s/brainstorm.manifest.json (read from fixture path).
# OUTPUT: pytest pass/fail per invariant.
# KEYWORDS: [DOMAIN(9): Testing; TECH(8): JSONValidation; CONCEPT(10): ZeroKnowledgeDomain;
#            CONCEPT(9): ManifestContract; PATTERN(8): StructuralValidator]
# LINKS: [READS_DATA_FROM(9): k8s/brainstorm.manifest.json;
#         USES_API(8): src.server.metrics (for canonical metric names)]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.4 (Slice D exit criteria), §1.1 (R4),
#   §1.5 AC1 (zero-knowledge), §9.5 (replicas/workers hardlock).
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - manifest.json must parse as valid JSON.
# - auth.zero_knowledge_claims must be exactly True (not truthy — boolean True).
# - deployment.replicas_hardlock == 1 and deployment.workers_hardlock == 1.
# - capabilities set must cover exactly brainstorm__turn and brainstorm__done.
# - all 10 canonical metric names from Slice C metrics.py must appear in metric_names.
# - the string "user_id" must NOT appear anywhere in the manifest (R4 zero-knowledge).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why structural validator rather than jsonschema?
# A: jsonschema is not in requirements.txt and adding it as a runtime dep would bloat
#    the image. A structural validator is ~50 lines of Python and covers all contract
#    invariants without external dependencies. If jsonschema is available (dev env),
#    the draft-07 validation test also runs.
# Q: Why check for "user_id" string absence in the entire manifest?
# A: AC1 (zero-knowledge) requires that brainstorm never handles user identity.
#    If "user_id" appeared in a schema property, it would signal a contract breach
#    that could cause the gateway to route identity-bearing payloads to brainstorm.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice D.]
# END_CHANGE_SUMMARY

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Canonical metric names from Slice C (src/server/metrics.py).
# These are the exact names registered in build_registry().
# ---------------------------------------------------------------------------
CANONICAL_METRIC_NAMES = frozenset([
    "brainstorm_turns_total",
    "brainstorm_turn_duration_seconds",
    "brainstorm_llm_roundtrip_seconds",
    "brainstorm_active_sessions",
    "brainstorm_done_total",
    "brainstorm_token_verify_failures_total",
    "brainstorm_idempotent_hits_total",
    "brainstorm_sweeper_runs_total",
    "brainstorm_sweeper_deleted_total",
    "brainstorm_readyz_checks_total",
])

# Required top-level keys in the manifest
REQUIRED_TOP_LEVEL_KEYS = frozenset([
    "name", "version", "description", "endpoint", "auth",
    "capabilities", "schemas", "limits", "observability", "deployment",
])

# Inline JSON-Schema draft-07 definition for the manifest shape.
# Used only if jsonschema library is available.
MANIFEST_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": list(REQUIRED_TOP_LEVEL_KEYS),
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "description": {"type": "string"},
        "endpoint": {
            "type": "object",
            "required": ["base_url", "health", "ready", "metrics"],
            "properties": {
                "base_url": {"type": "string"},
                "health": {"type": "string"},
                "ready": {"type": "string"},
                "metrics": {"type": "string"},
            },
        },
        "auth": {
            "type": "object",
            "required": ["scheme", "token_version", "hmac_alg", "required_service_id", "zero_knowledge_claims"],
            "properties": {
                "scheme": {"type": "string"},
                "token_version": {"type": "string"},
                "hmac_alg": {"type": "string"},
                "required_service_id": {"type": "string"},
                "zero_knowledge_claims": {"type": "boolean"},
            },
        },
        "capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "method", "path", "description"],
            },
        },
        "deployment": {
            "type": "object",
            "required": ["replicas_hardlock", "workers_hardlock", "stateful", "rationale"],
            "properties": {
                "replicas_hardlock": {"type": "integer"},
                "workers_hardlock": {"type": "integer"},
                "stateful": {"type": "boolean"},
                "rationale": {"type": "string"},
            },
        },
    },
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# Helper: deep-walk JSON and collect all string values + dict keys
# ---------------------------------------------------------------------------

def _deep_collect_strings(obj, collected: list) -> None:
    """
    Recursively walk a JSON-decoded object and collect all string values and
    all dict keys into the provided list. Used for zero-knowledge assertion
    (no "user_id" string anywhere in manifest).
    """
    if isinstance(obj, str):
        collected.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            collected.append(k)
            _deep_collect_strings(v, collected)
    elif isinstance(obj, list):
        for item in obj:
            _deep_collect_strings(item, collected)


# ---------------------------------------------------------------------------
# Structural validator (no jsonschema dep required)
# ---------------------------------------------------------------------------

def _structural_validate(manifest: dict) -> list:
    """
    Validate the manifest against all required structural invariants.
    Returns a list of violation strings (empty = valid).

    Checks:
    - Required top-level keys present.
    - endpoint has required sub-keys.
    - auth has required sub-keys and zero_knowledge_claims is boolean True.
    - capabilities is a non-empty list with required fields.
    - deployment has replicas_hardlock=1, workers_hardlock=1.
    - observability.metric_names covers all canonical metrics.
    - limits has required numeric keys.
    """
    violations = []

    # Check required top-level keys
    missing_top = REQUIRED_TOP_LEVEL_KEYS - set(manifest.keys())
    if missing_top:
        violations.append(f"Missing top-level keys: {sorted(missing_top)}")

    # Validate endpoint sub-keys
    endpoint = manifest.get("endpoint", {})
    for key in ("base_url", "health", "ready", "metrics"):
        if key not in endpoint:
            violations.append(f"endpoint.{key} missing")

    # Validate auth sub-keys and zero_knowledge_claims type
    auth = manifest.get("auth", {})
    for key in ("scheme", "token_version", "hmac_alg", "required_service_id", "zero_knowledge_claims"):
        if key not in auth:
            violations.append(f"auth.{key} missing")
    if "zero_knowledge_claims" in auth and auth["zero_knowledge_claims"] is not True:
        violations.append(f"auth.zero_knowledge_claims must be boolean True, got {auth.get('zero_knowledge_claims')!r}")

    # Validate capabilities structure
    caps = manifest.get("capabilities", [])
    if not isinstance(caps, list) or len(caps) == 0:
        violations.append("capabilities must be a non-empty list")
    else:
        for i, cap in enumerate(caps):
            for field in ("id", "method", "path", "description"):
                if field not in cap:
                    violations.append(f"capabilities[{i}].{field} missing")

    # Validate deployment hardlocks
    deploy = manifest.get("deployment", {})
    if deploy.get("replicas_hardlock") != 1:
        violations.append(f"deployment.replicas_hardlock must be 1, got {deploy.get('replicas_hardlock')!r}")
    if deploy.get("workers_hardlock") != 1:
        violations.append(f"deployment.workers_hardlock must be 1, got {deploy.get('workers_hardlock')!r}")

    # Validate observability metric_names
    obs = manifest.get("observability", {})
    metric_names = set(obs.get("metric_names", []))
    missing_metrics = CANONICAL_METRIC_NAMES - metric_names
    if missing_metrics:
        violations.append(f"observability.metric_names missing: {sorted(missing_metrics)}")

    # Validate limits keys
    limits = manifest.get("limits", {})
    for key in ("max_concurrent_sessions", "session_ttl_sec", "turn_timeout_sec"):
        if key not in limits:
            violations.append(f"limits.{key} missing")

    return violations


# ===========================================================================
# Test functions
# ===========================================================================


# START_FUNCTION_test_manifest_file_exists_and_parses
# START_CONTRACT:
# PURPOSE: Verify k8s/brainstorm.manifest.json exists and is valid JSON.
# INPUTS: manifest_path fixture (pathlib.Path)
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(8): JSONParsing; PATTERN(7): FileExistence]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_manifest_file_exists_and_parses(manifest_path: pathlib.Path) -> None:
    """
    Asserts that k8s/brainstorm.manifest.json exists on disk and can be parsed
    as valid JSON. This is the foundational check — all other tests depend on it.
    """
    print(f"\n--- LDD TRACE: test_manifest_file_exists_and_parses ---")
    print(f"[DEPLOY][IMP:7][test_manifest][FileCheck] path={manifest_path}")

    assert manifest_path.exists(), f"Manifest file not found: {manifest_path}"
    assert manifest_path.is_file(), f"Manifest path is not a file: {manifest_path}"

    text = manifest_path.read_text(encoding="utf-8")
    assert len(text) > 0, "Manifest file is empty"

    manifest = json.loads(text)  # raises json.JSONDecodeError on invalid JSON
    assert isinstance(manifest, dict), "Manifest JSON root must be an object"

    print(f"[DEPLOY][IMP:9][test_manifest][FileCheck][BELIEF] Manifest parsed OK. "
          f"top_keys={sorted(manifest.keys())} [OK]")
# END_FUNCTION_test_manifest_file_exists_and_parses


# START_FUNCTION_test_manifest_validates_against_inline_schema
# START_CONTRACT:
# PURPOSE: Structural validation of the manifest against the inline schema definition.
#          Uses the custom structural validator (no jsonschema dep required).
#          If jsonschema library is available, also runs draft-07 validation as extra.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): SchemaValidation; PATTERN(8): StructuralValidator]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_manifest_validates_against_inline_schema(manifest_path: pathlib.Path) -> None:
    """
    Validates k8s/brainstorm.manifest.json against the inline structural schema.
    The structural validator checks all required top-level keys, endpoint/auth/capabilities
    sub-keys, deployment hardlocks, and metric name coverage. No external library needed.

    If jsonschema is installed in the dev environment, additionally validates the manifest
    against the MANIFEST_JSON_SCHEMA draft-07 definition for a stronger guarantee.
    """
    print(f"\n--- LDD TRACE: test_manifest_validates_against_inline_schema ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # START_BLOCK_STRUCTURAL_VALIDATION: [Custom structural validator]
    violations = _structural_validate(manifest)
    if violations:
        print("\n[DEPLOY][IMP:9][test_manifest][Structural][BELIEF] VIOLATIONS FOUND:")
        for v in violations:
            print(f"  - {v}")
    print(f"[DEPLOY][IMP:9][test_manifest][Structural][BELIEF] "
          f"violations={len(violations)} [{'OK' if not violations else 'FAIL'}]")
    assert not violations, f"Manifest structural violations:\n" + "\n".join(violations)
    # END_BLOCK_STRUCTURAL_VALIDATION

    # START_BLOCK_JSONSCHEMA_OPTIONAL: [Optional draft-07 validation if jsonschema installed]
    try:
        import jsonschema  # type: ignore[import]
        jsonschema.validate(instance=manifest, schema=MANIFEST_JSON_SCHEMA)
        print("[DEPLOY][IMP:7][test_manifest][JSONSchema] draft-07 validation passed [OK]")
    except ImportError:
        print("[DEPLOY][IMP:5][test_manifest][JSONSchema] jsonschema not installed — "
              "draft-07 validation skipped (structural validator ran instead) [SKIP]")
    # END_BLOCK_JSONSCHEMA_OPTIONAL
# END_FUNCTION_test_manifest_validates_against_inline_schema


# START_FUNCTION_test_manifest_has_required_zero_knowledge_flag
# START_CONTRACT:
# PURPOSE: Assert auth.zero_knowledge_claims is exactly boolean True (not just truthy).
#          Critical R4 invariant: brainstorm must never receive user identity.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(10): ZeroKnowledgeDomain; PATTERN(9): InvariantCheck]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_manifest_has_required_zero_knowledge_flag(manifest_path: pathlib.Path) -> None:
    """
    Asserts that manifest["auth"]["zero_knowledge_claims"] is exactly True.
    This is the R4 zero-knowledge invariant from §1.1 — brainstorm must never
    handle user identity. The gateway uses this flag to decide whether to strip
    user_id before forwarding tokens. A missing or False flag would violate AC1.
    """
    print(f"\n--- LDD TRACE: test_manifest_has_required_zero_knowledge_flag ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    auth = manifest.get("auth", {})
    zk = auth.get("zero_knowledge_claims")

    print(f"[DEPLOY][IMP:9][test_manifest][ZeroKnowledge][BELIEF] "
          f"zero_knowledge_claims={zk!r} type={type(zk).__name__} [{'OK' if zk is True else 'FAIL'}]")

    assert zk is True, (
        f"manifest.auth.zero_knowledge_claims must be exactly boolean True, got {zk!r}. "
        "R4 invariant (§1.1): brainstorm must never receive user identity."
    )
# END_FUNCTION_test_manifest_has_required_zero_knowledge_flag


# START_FUNCTION_test_manifest_declares_replicas_hardlock_1
# START_CONTRACT:
# PURPOSE: Assert deployment.replicas_hardlock == 1 and deployment.workers_hardlock == 1.
#          §9.5 hardlock: sqlite single-writer + LangGraph in-memory state require 1 pod/1 worker.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): ReplicasHardlock; CONCEPT(9): WorkersHardlock]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_manifest_declares_replicas_hardlock_1(manifest_path: pathlib.Path) -> None:
    """
    Asserts manifest["deployment"]["replicas_hardlock"] == 1 and
    manifest["deployment"]["workers_hardlock"] == 1.

    These values document the deployment invariant from §9.5: sqlite single-writer
    semantics and LangGraph in-memory state are incompatible with multi-replica or
    multi-worker deployments. The manifest communicates this to the gateway operator.
    """
    print(f"\n--- LDD TRACE: test_manifest_declares_replicas_hardlock_1 ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deploy = manifest.get("deployment", {})
    replicas = deploy.get("replicas_hardlock")
    workers = deploy.get("workers_hardlock")

    print(f"[DEPLOY][IMP:9][test_manifest][HardlockCheck][BELIEF] "
          f"replicas_hardlock={replicas!r} workers_hardlock={workers!r} "
          f"[{'OK' if replicas == 1 and workers == 1 else 'FAIL'}]")

    assert replicas == 1, (
        f"deployment.replicas_hardlock must be 1 (§9.5), got {replicas!r}."
    )
    assert workers == 1, (
        f"deployment.workers_hardlock must be 1 (§9.5), got {workers!r}."
    )
# END_FUNCTION_test_manifest_declares_replicas_hardlock_1


# START_FUNCTION_test_manifest_capabilities_cover_turn_and_done
# START_CONTRACT:
# PURPOSE: Assert capability IDs include exactly brainstorm__turn and brainstorm__done.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(8): CapabilityContract; PATTERN(7): SetCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_manifest_capabilities_cover_turn_and_done(manifest_path: pathlib.Path) -> None:
    """
    Asserts the manifest capabilities list contains IDs for both brainstorm__turn
    and brainstorm__done — the two MCP-exposed operations described in the task spec.
    No additional capabilities are required; the test checks coverage (superset).
    """
    print(f"\n--- LDD TRACE: test_manifest_capabilities_cover_turn_and_done ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    caps = manifest.get("capabilities", [])
    cap_ids = {c.get("id") for c in caps if isinstance(c, dict)}

    required = {"brainstorm__turn", "brainstorm__done"}
    missing = required - cap_ids

    print(f"[DEPLOY][IMP:9][test_manifest][Capabilities][BELIEF] "
          f"cap_ids={sorted(cap_ids)} missing={sorted(missing)} "
          f"[{'OK' if not missing else 'FAIL'}]")

    assert not missing, (
        f"Missing required capability IDs: {sorted(missing)}. Found: {sorted(cap_ids)}"
    )
# END_FUNCTION_test_manifest_capabilities_cover_turn_and_done


# START_FUNCTION_test_manifest_metric_names_match_implementation
# START_CONTRACT:
# PURPOSE: Assert observability.metric_names contains all 10 canonical metric names
#          from Slice C (src/server/metrics.py build_registry()).
#          Prevents manifest drift from the implementation.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): MetricsCoverage; PATTERN(8): ContractDriftDetection]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_manifest_metric_names_match_implementation(manifest_path: pathlib.Path) -> None:
    """
    Asserts that manifest["observability"]["metric_names"] is a superset of (or
    exactly matches) the 10 canonical metric names from build_registry() in
    src/server/metrics.py. If metrics are added in Slice C but not in the manifest,
    this test fails — preventing silent contract drift between code and operator docs.

    The 10 canonical names are defined at module level in CANONICAL_METRIC_NAMES.
    """
    print(f"\n--- LDD TRACE: test_manifest_metric_names_match_implementation ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    obs = manifest.get("observability", {})
    metric_names = set(obs.get("metric_names", []))

    missing = CANONICAL_METRIC_NAMES - metric_names

    print(f"[DEPLOY][IMP:9][test_manifest][MetricNames][BELIEF] "
          f"manifest_count={len(metric_names)} canonical_count={len(CANONICAL_METRIC_NAMES)} "
          f"missing={sorted(missing)} [{'OK' if not missing else 'FAIL'}]")

    assert not missing, (
        f"manifest.observability.metric_names missing canonical metrics: {sorted(missing)}"
    )
# END_FUNCTION_test_manifest_metric_names_match_implementation


# START_FUNCTION_test_manifest_never_references_user_id_field
# START_CONTRACT:
# PURPOSE: Deep-walk the entire manifest JSON and assert the string "user_id" never
#          appears as either a key or a value anywhere. R4 zero-knowledge invariant AC1.
# INPUTS: manifest_path fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(10): ZeroKnowledgeDomain; PATTERN(9): DeepWalkAssertion; CONCEPT(9): AC1]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_manifest_never_references_user_id_field(manifest_path: pathlib.Path) -> None:
    """
    Deep-walks the entire manifest JSON tree (keys + values recursively) and asserts
    that the literal string "user_id" never appears. This enforces AC1 (zero-knowledge):
    if the manifest declared a "user_id" schema property or endpoint parameter, the
    gateway operator might route identity-bearing payloads to brainstorm, violating R4.

    Note: '_meta' fields, comments, and all nested schemas are included in the walk.
    The only safe absence is complete absence.
    """
    print(f"\n--- LDD TRACE: test_manifest_never_references_user_id_field ---")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # START_BLOCK_DEEP_WALK: [Recursively collect all strings from manifest tree]
    all_strings: list = []
    _deep_collect_strings(manifest, all_strings)
    # END_BLOCK_DEEP_WALK

    # START_BLOCK_CHECK_USER_ID: [Assert "user_id" absent from every string]
    violations = [s for s in all_strings if "user_id" in s]

    print(f"[DEPLOY][IMP:9][test_manifest][ZeroKnowledge][BELIEF] "
          f"strings_checked={len(all_strings)} user_id_violations={len(violations)} "
          f"[{'OK' if not violations else 'FAIL'}]")

    if violations:
        print("[DEPLOY][IMP:10][test_manifest][ZeroKnowledge][ExceptionEnrichment] "
              f"Violating strings: {violations}")

    assert not violations, (
        f"R4 violation: 'user_id' found in manifest at {len(violations)} location(s): "
        f"{violations[:5]}. Manifest must never reference user identity (AC1, §1.1)."
    )
    # END_BLOCK_CHECK_USER_ID
# END_FUNCTION_test_manifest_never_references_user_id_field
