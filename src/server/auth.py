# FILE: src/server/auth.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Python port of crablink-gateway kernel/sessiontoken/token.go v1.0.0 verifier,
#          with R4 zero-knowledge payload interpretation (drops user_id, drops iat).
#          Provides verify_session_token() for cryptographic HMAC-SHA256 token validation
#          and require_service() FastAPI Depends factory for route-level auth injection.
#          This module is the SOLE entry point for session-token verification — no other
#          module should parse Authorization headers or touch HMAC secrets.
# SCOPE: Bearer header parsing, base64url decoding with pad-repair, HMAC-SHA256
#        constant-time verification, payload JSON parsing, claims validation,
#        AuthError taxonomy, FastAPI Depends factory with stable identity cache.
# INPUT: Raw "Authorization: Bearer v1.<b64url_payload>.<b64url_sig>" header string.
# OUTPUT: TokenClaims(service_id, session_id, exp) frozen dataclass on success.
#         AuthError typed exception on any failure.
# KEYWORDS: [DOMAIN(10): SessionAuth; TECH(10): HMAC_SHA256; CONCEPT(10): ZeroKnowledgeClaims;
#            PATTERN(10): ConstantTimeHMAC; PATTERN(9): Base64url_PadRepair;
#            CONCEPT(9): BearerParsing; CONCEPT(8): AuthErrorTaxonomy]
# LINKS: [READS_DATA_FROM(10): crablink-gateway/kernel/sessiontoken/token.go v1.0.0;
#         USES_API(9): hmac.compare_digest; USES_API(9): base64.urlsafe_b64decode;
#         USES_API(8): src.server.config.Config]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §1.1 (TokenContract R4⊕R3),
#   §2.1 (Slice A scope), §6 (negative constraints), I5/I6/I7/I8/I10 invariants
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - I5: set(f.name for f in fields(TokenClaims)) == {"service_id", "session_id", "exp"}
#   NO user_id, NO iat, NO scope fields ever.
# - I6: After json.loads(payload): raw.pop("user_id", None); raw.pop("iat", None);
#   assert "user_id" not in raw and "iat" not in raw (structural zero-knowledge).
# - I7: hmac.compare_digest() is the ONLY comparison of signature bytes.
#   NEVER use == on sig bytes (timing-side-channel vulnerability).
# - I8: base64url decoding uses pad-repair: append "=" until len(s) % 4 == 0.
#   Go side uses RawURLEncoding (no padding); Python urlsafe_b64decode requires padding.
# - I9: No log line at IMP >= 5 may contain: raw Authorization header, raw HMAC secret,
#   raw API key, raw session_id. Only fingerprints: sha256:<first-8-hex>.
# - I10: Every AuthError has a .reason attribute from the approved taxonomy:
#   {malformed, bad_version, bad_signature, expired, wrong_service, missing_session}.
# - _DEPS_CACHE provides stable callable identity for dependency_overrides in tests.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why does TokenClaims have exactly {service_id, session_id, exp} and NOT user_id?
# A: R4 zero-knowledge collapse (plan §1.1): brainstorm must not know user identity.
#    user_id is present in the Go wire format but is explicitly popped after json.loads
#    and asserted absent. This is a structural architectural invariant, not an oversight.
# Q: Why base64.urlsafe_b64decode with manual pad-repair instead of base64.b64decode?
# A: Go uses base64.RawURLEncoding which omits padding. Python's urlsafe_b64decode
#    requires padding. Pad-repair (append "=" * (-len(s) % 4)) is the canonical fix
#    per I8. Standard b64decode uses + and / which differ from Go's url-safe alphabet.
# Q: Why hmac.compare_digest instead of ==?
# A: HMAC comparison via == is vulnerable to timing side-channel attacks: Python's
#    string == short-circuits on first differing byte, leaking info about the secret
#    through response time variance. hmac.compare_digest runs in constant time (mirrors
#    Go's crypto/subtle.ConstantTimeCompare and hmac.Equal behaviour).
# Q: Why is require_service a factory returning from _DEPS_CACHE?
# A: FastAPI dependency_overrides uses object identity. If each call creates a new
#    closure, test overrides fail silently. The cache ensures the same callable object
#    is returned for the same service_id, making overrides reliable.
# Q: Why is bearer parsing inside verify_session_token rather than the Depends factory?
# A: Plan §1.1 states: "Bearer-parsing is internal to verify_session_token".
#    This keeps the verifier testable in isolation (just pass the raw header string)
#    and the Depends factory dumb (just read header + delegate).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.1.0 - Slice E: added mint_session_token() and _b64url_encode() helpers
#               for dev tooling (scripts/mint_token.py). Public surface minimal — one new
#               function only. TokenClaims shape and verify_session_token unchanged.]
# PREV_CHANGE_SUMMARY: [v1.0.0 - Initial creation as Slice A: full HMAC verifier, TokenClaims
#               frozen dataclass, AuthError taxonomy, require_service factory with
#               _DEPS_CACHE stable identity. Zero-knowledge I5/I6 invariants enforced.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS  10  [Frozen dataclass: verified token claims {service_id, session_id, exp}] => TokenClaims
# CLASS   9  [Typed exception with .reason field for metric labeling] => AuthError
# FUNC   10  [Core verifier: parse Bearer, base64url-decode, HMAC-verify, pop PII, validate] => verify_session_token
# FUNC    9  [Dev-only mint: creates wire-format v1.<b64url_payload>.<b64url_sig> token] => mint_session_token
# FUNC    8  [FastAPI Depends factory: wraps verifier for route injection] => require_service
# FUNC    5  [Log-safe SHA-256 fingerprint of arbitrary bytes] => _token_fp
# FUNC    4  [Pad-repair for base64url strings from Go RawURLEncoding] => _b64url_decode
# FUNC    4  [Base64url encode without padding, matching Go RawURLEncoding] => _b64url_encode
# END_MODULE_MAP
#
# START_USE_CASES:
# - [verify_session_token]: HTTP handler -> verify_session_token(header, "brainstorm", now, secret)
#   -> TokenClaims on success, AuthError on failure -> metric increment + 401/403
# - [require_service]: route decorator -> Depends(require_service("brainstorm"))
#   -> claims injected into handler signature
# - [TokenClaims]: handler body -> claims.session_id -> load/create checkpoint thread
# END_USE_CASES

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Callable

logger = logging.getLogger(__name__)

# Compiled UUID-v4 pattern for session_id validation
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Stable identity cache for require_service factory (dependency_overrides compatibility)
_DEPS_CACHE: dict[str, Callable] = {}


# START_FUNCTION_TokenClaims (dataclass, not a function — documented for MODULE_MAP)
# START_CONTRACT:
# PURPOSE: Frozen dataclass holding verified session-token claims after successful
#          HMAC verification and R4 zero-knowledge payload reduction. Exactly three
#          fields: service_id, session_id, exp. NEVER contains user_id or iat.
# KEYWORDS: [CONCEPT(10): ZeroKnowledgeClaims; PATTERN(9): FrozenDataclass]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
@dataclasses.dataclass(frozen=True)
class TokenClaims:
    """
    Verified, zero-knowledge session-token claims produced by verify_session_token().

    Contains exactly three fields by architectural contract (I5 invariant):
    - service_id: Must equal "brainstorm" for this service; used for scope validation.
    - session_id: UUID-v4 string; used as LangGraph thread_id for checkpoint routing.
    - exp: Unix timestamp (seconds); expiry already validated by verify_session_token.

    user_id and iat from the Go wire format are explicitly dropped (I6 invariant).
    Frozen to prevent accidental mutation after construction.
    """

    service_id: str
    session_id: str
    exp: int

# END_FUNCTION_TokenClaims


# START_FUNCTION_AuthError (exception class — documented for MODULE_MAP)
# START_CONTRACT:
# PURPOSE: Typed exception with a machine-readable .reason attribute for Prometheus
#          metric labeling (brainstorm_token_verify_failures_total{reason=...}).
#          reason MUST be one of the six approved taxonomy values.
# INPUTS:
#   - reason: one of {malformed, bad_version, bad_signature, expired, wrong_service, missing_session}
#   - detail: optional human-readable context (never contains raw secrets)
# KEYWORDS: [CONCEPT(9): AuthErrorTaxonomy; PATTERN(8): TypedExceptionWithMetricLabel]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class AuthError(Exception):
    """
    Typed authentication exception carrying a machine-readable reason code for
    Prometheus metric labeling via brainstorm_token_verify_failures_total{reason=...}.

    The reason taxonomy is fixed and exhaustive:
    - malformed: token structure invalid (not 3 parts, not valid base64url)
    - bad_version: prefix is not "v1"
    - bad_signature: HMAC compare_digest returned False
    - expired: claims.exp <= now
    - wrong_service: claims.service_id != required_service_id
    - missing_session: claims.session_id is empty or not a valid UUID-v4

    detail is optional human context appended to the message; it MUST NOT contain
    raw secrets, raw tokens, or raw user data.
    """

    VALID_REASONS = frozenset(
        {"malformed", "bad_version", "bad_signature", "expired", "wrong_service", "missing_session"}
    )

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in self.VALID_REASONS:
            raise ValueError(
                f"AuthError reason '{reason}' is not in the approved taxonomy "
                f"{sorted(self.VALID_REASONS)}. This is a programming error."
            )
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason

# END_FUNCTION_AuthError


# START_FUNCTION__b64url_decode
# START_CONTRACT:
# PURPOSE: Decode a base64url-encoded string using pad-repair to handle Go's
#          RawURLEncoding output (no padding). Appends "=" characters until
#          len(s) % 4 == 0 before calling urlsafe_b64decode.
# INPUTS:
#   - base64url string without padding => s: str | bytes
# OUTPUTS:
#   - bytes: decoded raw bytes
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(9): Base64url_PadRepair; TECH(8): RawURLEncoding_compat]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def _b64url_decode(s: str | bytes) -> bytes:
    """
    Decode a base64url string produced by Go's base64.RawURLEncoding (no padding).
    Python's urlsafe_b64decode requires padding to a multiple of 4 bytes. We repair
    the padding by appending '=' characters: pad_len = (-len(s)) % 4.
    Uses urlsafe_b64decode which handles the url-safe alphabet (- and _ instead of + and /).
    Raises base64.binascii.Error (a ValueError subclass) on malformed input.
    """
    if isinstance(s, str):
        s = s.encode("ascii")
    pad_len = (-len(s)) % 4
    padded = s + b"=" * pad_len
    return base64.urlsafe_b64decode(padded)
# END_FUNCTION__b64url_decode


# START_FUNCTION__token_fp
# START_CONTRACT:
# PURPOSE: Produce a log-safe fingerprint of arbitrary bytes (token, session_id, etc.):
#          "sha256:<first-8-hex>". Used in every IMP >= 5 log line instead of raw values.
# INPUTS:
#   - raw bytes or string to fingerprint => data: bytes | str
# OUTPUTS:
#   - str: "sha256:<first-8-hex-chars>" (16 chars total after prefix)
# KEYWORDS: [CONCEPT(9): LogRedaction; PATTERN(8): TokenFingerprint]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def _token_fp(data: bytes | str) -> str:
    """
    Generate a log-safe fingerprint: sha256 hex of data, truncated to 8 chars.
    This provides enough entropy for log correlation without leaking the raw value.
    Never log more than 8 hex chars of a secret fingerprint (reduces brute-force surface).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()[:8]
# END_FUNCTION__token_fp


# START_FUNCTION_verify_session_token
# START_CONTRACT:
# PURPOSE: Parse, cryptographically verify, and semantically validate a v1 session token
#          from the raw Authorization header value. Returns TokenClaims on success.
#          Implements I5 (exact fields), I6 (pop user_id/iat), I7 (constant-time HMAC),
#          I8 (pad-repair base64url), I10 (LDD logging format).
# INPUTS:
#   - Raw "Authorization: Bearer v1.<p>.<s>" header or None => raw_authorization_header: str | None
#   - Expected service_id value => required_service_id: str
#   - Current Unix timestamp in seconds => now: int
#   - HMAC secret bytes => secret: bytes
# OUTPUTS:
#   - TokenClaims: Verified, zero-knowledge claims on success.
# SIDE_EFFECTS: Logs at IMP:9 for both failure and success (AI Belief State entries).
# KEYWORDS: [DOMAIN(10): TokenVerify; PATTERN(10): ConstantTimeHMAC; PATTERN(9): Base64url;
#            CONCEPT(10): ZeroKnowledgeR4; PATTERN(8): BearerParsing]
# LINKS: [READS_DATA_FROM(10): Go token.go Validate function for wire-format reference]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
def verify_session_token(
    raw_authorization_header: str | None,
    required_service_id: str,
    now: int,
    secret: bytes,
) -> TokenClaims:
    """
    Verify a v1 session token from the Authorization Bearer header. This is a Python
    port of crablink-gateway/kernel/sessiontoken/token.go Validate() with R4 zero-
    knowledge payload reduction.

    Processing pipeline:
    1. Assert header present and starts with "Bearer " (else malformed).
    2. Strip "Bearer " prefix to get raw token string.
    3. Split on "." — must produce exactly 3 parts (else malformed).
    4. Assert parts[0] == "v1" (else bad_version).
    5. Base64url-decode parts[1] (payload) and parts[2] (sig) with pad-repair.
       Any decode error -> malformed.
    6. HMAC-SHA256(secret, payload_bytes) and hmac.compare_digest with sig_bytes.
       Mismatch -> bad_signature.
    7. json.loads(payload_bytes) -> dict raw.
       Immediately: raw.pop("user_id", None); raw.pop("iat", None).
       Assert both absent (I6 zero-knowledge structural check).
    8. Construct TokenClaims from raw["service_id"], raw["session_id"], int(raw["exp"]).
    9. Validate claims.service_id == required_service_id (else wrong_service).
    10. Validate claims.session_id non-empty and matches UUID-v4 regex (else missing_session).
    11. Validate claims.exp > now (else expired).
    12. Log IMP:9 BELIEF on success. Return TokenClaims.

    Any step that raises logs at IMP:9 [Auth][Verify-Failed] with reason and token_fp.
    """

    # START_BLOCK_HEADER_PARSE: [Parse Bearer header and extract raw token]
    if not raw_authorization_header:
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=missing_authorization_header [FAIL]"
        )
        raise AuthError("malformed", "Authorization header is absent")

    if not raw_authorization_header.startswith("Bearer "):
        fp = _token_fp(raw_authorization_header[:32])
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=not_bearer_scheme header_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", "Authorization header must use Bearer scheme")

    raw_token = raw_authorization_header[len("Bearer "):]
    # END_BLOCK_HEADER_PARSE

    # START_BLOCK_STRUCTURE_CHECK: [Split and version-check the token]
    parts = raw_token.split(".")
    if len(parts) != 3:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=wrong_part_count parts={len(parts)} token_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", f"Expected 3 dot-separated parts, got {len(parts)}")

    if parts[0] != "v1":
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=bad_version version_prefix={parts[0]!r} token_fp={fp} [FAIL]"
        )
        raise AuthError("bad_version", f"Unsupported version prefix '{parts[0]}', expected 'v1'")
    # END_BLOCK_STRUCTURE_CHECK

    # START_BLOCK_BASE64_DECODE: [Decode payload and signature bytes with pad-repair]
    try:
        payload_bytes = _b64url_decode(parts[1])
    except Exception as exc:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=payload_base64_error err={exc!r} token_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", f"Payload base64url decode failed: {exc}") from exc

    try:
        sig_bytes = _b64url_decode(parts[2])
    except Exception as exc:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=sig_base64_error err={exc!r} token_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", f"Signature base64url decode failed: {exc}") from exc
    # END_BLOCK_BASE64_DECODE

    # START_BLOCK_HMAC_VERIFY: [Constant-time HMAC-SHA256 verification — I7]
    computed_mac = hmac.new(secret, payload_bytes, "sha256").digest()
    # I7: MUST use hmac.compare_digest — never == for signature bytes
    if not hmac.compare_digest(computed_mac, sig_bytes):
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=bad_signature token_fp={fp} [FAIL]"
        )
        raise AuthError("bad_signature", "HMAC signature mismatch")
    # END_BLOCK_HMAC_VERIFY

    # START_BLOCK_PAYLOAD_PARSE: [JSON decode and R4 zero-knowledge pop — I6]
    try:
        raw: dict = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=payload_json_invalid err={exc!r} token_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", f"Payload JSON decode failed: {exc}") from exc

    # I6: Immediately pop PII fields — zero-knowledge structural invariant
    raw.pop("user_id", None)
    raw.pop("iat", None)
    # Structural assertion: these fields must not survive into TokenClaims construction
    assert "user_id" not in raw and "iat" not in raw, (
        "I6 violation: user_id or iat survived pop() — this is a code defect"
    )
    # END_BLOCK_PAYLOAD_PARSE

    # START_BLOCK_CLAIMS_CONSTRUCTION: [Build TokenClaims from reduced payload]
    try:
        service_id = str(raw["service_id"])
        session_id = str(raw["session_id"])
        exp = int(raw["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=malformed detail=missing_required_claim err={exc!r} token_fp={fp} [FAIL]"
        )
        raise AuthError("malformed", f"Required claim missing or invalid type: {exc}") from exc
    # END_BLOCK_CLAIMS_CONSTRUCTION

    # START_BLOCK_SEMANTIC_VALIDATION: [Validate service_id, session_id, expiry]
    if service_id != required_service_id:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=wrong_service got={service_id!r} expected={required_service_id!r} "
            f"token_fp={fp} [FAIL]"
        )
        raise AuthError("wrong_service", f"Token service_id '{service_id}' != required '{required_service_id}'")

    if not session_id or not _UUID4_RE.match(session_id):
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=missing_session session_fp={_token_fp(session_id)} token_fp={fp} [FAIL]"
        )
        raise AuthError("missing_session", "session_id is missing or not a valid UUID-v4")

    if exp <= now:
        fp = _token_fp(raw_token.encode("utf-8"))
        logger.warning(
            f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] "
            f"reason=expired exp={exp} now={now} delta={now - exp}s token_fp={fp} [FAIL]"
        )
        raise AuthError("expired", f"Token expired {now - exp}s ago (exp={exp}, now={now})")
    # END_BLOCK_SEMANTIC_VALIDATION

    # START_BLOCK_SUCCESS_LOG: [AI Belief State — IMP:9 on successful verification]
    claims = TokenClaims(service_id=service_id, session_id=session_id, exp=exp)
    session_fp = _token_fp(session_id)
    token_fp = _token_fp(raw_token.encode("utf-8"))
    exp_delta = exp - now

    logger.info(
        f"[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-OK][BELIEF] "
        f"token_fp={token_fp} session_fp={session_fp} "
        f"service_id={service_id} exp_delta_s={exp_delta} [OK]"
    )
    return claims
    # END_BLOCK_SUCCESS_LOG

# END_FUNCTION_verify_session_token


# START_FUNCTION__b64url_encode
# START_CONTRACT:
# PURPOSE: Encode bytes to a base64url string WITHOUT padding, matching Go's
#          base64.RawURLEncoding output that is consumed by verify_session_token
#          on the decode side. Inverse of _b64url_decode.
# INPUTS:
#   - Raw bytes to encode => data: bytes
# OUTPUTS:
#   - str: base64url-encoded string without trailing '=' characters
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(9): Base64url_RawEncoding; TECH(8): GoRawURLEncoding_compat]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def _b64url_encode(data: bytes) -> str:
    """
    Encode bytes using url-safe base64 WITHOUT padding, mirroring Go's
    base64.RawURLEncoding. The resulting string uses '-' and '_' in place of
    '+' and '/', and has all trailing '=' padding characters stripped.
    This is the exact inverse of _b64url_decode which performs pad-repair
    before decoding.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
# END_FUNCTION__b64url_encode


# START_FUNCTION_mint_session_token
# START_CONTRACT:
# PURPOSE: Dev-only helper that constructs a wire-format v1 session token identical
#          to those produced by crablink-gateway/kernel/sessiontoken/token.go.
#          Used exclusively by scripts/mint_token.py and tests/smoke/test_mint_roundtrip.py.
#          NOT intended for production code paths — production tokens are minted by gateway.
# INPUTS:
#   - Raw HMAC secret bytes used to sign the token => secret: bytes
#   - Service identifier to embed in the payload (default "brainstorm") => service_id: str
#   - UUID-v4 session identifier => session_id: str
#   - Unix timestamp (seconds) at which the token expires => exp: int
# OUTPUTS:
#   - str: Wire-format token "v1.<b64url_payload>.<b64url_sig>" with no whitespace or newlines.
# SIDE_EFFECTS: Logs at IMP:4 (dev tooling — trace level, never in prod paths).
# KEYWORDS: [DOMAIN(9): TokenMint; TECH(10): HMAC_SHA256; PATTERN(9): GoWireCompat;
#            CONCEPT(8): DevHelper]
# LINKS: [INVERSE_OF: verify_session_token; USES_API(8): hmac.new; USES_API(8): json.dumps]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def mint_session_token(
    secret: bytes,
    service_id: str,
    session_id: str,
    exp: int,
) -> str:
    """
    Construct a v1 session token wire-compatible with crablink-gateway token.go v1.0.0.

    Wire format: "v1.<b64url_payload>.<b64url_sig>"
      - payload  : base64url(RawURLEncoding) of compact JSON {"service_id":..., "session_id":..., "exp":...}
      - sig      : base64url(RawURLEncoding) of HMAC-SHA256(secret, payload_bytes)

    The payload JSON is serialised with sorted keys and no spaces (compact) to guarantee
    that the HMAC covers a deterministic byte sequence — the same sequence Go token.go
    would produce when using json.Marshal on the same struct fields.

    Note: The Go gateway token.go also embeds user_id and iat in the wire payload.
    This mint helper intentionally omits those fields because:
    1. Brainstorm is a zero-knowledge domain (R4 invariant).
    2. verify_session_token pops user_id and iat immediately after decode (I6).
    3. Their absence does not affect HMAC validity — HMAC covers whatever bytes are
       present in the payload, and verify_session_token only checks service_id/session_id/exp.

    This function is strictly for developer tooling and integration tests. Never call
    it from production request handlers or application startup code.
    """

    # START_BLOCK_PAYLOAD_BUILD: [Construct compact JSON payload matching Go wire format]
    payload_dict = {
        "exp": exp,
        "service_id": service_id,
        "session_id": session_id,
    }
    # Compact JSON with sorted keys, no spaces — deterministic byte sequence for HMAC
    payload_json: str = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
    payload_bytes: bytes = payload_json.encode("utf-8")
    # END_BLOCK_PAYLOAD_BUILD

    # START_BLOCK_SIGN: [HMAC-SHA256 the payload bytes with the shared secret]
    sig_bytes: bytes = hmac.new(secret, payload_bytes, "sha256").digest()
    # END_BLOCK_SIGN

    # START_BLOCK_ENCODE: [Base64url-encode without padding — Go RawURLEncoding compat]
    payload_b64 = _b64url_encode(payload_bytes)
    sig_b64 = _b64url_encode(sig_bytes)
    token = f"v1.{payload_b64}.{sig_b64}"
    # END_BLOCK_ENCODE

    # START_BLOCK_TRACE_LOG: [Dev-tooling trace log — IMP:4, never in prod path]
    session_fp = _token_fp(session_id)
    logger.debug(
        f"[BRAINSTORM][IMP:4][mint_session_token][TokenMint][Build] "
        f"service_id={service_id} session_fp={session_fp} exp={exp} "
        f"token_len={len(token)} [OK]"
    )
    # END_BLOCK_TRACE_LOG

    return token
# END_FUNCTION_mint_session_token


# START_FUNCTION_require_service
# START_CONTRACT:
# PURPOSE: FastAPI Depends factory returning a stable closure that reads the
#          Authorization header and delegates to verify_session_token. Uses
#          _DEPS_CACHE for stable callable identity (required for dependency_overrides
#          to work correctly in tests — per plan §1.2).
# INPUTS:
#   - Required service identifier => service_id: str
# OUTPUTS:
#   - Callable: async dependency closure that returns TokenClaims
# SIDE_EFFECTS: Populates _DEPS_CACHE on first call per service_id.
# KEYWORDS: [PATTERN(9): DependsFactory; PATTERN(8): StableIdentityCache;
#            TECH(8): FastAPI_Depends]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def require_service(service_id: str) -> Callable:
    """
    FastAPI Depends factory for per-route authentication. Returns a closure that:
    1. Reads the Authorization header via FastAPI's Header injection.
    2. Reads the Config singleton for hmac_secret.
    3. Calls verify_session_token with current time and the secret bytes.
    4. On AuthError: raises HTTPException(401) for most reasons; 403 for wrong_service.

    Uses _DEPS_CACHE with setdefault to guarantee the same callable object is returned
    for the same service_id — this is required for FastAPI's dependency_overrides to
    work reliably in tests (identity comparison, not equality).

    The closure captures service_id in its closure scope, not as a mutable global.
    """

    def _make_dep(sid: str) -> Callable:
        """Create the inner async dependency closure for service_id=sid."""
        # Import here to avoid circular imports at module load time; these are
        # imported once when _make_dep() is called (i.e., at first require_service call).
        from fastapi import Header as _Header, HTTPException as _HTTPException  # noqa: PLC0415
        from src.server.config import get_cfg as _get_cfg  # noqa: PLC0415

        async def _dep(
            authorization: str | None = _Header(default=None, alias="Authorization"),
        ) -> TokenClaims:
            """
            Inner FastAPI dependency: extracts Authorization header via FastAPI Header(),
            calls verify_session_token, translates AuthError to HTTPException.
            """
            cfg = _get_cfg()
            secret = cfg.hmac_secret.get_secret_value().encode("utf-8")
            now = int(time.time())

            try:
                claims = verify_session_token(
                    raw_authorization_header=authorization,
                    required_service_id=sid,
                    now=now,
                    secret=secret,
                )
                return claims
            except AuthError as exc:
                # wrong_service is a 403 (authenticated but wrong scope);
                # all other reasons are 401 (unauthenticated / bad token)
                http_status = 403 if exc.reason == "wrong_service" else 401
                raise _HTTPException(
                    status_code=http_status,
                    detail={"error": exc.reason, "message": str(exc)},
                ) from exc

        return _dep

    return _DEPS_CACHE.setdefault(service_id, _make_dep(service_id))

# END_FUNCTION_require_service
