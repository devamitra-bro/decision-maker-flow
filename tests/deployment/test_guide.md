# FILE: tests/deployment/test_guide.md
# VERSION: 1.0.0
# PURPOSE: QA semantic bridge for the Slice D deployment test suite.
#          Describes required inputs, verification commands, and expected LDD markers.

## Slice D — Deployment Test Suite

### Overview

Tests in `tests/deployment/` validate all deployment artefacts for correctness:
- `Dockerfile` structural invariants (CMD, USER, EXPOSE, HEALTHCHECK, VOLUME)
- `k8s/brainstorm.manifest.json` schema and zero-knowledge compliance
- `k8s/*.yaml` Kubernetes manifest structural correctness
- Kustomize render + dry-run apply validation

---

### Test Files

| File | Tests | External Tool Deps |
|------|-------|--------------------|
| `test_dockerfile_lints.py` | 7 tests | `hadolint` (optional, skip if absent) |
| `test_manifest_json.py` | 7 tests | `jsonschema` (optional, skips gracefully) |
| `test_k8s_manifests.py` | 6 tests | `kubectl` (optional, skip if absent) |

---

### Running Tests

```bash
# Run all deployment tests
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
/opt/homebrew/bin/python3.12 -m pytest tests/deployment/ -v -s

# Run with kubectl-dependent tests (requires kubectl in PATH)
/opt/homebrew/bin/python3.12 -m pytest tests/deployment/ -v -s

# Run only manifest tests
/opt/homebrew/bin/python3.12 -m pytest tests/deployment/test_manifest_json.py -v -s
```

---

### Required Input Files

| File | Must Exist | Key Invariants |
|------|-----------|----------------|
| `Dockerfile` | Yes | CMD with --workers 1; USER 10001; EXPOSE 8000; HEALTHCHECK /healthz; VOLUME ["/data"] |
| `k8s/brainstorm.manifest.json` | Yes | auth.zero_knowledge_claims=true; deployment.replicas_hardlock=1; 10 metric names |
| `k8s/deployment.yaml` | Yes | StatefulSet; replicas=1; readOnlyRootFilesystem=true; port 8000 |
| `k8s/service.yaml` | Yes | ClusterIP; port 8000 |
| `k8s/configmap.yaml` | Yes | All env keys from §9.6 |
| `k8s/secret.example.yaml` | Yes | hmac_secret + llm_api_key keys |
| `k8s/kustomization.yaml` | Yes | Lists configmap, secret, deployment, service |

---

### Expected LDD Log Markers (IMP:7-10)

| Test | Expected Log Marker |
|------|---------------------|
| `test_manifest_file_exists_and_parses` | `[DEPLOY][IMP:9][test_manifest][FileCheck][BELIEF]` |
| `test_manifest_validates_against_inline_schema` | `[DEPLOY][IMP:9][test_manifest][Structural][BELIEF]` |
| `test_manifest_has_required_zero_knowledge_flag` | `[DEPLOY][IMP:9][test_manifest][ZeroKnowledge][BELIEF]` |
| `test_manifest_declares_replicas_hardlock_1` | `[DEPLOY][IMP:9][test_manifest][HardlockCheck][BELIEF]` |
| `test_manifest_capabilities_cover_turn_and_done` | `[DEPLOY][IMP:9][test_manifest][Capabilities][BELIEF]` |
| `test_manifest_metric_names_match_implementation` | `[DEPLOY][IMP:9][test_manifest][MetricNames][BELIEF]` |
| `test_manifest_never_references_user_id_field` | `[DEPLOY][IMP:9][test_manifest][ZeroKnowledge][BELIEF]` |
| `test_dockerfile_has_exact_cmd_workers_1` | `[DEPLOY][IMP:9][test_dockerfile][CMDCheck][BELIEF]` |
| `test_deployment_yaml_is_statefulset_with_replicas_1` | `[DEPLOY][IMP:9][test_k8s][ReplicasCheck][BELIEF]` |

---

### Zero-Knowledge Verification

All tests must pass `scripts/verify_zero_knowledge.sh`:

```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
./scripts/verify_zero_knowledge.sh
# Expected: [ZK_CHECK][PASS] src/server/ clean.
```

---

### Docker Smoke Test (Manual)

```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
docker build -t brainstorm-mcp:slice-d-test .
docker run --rm -d --name brainstorm-smoke \
  -e BRAINSTORM_HMAC_SECRET='smoke-hmac-secret-32bytes-aaaaaa' \
  -e GATEWAY_LLM_PROXY_URL='https://example.invalid/v1' \
  -e GATEWAY_LLM_API_KEY='smoke-key' \
  -p 18000:8000 brainstorm-mcp:slice-d-test
sleep 5
curl -sS -w '\n%{http_code}' http://127.0.0.1:18000/healthz
docker stop brainstorm-smoke
```
Expected: HTTP 200 from /healthz within 10 seconds.

---

### Anti-Loop Counter

`tests/deployment/.test_counter.json` tracks test failures. Reset to `{"failures": 0}` manually if needed:

```bash
echo '{"failures": 0}' > tests/deployment/.test_counter.json
```
