#!/usr/bin/env bash
# FILE: scripts/verify_zero_knowledge.sh
# VERSION: 2.0.0
# PURPOSE: Enforce AC1 (no user identity leakage) and AC7 (no billing vocabulary)
#          inside src/server/ — the R4 zero-knowledge enforcement perimeter.
# SCOPE:   src/server/*.py (excludes tests, __pycache__, legacy src/features|ui|core).
# EXIT:    0 on clean, 1 on violation.
#
# RATIONALE:
#   Previous v1.0 scanned all of src/ which produced a false positive on the word
#   "balanced" appearing in comments inside src/core/json_utils.py (a legacy JSON
#   parsing helper outside the R4 enforcement perimeter).
#
#   AC1 user_id pattern uses precise anchors (\.user_id, user_id\s*[=:], userID, UserID)
#   rather than a bare substring, so that auth.py's own enforcement code —
#   raw.pop("user_id", None) and assert "user_id" not in raw — does NOT trigger the gate.
#   Those lines implement the zero-knowledge invariant; they must not be mistaken for
#   violations of it.
#
#   AC7 billing vocabulary uses \b word-boundary regex so that common English words
#   like "balanced" or "credited" in comments do not fire on the billing check.
#
# CHANGE_SUMMARY:
#   v2.0.0 - Scope narrowed to src/server/ only; AC1 pattern made precise;
#             AC7 pattern hardened with word boundaries; false positive eliminated.
#   v1.0.0 - Initial creation (Slice A): scanned full src/, bare-substring patterns.

set -e
cd "$(dirname "$0")/.."

# AC1: user identity leakage — precise anchors, not bare substring.
# Catches: claims.user_id  user.id  userID  UserID  user_id = ...  user_id: ...
# Does NOT catch: raw.pop("user_id", None) or "user_id" inside assert message strings.
AC1_PATTERN='(\.user_id|user\.id|userID|UserID|user_id\s*[=:])'

# AC7: billing vocabulary — word boundaries prevent matching "balanced", "credited", etc.
AC7_PATTERN='(⚡|\b(energy|billing|credits?|balances?|deducts?|debits?|ruble|rubles|stars|deposit|withdraw)\b)'

COMBINED_PATTERN="(${AC1_PATTERN}|${AC7_PATTERN})"

if grep -rInE --include='*.py' \
           --exclude-dir=__pycache__ \
           --exclude-dir=tests \
           --exclude-dir=.pytest_cache \
           "$COMBINED_PATTERN" src/server/; then
    echo ""
    echo "[ZK_CHECK][FAIL] Forbidden domain terms detected in src/server/."
    echo "R4 (zero-knowledge identity) and AC7 (zero billing vocabulary) violated."
    exit 1
fi

echo "[ZK_CHECK][PASS] src/server/ clean."
exit 0
