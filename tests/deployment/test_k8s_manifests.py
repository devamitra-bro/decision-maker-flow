# FILE: tests/deployment/test_k8s_manifests.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Validate Kubernetes manifest YAML files in k8s/ for structural correctness
#          and compliance with plan invariants. Uses pyyaml for parsing. kubectl-dependent
#          tests (kustomize + dry-run) gracefully skip if kubectl is not on PATH.
# SCOPE: deployment.yaml (StatefulSet kind, replicas=1, security context, ports, envFrom);
#        kustomization.yaml (kubectl kustomize render + dry-run apply); YAML parse integrity.
# INPUT: k8s/*.yaml files (located via k8s_dir fixture from conftest.py).
# OUTPUT: pytest PASS/FAIL per structural invariant; kubectl tests SKIP if tool absent.
# KEYWORDS: [DOMAIN(9): Testing; TECH(9): Kubernetes; TECH(8): PyYAML; TECH(7): Kustomize;
#            CONCEPT(9): StatefulSet; CONCEPT(8): SecurityContext; PATTERN(7): DryRunValidation]
# LINKS: [READS_DATA_FROM(9): k8s/deployment.yaml, k8s/kustomization.yaml;
#         USES_API(8): yaml; USES_API(7): subprocess (kubectl only)]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.4 (Slice D exit criteria),
#   §9.5 (replicas=1 hardlock), §6 constraint #13 (no multi-replica sqlite).
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - deployment.yaml must parse as YAML containing a StatefulSet with spec.replicas == 1.
# - Container securityContext must have readOnlyRootFilesystem=true and runAsNonRoot=true
#   (via pod securityContext) and capabilities.drop == ["ALL"].
# - Container must expose port 8000.
# - envFrom must reference brainstorm-config ConfigMap; env must reference brainstorm-secrets.
# - kubectl kustomize k8s/ must produce non-empty output (kustomize render).
# - kubectl apply --dry-run=client -k k8s/ must exit 0 (all manifests valid for the API).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why subprocess for kubectl rather than a Python k8s client?
# A: kubectl is the authoritative k8s manifest validator. The Python k8s client requires
#    a live cluster context; kubectl dry-run=client works offline against the local schema.
#    subprocess is used ONLY for external k8s tool invocation, not for business logic
#    (core-rules §4 forbids subprocess for business logic; tool invocation is permitted).
# Q: Why pyyaml.safe_load_all for the StatefulSet file?
# A: deployment.yaml contains two YAML documents (StatefulSet + commented HPA stub).
#    safe_load_all handles multi-document YAML. safe_load (not load) prevents arbitrary
#    Python object deserialisation from untrusted YAML.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice D.]
# END_CHANGE_SUMMARY

import pathlib
import shutil
import subprocess

import pytest
import yaml


# ===========================================================================
# Helper functions
# ===========================================================================


def _load_yaml_documents(path: pathlib.Path) -> list:
    """
    Load all YAML documents from a file using yaml.safe_load_all.
    Returns a list of parsed documents (dicts or None for empty documents).
    Raises yaml.YAMLError on parse failure.
    """
    with path.open("r", encoding="utf-8") as fh:
        docs = list(yaml.safe_load_all(fh))
    # Filter out None entries (empty document separators ---)
    return [d for d in docs if d is not None]


def _find_doc_by_kind(docs: list, kind: str) -> dict:
    """
    Find the first document in a list with the given 'kind' value.
    Returns the document dict or raises AssertionError if not found.
    """
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == kind:
            return doc
    raise AssertionError(f"No YAML document with kind={kind!r} found. Found kinds: "
                         f"{[d.get('kind') for d in docs if isinstance(d, dict)]}")


# ===========================================================================
# Test functions
# ===========================================================================


# START_FUNCTION_test_deployment_yaml_is_statefulset_with_replicas_1
# START_CONTRACT:
# PURPOSE: Parse deployment.yaml, find the StatefulSet document, assert spec.replicas == 1.
# INPUTS: k8s_dir fixture (pathlib.Path to k8s/)
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): StatefulSet; CONCEPT(9): ReplicasHardlock]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_deployment_yaml_is_statefulset_with_replicas_1(k8s_dir: pathlib.Path) -> None:
    """
    Parses k8s/deployment.yaml, finds the StatefulSet document (the file may contain
    multiple YAML documents — e.g. the commented-out HPA stub becomes a None doc),
    and asserts kind=StatefulSet with spec.replicas == 1.

    This enforces §9.5 and §6 constraint #13: sqlite single-writer semantics require
    exactly one pod. The replicas=1 hardlock in YAML is the k8s-layer enforcement.
    """
    print(f"\n--- LDD TRACE: test_deployment_yaml_is_statefulset_with_replicas_1 ---")

    deployment_file = k8s_dir / "deployment.yaml"
    assert deployment_file.exists(), f"deployment.yaml not found: {deployment_file}"

    docs = _load_yaml_documents(deployment_file)
    print(f"[DEPLOY][IMP:7][test_k8s][DeploymentParse] docs_count={len(docs)} "
          f"kinds={[d.get('kind') for d in docs if isinstance(d, dict)]}")

    sts = _find_doc_by_kind(docs, "StatefulSet")
    replicas = sts.get("spec", {}).get("replicas")

    print(f"[DEPLOY][IMP:9][test_k8s][ReplicasCheck][BELIEF] "
          f"kind=StatefulSet replicas={replicas} [{'OK' if replicas == 1 else 'FAIL'}]")

    assert sts["kind"] == "StatefulSet", (
        f"Expected StatefulSet, got {sts['kind']!r}. "
        "Use StatefulSet for sqlite (single-writer stateful storage)."
    )
    assert replicas == 1, (
        f"spec.replicas must be 1 (§9.5 hardlock), got {replicas!r}."
    )
# END_FUNCTION_test_deployment_yaml_is_statefulset_with_replicas_1


# START_FUNCTION_test_deployment_has_readonly_rootfs_and_nonroot_user
# START_CONTRACT:
# PURPOSE: Assert container securityContext has allowPrivilegeEscalation=false,
#          readOnlyRootFilesystem=true, capabilities.drop=["ALL"]; and pod securityContext
#          has runAsNonRoot=true with runAsUser=10001.
# INPUTS: k8s_dir fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): SecurityContext; CONCEPT(8): NonRootUser; CONCEPT(8): ReadOnlyFS]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_deployment_has_readonly_rootfs_and_nonroot_user(k8s_dir: pathlib.Path) -> None:
    """
    Parses k8s/deployment.yaml and validates the security configuration:

    Pod-level securityContext (spec.template.spec.securityContext):
    - runAsNonRoot: true
    - runAsUser: 10001
    - runAsGroup: 10001

    Container-level securityContext (containers[0].securityContext):
    - allowPrivilegeEscalation: false
    - readOnlyRootFilesystem: true
    - capabilities.drop: ["ALL"]

    These match the Dockerfile's USER 10001:10001 and satisfy k8s Pod Security
    Standards (restricted profile) requirements.
    """
    print(f"\n--- LDD TRACE: test_deployment_has_readonly_rootfs_and_nonroot_user ---")

    deployment_file = k8s_dir / "deployment.yaml"
    docs = _load_yaml_documents(deployment_file)
    sts = _find_doc_by_kind(docs, "StatefulSet")

    pod_spec = sts["spec"]["template"]["spec"]
    pod_sc = pod_spec.get("securityContext", {})
    containers = pod_spec.get("containers", [])
    assert containers, "No containers defined in StatefulSet pod spec"
    container = containers[0]
    c_sc = container.get("securityContext", {})

    print(f"[DEPLOY][IMP:9][test_k8s][SecurityContext][BELIEF] "
          f"pod.runAsNonRoot={pod_sc.get('runAsNonRoot')} "
          f"pod.runAsUser={pod_sc.get('runAsUser')} "
          f"container.allowPrivEsc={c_sc.get('allowPrivilegeEscalation')} "
          f"container.readOnlyRootFS={c_sc.get('readOnlyRootFilesystem')} "
          f"container.capsDrop={c_sc.get('capabilities', {}).get('drop')} "
          f"[{'OK' if all([pod_sc.get('runAsNonRoot'), pod_sc.get('runAsUser') == 10001, c_sc.get('allowPrivilegeEscalation') is False, c_sc.get('readOnlyRootFilesystem') is True]) else 'FAIL'}]")

    assert pod_sc.get("runAsNonRoot") is True, (
        f"spec.template.spec.securityContext.runAsNonRoot must be true, got {pod_sc.get('runAsNonRoot')!r}"
    )
    assert pod_sc.get("runAsUser") == 10001, (
        f"spec.template.spec.securityContext.runAsUser must be 10001, got {pod_sc.get('runAsUser')!r}"
    )
    assert c_sc.get("allowPrivilegeEscalation") is False, (
        f"container.securityContext.allowPrivilegeEscalation must be false, "
        f"got {c_sc.get('allowPrivilegeEscalation')!r}"
    )
    assert c_sc.get("readOnlyRootFilesystem") is True, (
        f"container.securityContext.readOnlyRootFilesystem must be true, "
        f"got {c_sc.get('readOnlyRootFilesystem')!r}"
    )
    caps = c_sc.get("capabilities", {})
    drop = caps.get("drop", [])
    assert "ALL" in drop, (
        f"container.securityContext.capabilities.drop must contain 'ALL', got {drop!r}"
    )
# END_FUNCTION_test_deployment_has_readonly_rootfs_and_nonroot_user


# START_FUNCTION_test_deployment_exposes_port_8000
# START_CONTRACT:
# PURPOSE: Assert the StatefulSet container exposes containerPort 8000.
# INPUTS: k8s_dir fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(7): ContainerPort; PATTERN(7): PortAssertion]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_deployment_exposes_port_8000(k8s_dir: pathlib.Path) -> None:
    """
    Parses k8s/deployment.yaml and asserts the brainstorm container declares
    containerPort 8000. This matches the EXPOSE 8000 in the Dockerfile and
    the port 8000 in k8s/service.yaml for consistent service routing.
    """
    print(f"\n--- LDD TRACE: test_deployment_exposes_port_8000 ---")

    deployment_file = k8s_dir / "deployment.yaml"
    docs = _load_yaml_documents(deployment_file)
    sts = _find_doc_by_kind(docs, "StatefulSet")

    containers = sts["spec"]["template"]["spec"].get("containers", [])
    assert containers, "No containers in StatefulSet pod spec"
    container = containers[0]

    ports = container.get("ports", [])
    port_numbers = [p.get("containerPort") for p in ports]

    print(f"[DEPLOY][IMP:9][test_k8s][PortCheck][BELIEF] "
          f"container_ports={port_numbers} [{'OK' if 8000 in port_numbers else 'FAIL'}]")

    assert 8000 in port_numbers, (
        f"Container must expose containerPort 8000. Found ports: {port_numbers}"
    )
# END_FUNCTION_test_deployment_exposes_port_8000


# START_FUNCTION_test_deployment_references_config_and_secrets
# START_CONTRACT:
# PURPOSE: Assert envFrom references brainstorm-config ConfigMap and env references
#          brainstorm-secrets Secret for both hmac_secret and llm_api_key.
# INPUTS: k8s_dir fixture
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(8): EnvConfiguration; CONCEPT(8): SecretReference; PATTERN(7): ConfigMap]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_deployment_references_config_and_secrets(k8s_dir: pathlib.Path) -> None:
    """
    Parses k8s/deployment.yaml and verifies:
    1. container.envFrom contains a configMapRef named "brainstorm-config".
    2. container.env contains secretKeyRef entries for "brainstorm-secrets" with
       keys "hmac_secret" and "llm_api_key".

    This ensures the deployment wires all required configuration from the ConfigMap
    and Secrets defined in k8s/configmap.yaml and k8s/secret.example.yaml.
    """
    print(f"\n--- LDD TRACE: test_deployment_references_config_and_secrets ---")

    deployment_file = k8s_dir / "deployment.yaml"
    docs = _load_yaml_documents(deployment_file)
    sts = _find_doc_by_kind(docs, "StatefulSet")

    container = sts["spec"]["template"]["spec"]["containers"][0]

    # Check envFrom -> configMapRef
    env_from = container.get("envFrom", [])
    config_map_refs = [
        e.get("configMapRef", {}).get("name")
        for e in env_from
        if isinstance(e, dict) and "configMapRef" in e
    ]

    print(f"[DEPLOY][IMP:7][test_k8s][EnvFromCheck] configMapRefs={config_map_refs}")
    assert "brainstorm-config" in config_map_refs, (
        f"container.envFrom must reference configmap 'brainstorm-config'. "
        f"Found configMapRefs: {config_map_refs}"
    )

    # Check env -> secretKeyRef for both required keys
    env = container.get("env", [])
    secret_refs = {}
    for e in env:
        if isinstance(e, dict) and "valueFrom" in e:
            vf = e["valueFrom"]
            if "secretKeyRef" in vf:
                skr = vf["secretKeyRef"]
                secret_refs[e.get("name", "")] = (skr.get("name"), skr.get("key"))

    print(f"[DEPLOY][IMP:7][test_k8s][SecretRefCheck] secret_refs={secret_refs}")

    # Assert BRAINSTORM_HMAC_SECRET references brainstorm-secrets/hmac_secret
    assert "BRAINSTORM_HMAC_SECRET" in secret_refs, (
        "container.env missing BRAINSTORM_HMAC_SECRET secretKeyRef"
    )
    hmac_ref = secret_refs["BRAINSTORM_HMAC_SECRET"]
    assert hmac_ref[0] == "brainstorm-secrets", (
        f"BRAINSTORM_HMAC_SECRET must reference secret 'brainstorm-secrets', got {hmac_ref[0]!r}"
    )
    assert hmac_ref[1] == "hmac_secret", (
        f"BRAINSTORM_HMAC_SECRET must use key 'hmac_secret', got {hmac_ref[1]!r}"
    )

    # Assert GATEWAY_LLM_API_KEY references brainstorm-secrets/llm_api_key
    assert "GATEWAY_LLM_API_KEY" in secret_refs, (
        "container.env missing GATEWAY_LLM_API_KEY secretKeyRef"
    )
    llm_ref = secret_refs["GATEWAY_LLM_API_KEY"]
    assert llm_ref[0] == "brainstorm-secrets", (
        f"GATEWAY_LLM_API_KEY must reference secret 'brainstorm-secrets', got {llm_ref[0]!r}"
    )
    assert llm_ref[1] == "llm_api_key", (
        f"GATEWAY_LLM_API_KEY must use key 'llm_api_key', got {llm_ref[1]!r}"
    )

    print(f"[DEPLOY][IMP:9][test_k8s][EnvConfigCheck][BELIEF] "
          f"configmap=brainstorm-config secret=brainstorm-secrets keys=[hmac_secret,llm_api_key] [OK]")
# END_FUNCTION_test_deployment_references_config_and_secrets


# START_FUNCTION_test_kustomization_is_valid
# START_CONTRACT:
# PURPOSE: Run `kubectl kustomize k8s/` and assert non-empty YAML output.
#          Gracefully skips if kubectl is not on PATH.
# INPUTS: k8s_dir fixture; subprocess (kubectl)
# OUTPUTS: pytest PASS / SKIP / FAIL
# KEYWORDS: [TECH(8): Kustomize; TECH(7): kubectl; PATTERN(7): RenderValidation]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_kustomization_is_valid(k8s_dir: pathlib.Path) -> None:
    """
    Runs `kubectl kustomize k8s/` and asserts the output is non-empty YAML.
    This validates that kustomize can render the base manifest set without errors.
    The test is skipped gracefully when kubectl is not available — useful for
    environments without a k8s client installed.
    """
    print(f"\n--- LDD TRACE: test_kustomization_is_valid ---")
    print(f"[DEPLOY][IMP:7][test_k8s][Kustomize] Running kubectl kustomize {k8s_dir}")

    result = subprocess.run(
        ["kubectl", "kustomize", str(k8s_dir)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"[DEPLOY][IMP:9][test_k8s][Kustomize][BELIEF] kustomize render FAILED [FAIL]")
        print(result.stderr)
    else:
        line_count = len(result.stdout.splitlines())
        print(f"[DEPLOY][IMP:9][test_k8s][Kustomize][BELIEF] "
              f"render_lines={line_count} [OK]")

    assert result.returncode == 0, (
        f"kubectl kustomize failed:\n{result.stderr}"
    )
    assert result.stdout.strip(), "kubectl kustomize produced empty output"

    # Validate the rendered output parses as valid YAML
    rendered_docs = list(yaml.safe_load_all(result.stdout))
    rendered_docs = [d for d in rendered_docs if d is not None]
    assert len(rendered_docs) > 0, "kubectl kustomize produced no YAML documents"

    print(f"[DEPLOY][IMP:7][test_k8s][Kustomize] "
          f"rendered_docs={len(rendered_docs)} "
          f"kinds={sorted(set(d.get('kind') for d in rendered_docs if d))}")
# END_FUNCTION_test_kustomization_is_valid


# START_FUNCTION_test_kustomize_dryrun_applies_cleanly
# START_CONTRACT:
# PURPOSE: Run `kubectl apply --dry-run=client -k k8s/` and assert exit code 0.
#          Validates that all manifests are structurally valid per the k8s API schema.
#          Gracefully skips if kubectl is not on PATH.
# INPUTS: k8s_dir fixture; subprocess (kubectl)
# OUTPUTS: pytest PASS / SKIP / FAIL
# KEYWORDS: [TECH(9): kubectl_dryrun; TECH(8): Kustomize; PATTERN(8): DryRunValidation]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_kustomize_dryrun_applies_cleanly(k8s_dir: pathlib.Path) -> None:
    """
    Runs `kubectl apply --dry-run=client -k k8s/` and asserts returncode == 0.
    This is the Slice D exit criterion from §2.4: all k8s manifests must validate
    against the local k8s API schema without requiring a live cluster.

    dry-run=client: validates against local schema (no network required).
    -k k8s/: uses kustomize rendering (reads kustomization.yaml).
    """
    print(f"\n--- LDD TRACE: test_kustomize_dryrun_applies_cleanly ---")
    print(f"[DEPLOY][IMP:7][test_k8s][DryRun] kubectl apply --dry-run=client -k {k8s_dir}")

    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-k", str(k8s_dir)],
        capture_output=True,
        text=True,
    )

    output = (result.stdout + result.stderr).strip()
    lines = output.splitlines()

    print(f"[DEPLOY][IMP:9][test_k8s][DryRun][BELIEF] "
          f"returncode={result.returncode} output_lines={len(lines)} "
          f"[{'OK' if result.returncode == 0 else 'FAIL'}]")

    if result.returncode != 0:
        print("[DEPLOY][IMP:10][test_k8s][DryRun][ExceptionEnrichment]")
        for line in lines:
            print(f"  {line}")

    assert result.returncode == 0, (
        f"kubectl apply --dry-run=client failed (returncode={result.returncode}):\n"
        f"{result.stderr}\n{result.stdout}"
    )
    assert output, "kubectl apply --dry-run=client produced no output"
# END_FUNCTION_test_kustomize_dryrun_applies_cleanly
