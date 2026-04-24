# FILE: tests/smoke/test_mint_roundtrip.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for the mint_session_token -> verify_session_token roundtrip.
#          Verifies that a token minted by src.server.auth.mint_session_token can be
#          successfully verified by verify_session_token and produces the expected
#          TokenClaims. Also verifies that a tampered token raises AuthError.
#          NO network calls. NO subprocess.run for business logic.
# SCOPE: mint->verify roundtrip, claims field correctness, tampered-byte rejection.
# INPUT: src.server.auth.{mint_session_token, verify_session_token, TokenClaims, AuthError}
# OUTPUT: pytest PASS/FAIL assertions with LDD telemetry at IMP:7-10.
# KEYWORDS: [DOMAIN(9): TokenMint; DOMAIN(10): TokenVerify; PATTERN(9): RoundtripTest;
#            CONCEPT(9): ZeroKnowledge; TECH(8): HMAC_SHA256; PATTERN(8): TamperedByteRejection]
# LINKS: [TESTS: src/server/auth.py mint_session_token, verify_session_token]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.5 (Slice E scope), §1.1 (TokenContract)
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why is this named test_mint_roundtrip and NOT test_smoke_*?
# A: pytest would auto-collect test_smoke_brainstorm.py as part of L2 smoke.
#    The name test_mint_roundtrip explicitly signals a pure unit test with no network.
# Q: Why are both mint_session_token and verify_session_token imported from src.server.auth?
# A: These are the canonical production primitives. Testing their roundtrip through the
#    shared module guarantees wire-format consistency — any encoding drift in mint would
#    produce a token that verify rejects, and this test would catch it immediately.
# Q: Why @pytest.mark.unit?
# A: Enables selective test runs: pytest -m unit skips integration and network tests.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice E: roundtrip + tamper tests.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC  9  [Unit test: mint->verify happy path, claims field check] => test_mint_verify_roundtrip_happy_path
# FUNC  9  [Unit test: tampered byte in sig -> AuthError bad_signature] => test_tampered_sig_raises_auth_error
# FUNC  8  [Unit test: tampered byte in payload -> AuthError bad_signature] => test_tampered_payload_raises_auth_error
# FUNC  7  [Unit test: expired token -> AuthError expired] => test_expired_token_raises_auth_error
# FUNC  7  [Unit test: wrong service_id -> AuthError wrong_service] => test_wrong_service_raises_auth_error
# FUNC  6  [Unit test: TokenClaims shape invariant I5 — exactly 3 fields] => test_token_claims_strict_shape
# END_MODULE_MAP

import dataclasses
import time

import pytest

from src.server.auth import (
    AuthError,
    TokenClaims,
    _b64url_decode,
    _b64url_encode,
    mint_session_token,
    verify_session_token,
)


# Shared test fixtures — inline constants (no network, no tmp_path needed for pure HMAC tests)
_TEST_SECRET = b"test-hmac-secret-32bytes-minimum!"
_TEST_SERVICE_ID = "brainstorm"
_TEST_SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"
_FUTURE_EXP = int(time.time()) + 3600  # 1 hour from now — always in the future during test run


# START_FUNCTION_test_token_claims_strict_shape
# START_CONTRACT:
# PURPOSE: Assert I5 invariant: TokenClaims frozen dataclass contains EXACTLY
#          {service_id, session_id, exp} — no user_id, no iat, no other fields.
#          This test MUST pass before any mint/verify tests run.
# KEYWORDS: [CONCEPT(10): ZeroKnowledgeClaims; PATTERN(9): InvariantEnforcement]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@pytest.mark.unit
def test_token_claims_strict_shape() -> None:
    """
    I5 invariant check: TokenClaims must contain exactly three fields.
    Any addition of user_id, iat, or other PII fields is a zero-knowledge violation.
    This test is the structural guard for that invariant.
    """

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_token_claims_strict_shape] ---")
    print("[BRAINSTORM][IMP:9][test_token_claims_strict_shape][I5][Belief] "
          "TokenClaims MUST have exactly {service_id, session_id, exp} [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_SHAPE
    actual_fields = {f.name for f in dataclasses.fields(TokenClaims)}
    expected_fields = {"service_id", "session_id", "exp"}
    assert actual_fields == expected_fields, (
        f"I5 violation: TokenClaims has fields {actual_fields}, expected {expected_fields}. "
        "user_id and iat must never appear in TokenClaims."
    )
    # END_BLOCK_VERIFY_SHAPE

    print("[BRAINSTORM][IMP:9][test_token_claims_strict_shape][I5][Result] "
          f"fields={actual_fields} match expected [OK]")
# END_FUNCTION_test_token_claims_strict_shape


# START_FUNCTION_test_mint_verify_roundtrip_happy_path
# START_CONTRACT:
# PURPOSE: Verify that mint_session_token produces a token that verify_session_token
#          accepts and returns TokenClaims with the exact service_id, session_id, and
#          exp values that were passed to mint. This is the primary correctness test.
# KEYWORDS: [PATTERN(10): RoundtripTest; CONCEPT(10): WireCompat; PATTERN(9): FieldEquality]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.unit
def test_mint_verify_roundtrip_happy_path(caplog: pytest.LogCaptureFixture) -> None:
    """
    Full roundtrip: mint a token with known parameters, then verify it and assert
    that the resulting TokenClaims contains the exact same service_id, session_id,
    and exp values. Validates both the encoding path (mint) and decoding path (verify).
    """
    import logging
    caplog.set_level(logging.DEBUG)

    # START_BLOCK_MINT
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=_FUTURE_EXP,
    )
    # END_BLOCK_MINT

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_mint_verify_roundtrip_happy_path] ---")
    found_mint_log = False
    found_verify_log = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            try:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(record.message)
                if imp_level >= 9 and "verify_session_token" in record.message and "Verify-OK" in record.message:
                    found_verify_log = True
                if imp_level >= 3 and "mint_session_token" in record.message:
                    found_mint_log = True
            except (IndexError, ValueError):
                pass
    print("[BRAINSTORM][IMP:9][test_mint_verify_roundtrip_happy_path][RoundTrip][Belief] "
          f"token_structure_valid={token_str.startswith('v1.')} [CHECK]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY
    authorization_header = f"Bearer {token_str}"
    claims = verify_session_token(
        raw_authorization_header=authorization_header,
        required_service_id=_TEST_SERVICE_ID,
        now=int(time.time()),
        secret=_TEST_SECRET,
    )
    # END_BLOCK_VERIFY

    # START_BLOCK_ASSERTIONS
    assert isinstance(claims, TokenClaims), (
        f"verify_session_token must return TokenClaims, got {type(claims)}"
    )
    assert claims.service_id == _TEST_SERVICE_ID, (
        f"service_id mismatch: expected {_TEST_SERVICE_ID!r}, got {claims.service_id!r}"
    )
    assert claims.session_id == _TEST_SESSION_ID, (
        f"session_id mismatch: expected {_TEST_SESSION_ID!r}, got {claims.session_id!r}"
    )
    assert claims.exp == _FUTURE_EXP, (
        f"exp mismatch: expected {_FUTURE_EXP}, got {claims.exp}"
    )

    # Anti-illusion check: IMP:9 log for verify-OK must have been emitted
    # (re-check caplog after verify call — logs added after first print loop)
    for record in caplog.records:
        if "[IMP:" in record.message:
            try:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 9 and "verify_session_token" in record.message and "Verify-OK" in record.message:
                    found_verify_log = True
            except (IndexError, ValueError):
                pass

    assert found_verify_log, (
        "Critical LDD Error: verify_session_token did not emit [IMP:9] Verify-OK log. "
        "Either the token was rejected or the log invariant was broken."
    )

    print("[BRAINSTORM][IMP:9][test_mint_verify_roundtrip_happy_path][RoundTrip][Result] "
          f"claims={claims} roundtrip=OK [PASS]")
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_mint_verify_roundtrip_happy_path


# START_FUNCTION_test_tampered_sig_raises_auth_error
# START_CONTRACT:
# PURPOSE: Verify that flipping one byte in the signature portion of the token causes
#          verify_session_token to raise AuthError with reason="bad_signature".
#          This is the primary tamper-resistance test for HMAC integrity.
# KEYWORDS: [PATTERN(10): TamperTest; CONCEPT(10): ConstantTimeHMAC; PATTERN(9): ByteFlip]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.unit
def test_tampered_sig_raises_auth_error() -> None:
    """
    Tamper the signature portion of the token (part[2]) by XOR-ing the first byte.
    verify_session_token must raise AuthError with reason='bad_signature'.
    This validates that HMAC.compare_digest detects the manipulation.
    """

    # START_BLOCK_MINT_AND_TAMPER
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=_FUTURE_EXP,
    )

    parts = token_str.split(".")
    assert len(parts) == 3, f"Expected 3-part token, got {len(parts)}"

    # Decode signature, flip first byte, re-encode
    sig_bytes = bytearray(_b64url_decode(parts[2]))
    sig_bytes[0] ^= 0xFF
    parts[2] = _b64url_encode(bytes(sig_bytes))
    tampered_token = ".".join(parts)
    # END_BLOCK_MINT_AND_TAMPER

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_tampered_sig_raises_auth_error] ---")
    print("[BRAINSTORM][IMP:9][test_tampered_sig_raises_auth_error][TamperTest][Belief] "
          "Expect AuthError(bad_signature) on tampered sig [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_REJECTION
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer {tampered_token}",
            required_service_id=_TEST_SERVICE_ID,
            now=int(time.time()),
            secret=_TEST_SECRET,
        )

    assert exc_info.value.reason == "bad_signature", (
        f"Expected reason='bad_signature', got {exc_info.value.reason!r}. "
        "Tampered signature must be caught by HMAC compare_digest."
    )
    print("[BRAINSTORM][IMP:9][test_tampered_sig_raises_auth_error][TamperTest][Result] "
          f"reason={exc_info.value.reason!r} [PASS]")
    # END_BLOCK_VERIFY_REJECTION
# END_FUNCTION_test_tampered_sig_raises_auth_error


# START_FUNCTION_test_tampered_payload_raises_auth_error
# START_CONTRACT:
# PURPOSE: Verify that modifying the payload portion (part[1]) after the token is minted
#          causes verify_session_token to raise AuthError with reason="bad_signature".
#          Ensures that HMAC covers the payload, not just structural headers.
# KEYWORDS: [PATTERN(9): PayloadTamper; CONCEPT(10): HMACIntegrity]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.unit
def test_tampered_payload_raises_auth_error() -> None:
    """
    Tamper the payload portion of the token (part[1]) by XOR-ing the first byte.
    The original signature in part[2] is preserved but now covers a different payload.
    verify_session_token must raise AuthError with reason='bad_signature' because
    HMAC(secret, tampered_payload) != original_sig.
    """

    # START_BLOCK_MINT_AND_TAMPER
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=_FUTURE_EXP,
    )

    parts = token_str.split(".")
    assert len(parts) == 3

    # Decode payload, flip first byte, re-encode
    payload_bytes = bytearray(_b64url_decode(parts[1]))
    payload_bytes[0] ^= 0xFF
    parts[1] = _b64url_encode(bytes(payload_bytes))
    tampered_token = ".".join(parts)
    # END_BLOCK_MINT_AND_TAMPER

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_tampered_payload_raises_auth_error] ---")
    print("[BRAINSTORM][IMP:9][test_tampered_payload_raises_auth_error][TamperTest][Belief] "
          "Expect AuthError(bad_signature or malformed) on tampered payload [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_REJECTION
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer {tampered_token}",
            required_service_id=_TEST_SERVICE_ID,
            now=int(time.time()),
            secret=_TEST_SECRET,
        )

    # Payload tampering either breaks HMAC (bad_signature) or JSON decode (malformed)
    assert exc_info.value.reason in ("bad_signature", "malformed"), (
        f"Expected reason in ('bad_signature', 'malformed'), got {exc_info.value.reason!r}. "
        "Tampered payload must be rejected."
    )
    print("[BRAINSTORM][IMP:9][test_tampered_payload_raises_auth_error][TamperTest][Result] "
          f"reason={exc_info.value.reason!r} [PASS]")
    # END_BLOCK_VERIFY_REJECTION
# END_FUNCTION_test_tampered_payload_raises_auth_error


# START_FUNCTION_test_expired_token_raises_auth_error
# START_CONTRACT:
# PURPOSE: Verify that a token with exp in the past causes verify_session_token to
#          raise AuthError with reason="expired". Uses now+1 as exp so token is expired
#          immediately when verified at now+2 (simulated via explicit now= parameter).
# KEYWORDS: [PATTERN(8): ExpiryCheck; CONCEPT(8): TokenTTL]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.unit
def test_expired_token_raises_auth_error() -> None:
    """
    Mint a token with exp=1 (Unix epoch 1, far in the past) and verify it.
    verify_session_token must raise AuthError with reason='expired'.
    """

    # START_BLOCK_MINT_EXPIRED
    past_exp = int(time.time()) - 10  # 10 seconds in the past
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=past_exp,
    )
    # END_BLOCK_MINT_EXPIRED

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_expired_token_raises_auth_error] ---")
    print("[BRAINSTORM][IMP:9][test_expired_token_raises_auth_error][ExpiryTest][Belief] "
          f"Expect AuthError(expired) for exp={past_exp} now={int(time.time())} [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_REJECTION
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer {token_str}",
            required_service_id=_TEST_SERVICE_ID,
            now=int(time.time()),
            secret=_TEST_SECRET,
        )

    assert exc_info.value.reason == "expired", (
        f"Expected reason='expired', got {exc_info.value.reason!r}."
    )
    print("[BRAINSTORM][IMP:9][test_expired_token_raises_auth_error][ExpiryTest][Result] "
          f"reason={exc_info.value.reason!r} [PASS]")
    # END_BLOCK_VERIFY_REJECTION
# END_FUNCTION_test_expired_token_raises_auth_error


# START_FUNCTION_test_wrong_service_raises_auth_error
# START_CONTRACT:
# PURPOSE: Verify that a token minted for service_id="brainstorm" raises AuthError
#          with reason="wrong_service" when verified against required_service_id="other".
# KEYWORDS: [PATTERN(8): ServiceScopeCheck; CONCEPT(9): PathBasedAuthZ]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.unit
def test_wrong_service_raises_auth_error() -> None:
    """
    Mint a token for service_id='brainstorm', but verify it against required_service_id='other'.
    verify_session_token must raise AuthError with reason='wrong_service'.
    """

    # START_BLOCK_MINT
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=_FUTURE_EXP,
    )
    # END_BLOCK_MINT

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_wrong_service_raises_auth_error] ---")
    print("[BRAINSTORM][IMP:9][test_wrong_service_raises_auth_error][WrongService][Belief] "
          "Expect AuthError(wrong_service) when verifying 'brainstorm' token against 'other' [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_REJECTION
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer {token_str}",
            required_service_id="other_service",
            now=int(time.time()),
            secret=_TEST_SECRET,
        )

    assert exc_info.value.reason == "wrong_service", (
        f"Expected reason='wrong_service', got {exc_info.value.reason!r}."
    )
    print("[BRAINSTORM][IMP:9][test_wrong_service_raises_auth_error][WrongService][Result] "
          f"reason={exc_info.value.reason!r} [PASS]")
    # END_BLOCK_VERIFY_REJECTION
# END_FUNCTION_test_wrong_service_raises_auth_error


# START_FUNCTION_test_wrong_secret_raises_auth_error
# START_CONTRACT:
# PURPOSE: Verify that using a different secret to verify a token raises
#          AuthError with reason="bad_signature". This confirms that the secret
#          is integral to the HMAC — a token signed with secret A is not valid for secret B.
# KEYWORDS: [PATTERN(10): SecretIsolation; CONCEPT(10): HMAC_KeyBinding]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.unit
def test_wrong_secret_raises_auth_error() -> None:
    """
    Mint a token with _TEST_SECRET but attempt verification with a different secret.
    The HMAC over the payload will not match, so verify_session_token must raise
    AuthError with reason='bad_signature'.
    """

    # START_BLOCK_MINT
    token_str = mint_session_token(
        secret=_TEST_SECRET,
        service_id=_TEST_SERVICE_ID,
        session_id=_TEST_SESSION_ID,
        exp=_FUTURE_EXP,
    )
    different_secret = b"completely-different-secret-key!!"
    # END_BLOCK_MINT

    # START_BLOCK_LDD_TELEMETRY
    print("\n--- LDD TRAJECTORY (IMP:7-10) [test_wrong_secret_raises_auth_error] ---")
    print("[BRAINSTORM][IMP:9][test_wrong_secret_raises_auth_error][WrongSecret][Belief] "
          "Expect AuthError(bad_signature) when verifying with wrong secret [ASSERT]")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFY_REJECTION
    with pytest.raises(AuthError) as exc_info:
        verify_session_token(
            raw_authorization_header=f"Bearer {token_str}",
            required_service_id=_TEST_SERVICE_ID,
            now=int(time.time()),
            secret=different_secret,
        )

    assert exc_info.value.reason == "bad_signature", (
        f"Expected reason='bad_signature', got {exc_info.value.reason!r}."
    )
    print("[BRAINSTORM][IMP:9][test_wrong_secret_raises_auth_error][WrongSecret][Result] "
          f"reason={exc_info.value.reason!r} [PASS]")
    # END_BLOCK_VERIFY_REJECTION
# END_FUNCTION_test_wrong_secret_raises_auth_error
