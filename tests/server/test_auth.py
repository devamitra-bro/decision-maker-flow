# FILE: tests/server/test_auth.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for src/server/auth.py. Validates TokenClaims strict shape (I5),
#          all AuthError reason branches (malformed/bad_version/bad_signature/expired/
#          wrong_service/missing_session), the happy path, user_id/iat strip invariant (I6),
#          token_fp redaction, constant-time HMAC path, and require_service cache identity.
# SCOPE: verify_session_token all branches; TokenClaims dataclass invariants;
#        AuthError taxonomy; _token_fp redaction; _b64url_decode pad-repair.
# INPUT: Signed tokens from signed_token_factory fixture; fixed_now fixture.
# OUTPUT: pytest PASS/FAIL with LDD trajectory output at IMP:9.
# KEYWORDS: [DOMAIN(9): TestAuth; CONCEPT(10): ZeroKnowledgeClaims; PATTERN(9): BranchCoverage;
#            CONCEPT(9): ConstantTimeHMAC; PATTERN(8): AuthErrorTaxonomy]
# LINKS: [READS_DATA_FROM(10): src/server/auth.py]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §1.1 (I5, I6, I7, I8), §2.1 Slice A exit criteria
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice A: full auth test suite.]
# END_CHANGE_SUMMARY

import dataclasses
import logging
import time
import uuid

import pytest

from src.server.auth import (
    AuthError,
    TokenClaims,
    _b64url_decode,
    _token_fp,
    require_service,
    verify_session_token,
)

# Valid UUID-v4 for use in tests
VALID_SESSION_ID = "a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5"


# START_FUNCTION_test_token_claims_strict_shape
# START_CONTRACT:
# PURPOSE: I5 invariant: TokenClaims must have EXACTLY {service_id, session_id, exp}.
#          No user_id, no iat, no scope, no extra fields.
# KEYWORDS: [CONCEPT(10): I5_Invariant; PATTERN(9): FrozenDataclass]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_token_claims_strict_shape():
    """
    I5 invariant verification: TokenClaims frozen dataclass must contain exactly three
    fields: service_id, session_id, exp. Any deviation is an AC1 (zero-knowledge) violation.

    This test is mandatory per plan §1.5 and the mode-code prompt positive invariants.
    """
    field_names = {f.name for f in dataclasses.fields(TokenClaims)}
    expected = {"service_id", "session_id", "exp"}

    assert field_names == expected, (
        f"I5 VIOLATION: TokenClaims fields {field_names} != expected {expected}. "
        f"user_id and iat must NEVER be in TokenClaims (zero-knowledge invariant)."
    )
# END_FUNCTION_test_token_claims_strict_shape


# START_FUNCTION_test_token_claims_is_frozen
# START_CONTRACT:
# PURPOSE: TokenClaims must be immutable (frozen=True) to prevent accidental mutation.
# KEYWORDS: [PATTERN(7): FrozenDataclass]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_token_claims_is_frozen():
    """TokenClaims must be a frozen dataclass — mutation must raise FrozenInstanceError."""
    claims = TokenClaims(service_id="brainstorm", session_id=VALID_SESSION_ID, exp=9999999999)

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        claims.service_id = "mutated"  # type: ignore[misc]
# END_FUNCTION_test_token_claims_is_frozen


# START_FUNCTION_test_auth_error_valid_reasons
# START_CONTRACT:
# PURPOSE: AuthError must accept all 6 valid reason codes without raising ValueError.
# KEYWORDS: [CONCEPT(8): AuthErrorTaxonomy; PATTERN(7): ExceptionShape]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_auth_error_valid_reasons():
    """All 6 valid AuthError reason codes must be accepted without ValueError."""
    valid_reasons = [
        "malformed", "bad_version", "bad_signature",
        "expired", "wrong_service", "missing_session"
    ]
    for reason in valid_reasons:
        err = AuthError(reason)
        assert err.reason == reason
        assert reason in str(err)
# END_FUNCTION_test_auth_error_valid_reasons


# START_FUNCTION_test_auth_error_invalid_reason_raises
# START_CONTRACT:
# PURPOSE: AuthError must raise ValueError for reason codes outside the approved taxonomy.
# KEYWORDS: [CONCEPT(8): AuthErrorTaxonomy; PATTERN(8): FailFast]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_auth_error_invalid_reason_raises():
    """AuthError with invalid reason must raise ValueError immediately."""
    with pytest.raises(ValueError, match="approved taxonomy"):
        AuthError("wrong_scope")  # This was a legacy reason code — must be rejected

    with pytest.raises(ValueError):
        AuthError("bad_user_id")

    with pytest.raises(ValueError):
        AuthError("iat_skew")
# END_FUNCTION_test_auth_error_invalid_reason_raises


# START_FUNCTION_test_auth_error_with_detail
# START_CONTRACT:
# PURPOSE: AuthError with detail appends the detail to the message string.
# KEYWORDS: [CONCEPT(7): ExceptionShape]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_auth_error_with_detail():
    """AuthError(reason, detail) must include both in str(err)."""
    err = AuthError("malformed", "custom detail message")
    assert "malformed" in str(err)
    assert "custom detail message" in str(err)
    assert err.reason == "malformed"
# END_FUNCTION_test_auth_error_with_detail


# START_FUNCTION_test_verify_happy_path
# START_CONTRACT:
# PURPOSE: verify_session_token returns correct TokenClaims for a valid token.
#          Also verifies that user_id and iat are not in the returned claims (I6).
# KEYWORDS: [CONCEPT(10): HappyPath; CONCEPT(9): ZeroKnowledgeI6]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_verify_happy_path(signed_token_factory, token_secret, fixed_now, ldd_capture, caplog):
    """
    Happy path: valid token with correct service_id, future exp, valid session_id.
    TokenClaims returned must contain exactly {service_id, session_id, exp}.
    user_id must NOT appear anywhere in the returned object (I6 zero-knowledge).
    LDD IMP:9 [Auth][Verify-OK][BELIEF] must be logged.
    """
    caplog.set_level(logging.DEBUG)

    exp = fixed_now + 3600
    bearer = signed_token_factory("brainstorm", VALID_SESSION_ID, exp)

    claims = verify_session_token(
        raw_authorization_header=bearer,
        required_service_id="brainstorm",
        now=fixed_now,
        secret=token_secret,
    )

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 BEFORE business assertions — Anti-Illusion]
    high_imp_logs = ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Business assertions]
    assert isinstance(claims, TokenClaims)
    assert claims.service_id == "brainstorm"
    assert claims.session_id == VALID_SESSION_ID
    assert claims.exp == exp

    # I6 zero-knowledge: user_id and iat must NEVER be in TokenClaims
    field_names = {f.name for f in dataclasses.fields(claims)}
    assert "user_id" not in field_names, "I6 VIOLATION: user_id found in TokenClaims"
    assert "iat" not in field_names, "I6 VIOLATION: iat found in TokenClaims"

    # Anti-Illusion: IMP:9 Verify-OK log must be present
    found_belief = any("[Verify-OK][BELIEF]" in log for log in high_imp_logs)
    assert found_belief, (
        "Critical LDD Error: verify_session_token did not emit [IMP:9][Auth][Verify-OK][BELIEF] log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_verify_happy_path


# START_FUNCTION_test_verify_missing_header
# START_CONTRACT:
# PURPOSE: None Authorization header must raise AuthError("malformed").
# KEYWORDS: [CONCEPT(9): MissingHeader; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_missing_header(token_secret, fixed_now, ldd_capture, caplog):
    """Missing (None) Authorization header must raise AuthError with reason='malformed'."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=None,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )

    # START_BLOCK_LDD_TELEMETRY
    ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_missing_header


# START_FUNCTION_test_verify_non_bearer_scheme
# START_CONTRACT:
# PURPOSE: Non-Bearer Authorization scheme must raise AuthError("malformed").
# KEYWORDS: [CONCEPT(8): BearerScheme; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_non_bearer_scheme(token_secret, fixed_now):
    """Basic, Digest, or other auth schemes must be rejected as malformed."""
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header="Basic dXNlcjpwYXNz",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_non_bearer_scheme


# START_FUNCTION_test_verify_wrong_part_count
# START_CONTRACT:
# PURPOSE: Token with wrong number of dot-separated parts must raise AuthError("malformed").
# KEYWORDS: [CONCEPT(8): TokenStructure; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_wrong_part_count(token_secret, fixed_now):
    """Token with 2 parts (missing sig) or 4 parts must raise AuthError('malformed')."""
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header="Bearer v1.onlytwoparts",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"

    with pytest.raises(AuthError) as exc_info2:
        verify_session_token(
            raw_authorization_header="Bearer v1.part1.part2.extrapart",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info2.value.reason == "malformed"
# END_FUNCTION_test_verify_wrong_part_count


# START_FUNCTION_test_verify_bad_version
# START_CONTRACT:
# PURPOSE: Token with version prefix != "v1" must raise AuthError("bad_version").
# KEYWORDS: [CONCEPT(8): VersionCheck; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_bad_version(token_secret, fixed_now, signed_token_factory):
    """Token with 'v2' or other prefix must raise AuthError('bad_version')."""
    # Build a valid token then replace v1 with v2
    exp = fixed_now + 3600
    bearer = signed_token_factory("brainstorm", VALID_SESSION_ID, exp)
    # Replace "Bearer v1." with "Bearer v2."
    bad_bearer = bearer.replace("Bearer v1.", "Bearer v2.")

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bad_bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "bad_version"
# END_FUNCTION_test_verify_bad_version


# START_FUNCTION_test_verify_bad_signature
# START_CONTRACT:
# PURPOSE: Token with tampered signature must raise AuthError("bad_signature").
#          Verifies constant-time HMAC compare_digest path.
# KEYWORDS: [CONCEPT(10): ConstantTimeHMAC; PATTERN(9): TamperedSig]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_verify_bad_signature(token_secret, fixed_now, signed_token_factory, ldd_capture, caplog):
    """
    Tampered signature must raise AuthError('bad_signature').
    This verifies the hmac.compare_digest path (I7 constant-time invariant).
    LDD IMP:9 [Auth][Verify-Failed] must be logged.
    """
    caplog.set_level(logging.DEBUG)

    exp = fixed_now + 3600
    bearer = signed_token_factory("brainstorm", VALID_SESSION_ID, exp)
    # Tamper: replace last character of sig
    parts = bearer.split(".")
    tampered_sig = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    tampered_bearer = ".".join(parts[:-1] + [tampered_sig])

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=tampered_bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )

    # START_BLOCK_LDD_TELEMETRY
    high_imp_logs = ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    assert exc_info.value.reason == "bad_signature"

    # Anti-Illusion: IMP:9 Verify-Failed must be logged
    found_fail_log = any("[Verify-Failed]" in log for log in high_imp_logs)
    assert found_fail_log, "LDD Error: IMP:9 [Verify-Failed] not found in caplog for bad_signature"
# END_FUNCTION_test_verify_bad_signature


# START_FUNCTION_test_verify_wrong_secret
# START_CONTRACT:
# PURPOSE: Token signed with different secret must raise AuthError("bad_signature").
# KEYWORDS: [CONCEPT(9): WrongSecret; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_wrong_secret(signed_token_factory, token_secret, fixed_now):
    """Token signed with secret_A must be rejected by secret_B as bad_signature."""
    other_secret = b"completely-different-secret-bytes"
    exp = fixed_now + 3600
    # Sign with token_secret
    bearer = signed_token_factory("brainstorm", VALID_SESSION_ID, exp)

    # Verify with other_secret — must fail
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=other_secret,
        )
    assert exc_info.value.reason == "bad_signature"
# END_FUNCTION_test_verify_wrong_secret


# START_FUNCTION_test_verify_expired
# START_CONTRACT:
# PURPOSE: Token with exp <= now must raise AuthError("expired").
# KEYWORDS: [CONCEPT(9): Expiry; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_expired(signed_token_factory, token_secret, fixed_now, ldd_capture, caplog):
    """
    Token with exp = now - 1 must raise AuthError('expired').
    LDD IMP:9 [Verify-Failed] must be emitted.
    """
    caplog.set_level(logging.DEBUG)

    # exp is exactly now — should also be expired (exp <= now means expired)
    bearer_exact = signed_token_factory("brainstorm", VALID_SESSION_ID, exp=fixed_now)
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bearer_exact,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )

    ldd_capture()
    assert exc_info.value.reason == "expired"

    # Token 1 second in the past
    bearer_past = signed_token_factory("brainstorm", VALID_SESSION_ID, exp=fixed_now - 1)
    with pytest.raises(AuthError) as exc_info2:
        verify_session_token(
            raw_authorization_header=bearer_past,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info2.value.reason == "expired"
# END_FUNCTION_test_verify_expired


# START_FUNCTION_test_verify_wrong_service
# START_CONTRACT:
# PURPOSE: Token with service_id != required_service_id raises AuthError("wrong_service").
# KEYWORDS: [CONCEPT(9): ServiceScope; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_wrong_service(signed_token_factory, token_secret, fixed_now, ldd_capture, caplog):
    """
    Token with service_id="tavily" verified against required="brainstorm" must raise
    AuthError('wrong_service'). LDD IMP:9 [Verify-Failed] must be logged.
    """
    caplog.set_level(logging.DEBUG)

    exp = fixed_now + 3600
    bearer = signed_token_factory("tavily", VALID_SESSION_ID, exp)

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )

    high_imp_logs = ldd_capture()
    assert exc_info.value.reason == "wrong_service"
    found_fail_log = any("[Verify-Failed]" in log for log in high_imp_logs)
    assert found_fail_log, "LDD Error: IMP:9 [Verify-Failed] not found for wrong_service"
# END_FUNCTION_test_verify_wrong_service


# START_FUNCTION_test_verify_missing_session_empty
# START_CONTRACT:
# PURPOSE: Token with empty session_id must raise AuthError("missing_session").
# KEYWORDS: [CONCEPT(8): SessionValidation; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_missing_session_empty(signed_token_factory, token_secret, fixed_now):
    """Empty session_id in token must raise AuthError('missing_session')."""
    exp = fixed_now + 3600
    bearer = signed_token_factory("brainstorm", "", exp)  # empty session_id

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "missing_session"
# END_FUNCTION_test_verify_missing_session_empty


# START_FUNCTION_test_verify_missing_session_not_uuid4
# START_CONTRACT:
# PURPOSE: Token with session_id that is not UUID-v4 format raises AuthError("missing_session").
# KEYWORDS: [CONCEPT(8): UUIDv4Validation; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_missing_session_not_uuid4(signed_token_factory, token_secret, fixed_now):
    """Non-UUID session_id (e.g., plain string, UUIDv1) must raise AuthError('missing_session')."""
    exp = fixed_now + 3600

    # Plain non-UUID string
    bearer_plain = signed_token_factory("brainstorm", "not-a-uuid", exp)
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=bearer_plain,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "missing_session"

    # UUID v1 (not v4) — version digit must be 4
    uuid_v1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    bearer_v1 = signed_token_factory("brainstorm", uuid_v1, exp)
    with pytest.raises(AuthError) as exc_info2:
        verify_session_token(
            raw_authorization_header=bearer_v1,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info2.value.reason == "missing_session"
# END_FUNCTION_test_verify_missing_session_not_uuid4


# START_FUNCTION_test_verify_user_id_iat_stripped
# START_CONTRACT:
# PURPOSE: Verify I6 invariant: user_id and iat from the raw JSON are stripped and
#          never appear in TokenClaims. Even if the token explicitly contains them.
# KEYWORDS: [CONCEPT(10): I6_ZeroKnowledge; CONCEPT(9): PIIStrip]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_verify_user_id_iat_stripped(signed_token_factory, token_secret, fixed_now):
    """
    I6 zero-knowledge invariant: user_id (present in Go wire format) and iat
    must be popped before TokenClaims construction. The returned claims object
    must not have these as attributes or dataclass fields.
    """
    exp = fixed_now + 3600
    # signed_token_factory includes user_id=999 in payload (matches Go wire format)
    bearer = signed_token_factory("brainstorm", VALID_SESSION_ID, exp, user_id=12345)

    claims = verify_session_token(
        raw_authorization_header=bearer,
        required_service_id="brainstorm",
        now=fixed_now,
        secret=token_secret,
    )

    # I6: user_id must not be in TokenClaims (popped from raw before construction)
    field_names = {f.name for f in dataclasses.fields(claims)}
    assert "user_id" not in field_names, "I6 VIOLATION: user_id in TokenClaims fields"
    assert "iat" not in field_names, "I6 VIOLATION: iat in TokenClaims fields"
    assert not hasattr(claims, "user_id"), "I6 VIOLATION: claims has user_id attribute"
    assert not hasattr(claims, "iat"), "I6 VIOLATION: claims has iat attribute"
    assert claims.session_id == VALID_SESSION_ID
# END_FUNCTION_test_verify_user_id_iat_stripped


# START_FUNCTION_test_b64url_decode_pad_repair
# START_CONTRACT:
# PURPOSE: Verify _b64url_decode correctly handles Go RawURLEncoding output (no padding).
#          Tests all padding cases: 0, 1, 2, 3 chars missing.
# KEYWORDS: [PATTERN(9): Base64url_PadRepair; TECH(8): RawURLEncoding]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_b64url_decode_pad_repair():
    """
    _b64url_decode must handle base64url strings of any length (I8 pad-repair).
    Go RawURLEncoding produces strings without trailing '=' padding.
    Python urlsafe_b64decode requires padding — our function repairs it.
    """
    import base64

    # Test data: ensure each length mod 4 is covered
    test_cases = [
        b"",           # empty
        b"a",          # len 1 -> needs 3 pads
        b"ab",         # len 2 -> needs 2 pads
        b"abc",        # len 3 -> needs 1 pad
        b"abcd",       # len 4 -> no pad needed
        b"hello world test data for base64",
    ]

    for original in test_cases:
        # Encode with RawURL (no padding) as Go would
        raw_encoded = base64.urlsafe_b64encode(original).rstrip(b"=").decode("ascii")
        decoded = _b64url_decode(raw_encoded)
        assert decoded == original, f"Pad-repair failed for input {original!r}: {decoded!r}"
# END_FUNCTION_test_b64url_decode_pad_repair


# START_FUNCTION_test_token_fp_format
# START_CONTRACT:
# PURPOSE: Verify _token_fp produces the correct "sha256:<8hex>" format.
# KEYWORDS: [CONCEPT(8): LogRedaction; PATTERN(7): Fingerprint]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_token_fp_format():
    """_token_fp must return 'sha256:' + exactly 8 lowercase hex chars."""
    fp = _token_fp(b"test-data")
    assert fp.startswith("sha256:")
    hex_part = fp[7:]
    assert len(hex_part) == 8
    assert all(c in "0123456789abcdef" for c in hex_part)

    # String input should also work
    fp_str = _token_fp("string-input")
    assert fp_str.startswith("sha256:")
    assert len(fp_str) == 15  # "sha256:" (7) + 8 hex chars
# END_FUNCTION_test_token_fp_format


# START_FUNCTION_test_require_service_stable_identity
# START_CONTRACT:
# PURPOSE: require_service factory must return the same callable object for the same
#          service_id (stable identity for dependency_overrides). Different service_ids
#          must return different callables.
# KEYWORDS: [PATTERN(8): StableIdentityCache; TECH(7): FastAPI_Depends]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_require_service_stable_identity():
    """
    require_service("brainstorm") called twice must return identical callable objects.
    This is required for FastAPI dependency_overrides to work correctly in tests.
    """
    dep_a = require_service("brainstorm")
    dep_b = require_service("brainstorm")
    assert dep_a is dep_b, (
        "require_service must return the same callable for the same service_id "
        "(stable identity required for FastAPI dependency_overrides)"
    )

    # Different service_ids must return different callables
    dep_c = require_service("tavily")
    assert dep_a is not dep_c
# END_FUNCTION_test_require_service_stable_identity


# START_FUNCTION_test_verify_malformed_base64
# START_CONTRACT:
# PURPOSE: Token with invalid base64url in payload part must raise AuthError("malformed").
# KEYWORDS: [CONCEPT(8): MalformedToken; PATTERN(7): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_malformed_base64(token_secret, fixed_now):
    """Token with invalid base64url in payload (non-base64 chars) must raise malformed."""
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header="Bearer v1.!!!invalid_base64!!!.validenough",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_malformed_base64


# START_FUNCTION_test_verify_valid_uuid_v4_variants
# START_CONTRACT:
# PURPOSE: Verify that valid UUID-v4 session_ids in different case are accepted.
# KEYWORDS: [CONCEPT(7): UUIDv4; PATTERN(6): BranchCoverage]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_verify_valid_uuid_v4_variants(signed_token_factory, token_secret, fixed_now):
    """Several valid UUID-v4 strings must all pass session_id validation."""
    valid_uuids = [
        "a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5",   # variant 8 (valid)
        "b2c3d4e5-f6a7-4b8c-9d0e-f1a2b3c4d5e6",   # variant 9 (valid)
        "c3d4e5f6-a7b8-4c9d-ab1f-a2b3c4d5e6f7",   # variant a (valid)
        str(uuid.uuid4()),                           # fresh random UUID-v4
    ]

    exp = fixed_now + 3600
    for session_id in valid_uuids:
        bearer = signed_token_factory("brainstorm", session_id, exp)
        claims = verify_session_token(
            raw_authorization_header=bearer,
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
        assert claims.session_id == session_id
# END_FUNCTION_test_verify_valid_uuid_v4_variants


# START_FUNCTION_test_verify_invalid_sig_base64
# START_CONTRACT:
# PURPOSE: Token with valid payload base64 but invalid sig base64 (non-url-safe chars)
#          must raise AuthError("malformed") via the sig decode exception branch.
# KEYWORDS: [CONCEPT(8): SigBase64Error; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_verify_invalid_sig_base64(token_secret, fixed_now):
    """
    Token v1.<valid_payload>.<invalid_sig_base64> must raise malformed via sig decode branch.
    We construct a valid-looking payload but an invalid sig (containing '!!!' which
    is not valid base64url). Since payload base64 is valid, this specifically hits the
    sig decode exception at lines 338-344 of auth.py.
    """
    import base64
    import json as json_mod
    import hmac as hmac_mod

    # Build a valid payload base64
    payload = json_mod.dumps({"service_id": "brainstorm", "session_id": VALID_SESSION_ID, "exp": fixed_now + 3600}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()

    # Use a clearly invalid sig part (contains '!' which is not in base64url alphabet)
    invalid_sig = "!!invalid_sig_chars!!"

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer v1.{payload_b64}.{invalid_sig}",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_invalid_sig_base64


# START_FUNCTION_test_verify_valid_hmac_invalid_json
# START_CONTRACT:
# PURPOSE: Token where payload decodes from base64 but is not valid JSON, and the HMAC
#          is valid for that non-JSON payload, must raise AuthError("malformed") via the
#          json.loads exception branch (lines 362-368 of auth.py).
# KEYWORDS: [CONCEPT(8): InvalidJSON; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_verify_valid_hmac_invalid_json(token_secret, fixed_now):
    """
    Token where HMAC is valid but payload is not JSON must raise malformed (json.loads branch).
    We construct a token manually: payload = b"not-json", compute HMAC over it, encode both.
    """
    import base64
    import hmac as hmac_mod

    # Payload that is valid base64url but not valid JSON
    payload_bytes = b"this-is-not-valid-json-at-all"
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()

    # Compute HMAC over this payload so HMAC check passes
    mac = hmac_mod.new(token_secret, payload_bytes, "sha256")
    sig_b64 = base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer v1.{payload_b64}.{sig_b64}",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_valid_hmac_invalid_json


# START_FUNCTION_test_verify_missing_required_claim
# START_CONTRACT:
# PURPOSE: Token with valid HMAC + valid JSON but missing 'exp' field must raise
#          AuthError("malformed") via the claims construction exception branch (lines 384-390).
# KEYWORDS: [CONCEPT(8): MissingClaim; PATTERN(8): BranchCoverage]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_verify_missing_required_claim(token_secret, fixed_now):
    """
    Token with valid HMAC + valid JSON missing 'exp' key raises malformed (missing_required_claim).
    This covers the KeyError branch in claims construction at lines 384-390 of auth.py.
    """
    import base64
    import json as json_mod
    import hmac as hmac_mod

    # Valid JSON but missing 'exp' field
    payload = json_mod.dumps(
        {"service_id": "brainstorm", "session_id": VALID_SESSION_ID},  # no 'exp'
        separators=(",", ":"),
    )
    payload_bytes = payload.encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()

    # Compute valid HMAC so HMAC check passes
    mac = hmac_mod.new(token_secret, payload_bytes, "sha256")
    sig_b64 = base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()

    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer v1.{payload_b64}.{sig_b64}",
            required_service_id="brainstorm",
            now=fixed_now,
            secret=token_secret,
        )
    assert exc_info.value.reason == "malformed"
# END_FUNCTION_test_verify_missing_required_claim


# START_FUNCTION_test_require_service_dep_translates_auth_error
# START_CONTRACT:
# PURPOSE: Verify that the require_service inner _dep coroutine correctly translates
#          AuthError to HTTPException with correct status codes.
#          Also covers the _dep function body (lines 478-500 of auth.py).
# KEYWORDS: [CONCEPT(9): FastAPIDepends; PATTERN(8): AuthErrorTranslation]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_require_service_dep_translates_auth_error(server_env, fixed_now):
    """
    require_service('brainstorm') inner dep must raise HTTPException(401) for malformed/expired/
    bad_signature/missing_session, and HTTPException(403) for wrong_service.
    This test covers the async _dep body at lines 478-500 of auth.py.
    """
    import asyncio
    from fastapi import HTTPException

    dep = require_service("brainstorm")

    # Test: missing Authorization -> malformed -> 401
    async def run_missing():
        return await dep(authorization=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(run_missing())
    assert exc_info.value.status_code == 401

    # Test: wrong service_id -> wrong_service -> 403
    # Build a token for "tavily" service (wrong service)
    import base64
    import json as json_mod
    import hmac as hmac_mod
    from src.server.config import get_cfg

    cfg = get_cfg()
    secret = cfg.hmac_secret.get_secret_value().encode("utf-8")

    payload = json_mod.dumps(
        {
            "user_id": 1,
            "service_id": "tavily",
            "session_id": VALID_SESSION_ID,
            "exp": 9999999999,
        },
        separators=(",", ":"),
    )
    payload_bytes = payload.encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    mac = hmac_mod.new(secret, payload_bytes, "sha256")
    sig_b64 = base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()
    wrong_service_bearer = f"Bearer v1.{payload_b64}.{sig_b64}"

    async def run_wrong_service():
        return await dep(authorization=wrong_service_bearer)

    with pytest.raises(HTTPException) as exc_info2:
        asyncio.run(run_wrong_service())
    assert exc_info2.value.status_code == 403

    # Test: valid token -> returns TokenClaims (happy path through dep)
    payload_ok = json_mod.dumps(
        {
            "user_id": 1,
            "service_id": "brainstorm",
            "session_id": VALID_SESSION_ID,
            "exp": 9999999999,
        },
        separators=(",", ":"),
    )
    payload_bytes_ok = payload_ok.encode()
    payload_b64_ok = base64.urlsafe_b64encode(payload_bytes_ok).rstrip(b"=").decode()
    mac_ok = hmac_mod.new(secret, payload_bytes_ok, "sha256")
    sig_b64_ok = base64.urlsafe_b64encode(mac_ok.digest()).rstrip(b"=").decode()
    valid_bearer = f"Bearer v1.{payload_b64_ok}.{sig_b64_ok}"

    async def run_ok():
        return await dep(authorization=valid_bearer)

    claims = asyncio.run(run_ok())
    assert isinstance(claims, TokenClaims)
    assert claims.service_id == "brainstorm"
# END_FUNCTION_test_require_service_dep_translates_auth_error
