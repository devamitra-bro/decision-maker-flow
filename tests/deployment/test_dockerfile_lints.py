# FILE: tests/deployment/test_dockerfile_lints.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Static assertions about the Dockerfile structure. Verifies the invariants
#          mandated by §9.5 (exact CMD), §2.4 (non-root user, healthcheck, volume),
#          and positive invariant I2 (exact CMD form). hadolint integration is optional —
#          gracefully skips if the tool is not installed.
# SCOPE: Dockerfile text assertions; hadolint shell-out (skippable); structural checks.
# INPUT: Dockerfile at project root (located via __file__.resolve()).
# OUTPUT: pytest PASS/FAIL per structural invariant; hadolint SKIP if unavailable.
# KEYWORDS: [DOMAIN(9): Testing; TECH(8): DockerfileLinting; CONCEPT(9): InvariantCheck;
#            TECH(7): hadolint; PATTERN(8): StaticTextAssertion]
# LINKS: [READS_DATA_FROM(9): Dockerfile]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §9.5 (CMD hardlock), §2.4 (Slice D scope),
#   §6 positive invariant I2 (exact CMD).
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Dockerfile must contain exact CMD ["uvicorn","src.server.app_factory:create_app",...]
#   with --workers 1 (either compact JSON array or spaced form both accepted).
# - USER directive must appear before CMD in the Dockerfile.
# - USER 10001 (non-root uid).
# - EXPOSE 8000 only (no other EXPOSE lines).
# - HEALTHCHECK present and probes /healthz.
# - VOLUME ["/data"] declared.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why test Dockerfile structure in Python rather than a shell script?
# A: Python tests integrate with pytest Anti-Loop Protocol, produce structured output,
#    and can be skipped gracefully. Shell scripts lack pytest's reporting and hook system.
# Q: Why accept both compact and spaced CMD forms?
# A: Docker accepts both ["cmd","arg"] and ["cmd", "arg"] JSON array syntax. The plan
#    specifies the compact form; accepting the spaced form prevents false failures from
#    minor formatting choices during Dockerfile editing.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice D.]
# END_CHANGE_SUMMARY

import pathlib
import shutil
import subprocess

import pytest

# Resolve Dockerfile path from this file's location:
# tests/deployment/test_dockerfile_lints.py -> tests/deployment/ -> tests/ -> project root
DOCKERFILE = pathlib.Path(__file__).resolve().parents[2] / "Dockerfile"

# Exact CMD forms accepted (compact and spaced JSON array syntax both valid)
_CMD_COMPACT = (
    'CMD ["uvicorn","src.server.app_factory:create_app","--factory",'
    '"--host","0.0.0.0","--port","8000","--workers","1"]'
)
_CMD_SPACED = (
    'CMD ["uvicorn", "src.server.app_factory:create_app", "--factory",'
    ' "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]'
)


# START_FUNCTION_test_dockerfile_passes_hadolint
# START_CONTRACT:
# PURPOSE: Run hadolint against the Dockerfile and assert exit code 0.
#          Gracefully skips if hadolint is not installed on the system.
# INPUTS: None (reads DOCKERFILE constant)
# OUTPUTS: pytest PASS / SKIP / FAIL
# KEYWORDS: [TECH(7): hadolint; PATTERN(7): ExternalToolSkip]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@pytest.mark.skipif(shutil.which("hadolint") is None, reason="hadolint not installed")
def test_dockerfile_passes_hadolint() -> None:
    """
    Runs `hadolint <Dockerfile>` as a subprocess and asserts exit code 0.
    hadolint performs Dockerfile best-practice linting (ShellCheck integration,
    COPY best practices, package pinning, etc.). This test is skipped gracefully
    when hadolint is not available in the environment — CI pipelines that need
    this check should install hadolint explicitly.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_passes_hadolint ---")
    print(f"[DEPLOY][IMP:7][test_dockerfile][Hadolint] Running hadolint on {DOCKERFILE}")

    result = subprocess.run(
        ["hadolint", str(DOCKERFILE)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"[DEPLOY][IMP:9][test_dockerfile][Hadolint][BELIEF] "
              f"hadolint violations found [FAIL]")
        print(result.stdout)
        print(result.stderr)
    else:
        print(f"[DEPLOY][IMP:9][test_dockerfile][Hadolint][BELIEF] hadolint clean [OK]")

    assert result.returncode == 0, (
        f"hadolint found violations:\n{result.stdout}\n{result.stderr}"
    )
# END_FUNCTION_test_dockerfile_passes_hadolint


# START_FUNCTION_test_dockerfile_has_exact_cmd_workers_1
# START_CONTRACT:
# PURPOSE: Assert the Dockerfile contains the exact CMD with --workers 1 (§9.5 / I2).
#          Accepts both compact and spaced JSON array forms. Collapses newlines before
#          comparison to handle multi-line CMD continuations.
# INPUTS: None (reads DOCKERFILE constant)
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): CMDHardlock; PATTERN(8): StaticTextAssertion]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_dockerfile_has_exact_cmd_workers_1() -> None:
    """
    Asserts the Dockerfile CMD matches the I2 invariant exactly:
        CMD ["uvicorn","src.server.app_factory:create_app","--factory",
             "--host","0.0.0.0","--port","8000","--workers","1"]

    Both compact (no spaces after commas) and spaced (spaces after commas) forms are
    accepted. The check also normalises newlines to handle continued CMD lines.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_has_exact_cmd_workers_1 ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    # Collapse newlines for multi-line CMD continuations
    text_single_line = text.replace("\n", "").replace("\\", "")

    compact_present = _CMD_COMPACT.replace("\n", "").replace("\\", "") in text_single_line
    spaced_present = _CMD_SPACED.replace("\n", "").replace("\\", "") in text_single_line

    print(f"[DEPLOY][IMP:9][test_dockerfile][CMDCheck][BELIEF] "
          f"compact_present={compact_present} spaced_present={spaced_present} "
          f"[{'OK' if (compact_present or spaced_present) else 'FAIL'}]")

    assert compact_present or spaced_present, (
        "Dockerfile CMD does not match the I2 invariant (§9.5). "
        f"Expected one of:\n  {_CMD_COMPACT}\n  {_CMD_SPACED}\n\n"
        f"CMD lines found in Dockerfile:\n"
        + "\n".join(line for line in text.splitlines() if line.strip().startswith("CMD"))
    )
# END_FUNCTION_test_dockerfile_has_exact_cmd_workers_1


# START_FUNCTION_test_dockerfile_uses_non_root_user
# START_CONTRACT:
# PURPOSE: Assert Dockerfile sets USER to 10001 (non-root uid).
# INPUTS: None
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(8): NonRootUser; PATTERN(7): SecurityHardening]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_dockerfile_uses_non_root_user() -> None:
    """
    Asserts the Dockerfile contains a USER directive specifying uid 10001.
    This is required by k8s Pod Security Standards (restricted profile) and
    the k8s/deployment.yaml securityContext runAsUser: 10001.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_uses_non_root_user ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    user_lines = [line for line in text.splitlines() if line.strip().startswith("USER")]

    has_10001 = any("10001" in line for line in user_lines)

    print(f"[DEPLOY][IMP:9][test_dockerfile][UserCheck][BELIEF] "
          f"user_lines={user_lines} has_10001={has_10001} "
          f"[{'OK' if has_10001 else 'FAIL'}]")

    assert has_10001, (
        f"Dockerfile must contain 'USER 10001' directive. Found USER lines: {user_lines}"
    )
# END_FUNCTION_test_dockerfile_uses_non_root_user


# START_FUNCTION_test_dockerfile_exposes_only_8000
# START_CONTRACT:
# PURPOSE: Assert exactly one EXPOSE directive and it exposes port 8000.
# INPUTS: None
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(7): ExposedPort; PATTERN(7): SinglePortAssertion]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_dockerfile_exposes_only_8000() -> None:
    """
    Asserts the Dockerfile has exactly one EXPOSE directive and it exposes port 8000.
    brainstorm is a single-port service (HTTP on 8000). Multiple EXPOSE lines would
    be misleading and could prompt operators to open unnecessary firewall holes.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_exposes_only_8000 ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    expose_lines = [
        line.strip() for line in text.splitlines()
        if line.strip().startswith("EXPOSE")
    ]

    print(f"[DEPLOY][IMP:9][test_dockerfile][ExposeCheck][BELIEF] "
          f"expose_lines={expose_lines} [{'OK' if len(expose_lines) == 1 and '8000' in expose_lines[0] else 'FAIL'}]")

    assert len(expose_lines) == 1, (
        f"Expected exactly 1 EXPOSE directive, found {len(expose_lines)}: {expose_lines}"
    )
    assert "8000" in expose_lines[0], (
        f"EXPOSE must specify port 8000, got: {expose_lines[0]}"
    )
# END_FUNCTION_test_dockerfile_exposes_only_8000


# START_FUNCTION_test_dockerfile_healthcheck_probes_healthz
# START_CONTRACT:
# PURPOSE: Assert HEALTHCHECK directive is present and probes /healthz.
# INPUTS: None
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(8): HealthCheck; PATTERN(7): LivenessProbe]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_dockerfile_healthcheck_probes_healthz() -> None:
    """
    Asserts the Dockerfile contains a HEALTHCHECK directive that references /healthz.
    The healthcheck maps to the GET /healthz handler in turn_api.py and should use
    the same path as the k8s livenessProbe in deployment.yaml for consistency.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_healthcheck_probes_healthz ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    has_healthcheck = "HEALTHCHECK" in text
    has_healthz = "/healthz" in text

    print(f"[DEPLOY][IMP:9][test_dockerfile][HealthcheckCheck][BELIEF] "
          f"has_healthcheck={has_healthcheck} has_healthz={has_healthz} "
          f"[{'OK' if has_healthcheck and has_healthz else 'FAIL'}]")

    assert has_healthcheck, "Dockerfile missing HEALTHCHECK directive"
    assert has_healthz, "Dockerfile HEALTHCHECK does not reference /healthz"
# END_FUNCTION_test_dockerfile_healthcheck_probes_healthz


# START_FUNCTION_test_dockerfile_volumes_data
# START_CONTRACT:
# PURPOSE: Assert VOLUME ["/data"] is declared in the Dockerfile.
# INPUTS: None
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(7): VolumeDeclaration; PATTERN(7): PersistenceMount]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_dockerfile_volumes_data() -> None:
    """
    Asserts the Dockerfile declares VOLUME ["/data"]. This is the sqlite checkpoint
    persistence mount point. Without this declaration, container runtimes do not
    know to treat /data as a persistent volume and data would be lost on restart.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_volumes_data ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    has_volume_data = 'VOLUME ["/data"]' in text

    print(f"[DEPLOY][IMP:9][test_dockerfile][VolumeCheck][BELIEF] "
          f"has_volume_data={has_volume_data} [{'OK' if has_volume_data else 'FAIL'}]")

    assert has_volume_data, (
        'Dockerfile must declare VOLUME ["/data"] for sqlite checkpoint persistence.'
    )
# END_FUNCTION_test_dockerfile_volumes_data


# START_FUNCTION_test_dockerfile_no_root_cmd
# START_CONTRACT:
# PURPOSE: Assert USER directive appears before CMD in the Dockerfile.
#          Prevents accidental execution as root if USER is placed after CMD.
# INPUTS: None
# OUTPUTS: pytest PASS/FAIL
# KEYWORDS: [CONCEPT(9): NonRootExecution; PATTERN(8): DirectiveOrder]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_dockerfile_no_root_cmd() -> None:
    """
    Asserts that the USER directive appears before the CMD directive in the Dockerfile.
    Docker evaluates Dockerfile instructions top-to-bottom; if CMD came before USER,
    the process would run as root. This test enforces the correct ordering.

    Uses rfind() to find the LAST occurrence of each directive, which correctly handles
    multi-stage builds where USER may appear multiple times.
    """
    print(f"\n--- LDD TRACE: test_dockerfile_no_root_cmd ---")

    text = DOCKERFILE.read_text(encoding="utf-8")
    user_idx = text.rfind("USER ")
    cmd_idx = text.rfind("CMD ")

    print(f"[DEPLOY][IMP:9][test_dockerfile][OrderCheck][BELIEF] "
          f"user_idx={user_idx} cmd_idx={cmd_idx} "
          f"[{'OK' if user_idx < cmd_idx else 'FAIL'}]")

    assert user_idx != -1, "Dockerfile missing USER directive"
    assert cmd_idx != -1, "Dockerfile missing CMD directive"
    assert user_idx < cmd_idx, (
        f"USER directive (pos {user_idx}) must appear before CMD (pos {cmd_idx}) "
        "to ensure the process runs as non-root."
    )
# END_FUNCTION_test_dockerfile_no_root_cmd
