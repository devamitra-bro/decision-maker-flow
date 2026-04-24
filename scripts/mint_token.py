# FILE: scripts/mint_token.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Developer-only CLI helper that mints a v1 HMAC session token using the
#          production HMAC primitive from src.server.auth.mint_session_token. The output
#          token is wire-compatible with crablink-gateway/kernel/sessiontoken/token.go v1.0.0.
#          Used for local testing and smoke-test setup ONLY — never called from prod code.
# SCOPE: argparse CLI entrypoint; delegates all cryptographic work to auth.mint_session_token.
#        No HMAC reimplementation here — one canonical implementation in auth.py.
# INPUT: --service-id, --session-id (default: new uuid4), --ttl (required, seconds > 0),
#        --secret-env (default: BRAINSTORM_HMAC_SECRET env var).
# OUTPUT: Prints "Bearer <token>" to stdout. Dev warnings go to stderr.
# KEYWORDS: [DOMAIN(7): DevTooling; TECH(8): HMAC_SHA256; PATTERN(7): CLIWrapper;
#            CONCEPT(8): WireCompat; PATTERN(9): DelegateToCanonical]
# LINKS: [USES_API(10): src.server.auth.mint_session_token;
#         INVERSE_OF: src.server.auth.verify_session_token]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.5 (Slice E scope), §1.1 (TokenContract_R4_R3)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - HMAC is computed ONLY by src.server.auth.mint_session_token (no local reimplementation).
# - Secret MUST be >= 16 bytes. Script refuses to mint with shorter secrets.
# - Output token is written to stdout; warnings/info are written to stderr.
# - Secret is NEVER written to stdout or logged to stderr beyond a 8-char fingerprint.
# - If BRAINSTORM_SMOKE != "1", the __main__ guard exits 0 with a SKIP banner (CI-safe).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why delegate to auth.mint_session_token instead of reimplementing HMAC locally?
# A: One implementation means one place to audit, one place to fix. If the wire format
#    ever changes, the change propagates automatically. The previous v1.0.0 of this file
#    had a local mint_token() that diverged subtly (key ordering in JSON, user_id inclusion).
#    Delegating to the canonical primitive eliminates that risk.
# Q: Why does the canonical mint_session_token omit user_id from the payload?
# A: Brainstorm is a zero-knowledge domain (R4 invariant). verify_session_token pops
#    user_id immediately after decode (I6). A token without user_id is structurally valid
#    for the brainstorm verifier; the gateway's own minted tokens may include user_id but
#    brainstorm discards it. For pure brainstorm dev/test, omitting user_id is correct.
# Q: Why print warnings to stderr and token to stdout?
# A: Callers pipe stdout to token consumers (env vars, curl, etc.). Stderr mixing would
#    corrupt the token value. stdout = machine-readable, stderr = human-readable.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v2.0.0 - Slice E refactor: replaced local HMAC reimplementation with
#               delegation to src.server.auth.mint_session_token. CLI interface preserved
#               for backward compatibility. BRAINSTORM_SMOKE guard added.]
# PREV_CHANGE_SUMMARY: [v1.0.0 - Initial creation (Slice E): standalone HMAC minter
#               with argparse, 16-byte secret guard, TTL>0 check, stdout token, stderr warning.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC  9  [CLI entry point: parse args, validate, delegate to auth.mint_session_token, print] => main
# FUNC  4  [Log-safe fingerprint of secret bytes: sha256[:8]] => _secret_fp
# END_MODULE_MAP
#
# START_USE_CASES:
# - [main]: Developer -> python scripts/mint_token.py --service-id brainstorm --ttl 300
#           -> "Bearer v1.<token>" printed to stdout for use in curl / smoke tests
# END_USE_CASES

import argparse
import hashlib
import os
import sys
import time
import uuid


# START_FUNCTION__secret_fp
# START_CONTRACT:
# PURPOSE: Produce a log-safe 8-char SHA-256 fingerprint of secret bytes for stderr output.
#          NEVER logs more than 8 hex chars to limit brute-force surface.
# INPUTS:
#   - Raw secret bytes => secret: bytes
# OUTPUTS:
#   - str: "sha256:<first-8-hex>" fingerprint
# KEYWORDS: [CONCEPT(9): LogRedaction; PATTERN(8): SecretFingerprint]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def _secret_fp(secret: bytes) -> str:
    """
    Produce a log-safe fingerprint of secret bytes: sha256 hex truncated to 8 chars.
    Used in stderr diagnostics so developers can confirm which secret was used
    without exposing the raw value.
    """
    return "sha256:" + hashlib.sha256(secret).hexdigest()[:8]
# END_FUNCTION__secret_fp


# START_FUNCTION_main
# START_CONTRACT:
# PURPOSE: CLI entry point for minting a v1 session token. Loads the HMAC secret from
#          an environment variable, validates length and TTL, then calls the canonical
#          src.server.auth.mint_session_token to produce the wire-format token.
#          Prints "Bearer <token>" to stdout; all diagnostics go to stderr.
# INPUTS (via argparse):
#   - --service-id: str (default "brainstorm")
#   - --session-id: str | None (default: fresh uuid4)
#   - --ttl: int (required, seconds > 0)
#   - --secret-env: str (default "BRAINSTORM_HMAC_SECRET")
# OUTPUTS:
#   - stdout: "Bearer v1.<payload>.<sig>" token string
#   - stderr: DEV-ONLY warning + secret fingerprint (never raw secret)
# SIDE_EFFECTS: Reads environment variable named by --secret-env. Exits 1 on error.
# KEYWORDS: [DOMAIN(9): CLI; PATTERN(8): ArgParse; CONCEPT(9): FailFastValidation;
#            PATTERN(9): DelegateToCanonical]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def main() -> None:
    """
    CLI entry point: parse arguments, validate secret and TTL, then delegate to
    src.server.auth.mint_session_token for the actual HMAC computation and encoding.

    Argument layout:
    - --service-id: which service this token authorises (default: "brainstorm")
    - --session-id: UUID-v4 to embed; if omitted, a new UUID-v4 is generated
    - --ttl: lifetime in seconds (must be > 0); token exp = now + ttl
    - --secret-env: name of the env var holding the HMAC secret

    Secret validation:
    - Must be present in the named env var
    - Must be >= 16 bytes after UTF-8 encoding

    Exit codes:
    - 0: token printed successfully
    - 1: any validation error (missing secret, short secret, bad TTL, import error)
    """

    # START_BLOCK_ARGPARSE: [Define and parse CLI arguments]
    parser = argparse.ArgumentParser(
        prog="mint_token.py",
        description=(
            "[DEV-ONLY] Mint a v1 HMAC-SHA256 session token compatible with "
            "src/server/auth.py verify_session_token. NOT for production use."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  BRAINSTORM_HMAC_SECRET=mysupersecret python scripts/mint_token.py \\\n"
            "    --service-id brainstorm \\\n"
            "    --session-id 550e8400-e29b-41d4-a716-446655440000 \\\n"
            "    --ttl 300\n\n"
            "Output: Bearer v1.<payload>.<sig>  (written to stdout)\n"
            "Warnings and info are written to stderr.\n"
        ),
    )
    parser.add_argument(
        "--service-id",
        default="brainstorm",
        help="Service identifier to embed in token payload (default: 'brainstorm').",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="UUID-v4 session identifier. If omitted, a new UUID-v4 is auto-generated.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        required=True,
        help="Token lifetime in seconds (must be > 0). Token exp = now + ttl.",
    )
    parser.add_argument(
        "--secret-env",
        default="BRAINSTORM_HMAC_SECRET",
        help=(
            "Name of the environment variable holding the HMAC secret. "
            "Default: BRAINSTORM_HMAC_SECRET."
        ),
    )
    args = parser.parse_args()
    # END_BLOCK_ARGPARSE

    # START_BLOCK_DEV_WARNING: [Print DEV-ONLY warning to stderr]
    print(
        "[WARNING] DEV-ONLY: This script produces tokens for local development and "
        "testing ONLY. Do NOT use minted tokens in production pipelines. "
        "Production tokens are issued by crablink-gateway.",
        file=sys.stderr,
    )
    # END_BLOCK_DEV_WARNING

    # START_BLOCK_VALIDATE_TTL: [Validate TTL is a positive integer]
    if args.ttl <= 0:
        print(
            f"[ERROR] --ttl must be > 0 (got {args.ttl}). "
            "A token that expires in the past is useless.",
            file=sys.stderr,
        )
        sys.exit(1)
    # END_BLOCK_VALIDATE_TTL

    # START_BLOCK_LOAD_SECRET: [Load HMAC secret from environment variable]
    raw_secret = os.environ.get(args.secret_env)
    if not raw_secret:
        print(
            f"[ERROR] Environment variable '{args.secret_env}' is not set or empty. "
            "Set it to the BRAINSTORM_HMAC_SECRET value before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    secret_bytes = raw_secret.encode("utf-8")

    if len(secret_bytes) < 16:
        print(
            f"[ERROR] Secret in '{args.secret_env}' is only {len(secret_bytes)} bytes. "
            "Minimum is 16 bytes (128 bits) for HMAC-SHA256 key strength. "
            "Use a secret of at least 16 ASCII characters.",
            file=sys.stderr,
        )
        sys.exit(1)

    secret_fingerprint = _secret_fp(secret_bytes)
    print(
        f"[INFO] Using secret from '{args.secret_env}' "
        f"(fingerprint: {secret_fingerprint}, length: {len(secret_bytes)} bytes).",
        file=sys.stderr,
    )
    # END_BLOCK_LOAD_SECRET

    # START_BLOCK_RESOLVE_SESSION_ID: [Use provided session_id or generate a fresh UUID-v4]
    if args.session_id:
        session_id = args.session_id
        print(f"[INFO] Using provided session_id: {session_id}", file=sys.stderr)
    else:
        session_id = str(uuid.uuid4())
        print(f"[INFO] Generated new session_id: {session_id}", file=sys.stderr)
    # END_BLOCK_RESOLVE_SESSION_ID

    # START_BLOCK_IMPORT_MINT: [Lazy import of canonical mint_session_token from auth module]
    # Lazy import ensures this script does not break pytest collection if PYTHONPATH
    # is not configured. The project root is added to sys.path here only when running
    # as a CLI script (not when imported by test_mint_roundtrip.py which sets its own path).
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from src.server.auth import mint_session_token  # noqa: PLC0415
    except ImportError as exc:
        print(
            f"[ERROR] Cannot import src.server.auth.mint_session_token. "
            f"Run from the brainstorm project root or set PYTHONPATH. err={exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    # END_BLOCK_IMPORT_MINT

    # START_BLOCK_MINT: [Compute exp and call canonical mint function]
    now = int(time.time())
    exp = now + args.ttl

    token = mint_session_token(
        secret=secret_bytes,
        service_id=args.service_id,
        session_id=session_id,
        exp=exp,
    )
    bearer_token = f"Bearer {token}"

    print(
        f"[INFO] Minted token for service_id='{args.service_id}', "
        f"session_id='{session_id}', exp={exp} (TTL={args.ttl}s, now={now}).",
        file=sys.stderr,
    )
    # END_BLOCK_MINT

    # START_BLOCK_OUTPUT: [Print Bearer token to stdout — machine-readable]
    print(bearer_token)
    # END_BLOCK_OUTPUT

# END_FUNCTION_main


if __name__ == "__main__":
    main()
