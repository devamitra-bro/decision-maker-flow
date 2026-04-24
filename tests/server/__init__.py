# FILE: tests/server/__init__.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Package marker for the tests/server/ test suite. Ensures pytest discovers
#          tests in this directory and enables clean absolute imports of src.server.*
#          without sys.path hacks (conftest.py handles path injection).
# SCOPE: Package initialisation only — no test logic.
# INPUT: None.
# OUTPUT: Package namespace tests.server.* available for pytest collection.
# KEYWORDS: [DOMAIN(6): TestInfra; TECH(5): PackageMarker]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §5.1 (tests/server/ layout)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice A test package marker.]
# END_CHANGE_SUMMARY
