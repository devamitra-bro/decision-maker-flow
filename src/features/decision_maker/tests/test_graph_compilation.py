# FILE: src/features/decision_maker/tests/test_graph_compilation.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite for build_graph() compilation integrity (AC5, AC9, AC10).
# SCOPE: Verifies: graph compiles without exception; all 7 node IDs are present;
#        checkpointer is BaseCheckpointSaver (accepts MemorySaver in v2.0.0); interrupt_after is configured.
# INPUT: memory_checkpointer fixture (MemorySaver — no SQLite DB file); no LLM calls.
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): GraphCompilation; TECH(9): MemorySaver;
#            PATTERN(7): IntegrationTest; PATTERN(8): DependencyInjection]
# LINKS: [READS_DATA_FROM(10): src.features.decision_maker.graph.build_graph;
#         USES_API(9): langgraph.checkpoint.memory.MemorySaver;
#         USES_API(8): langgraph.checkpoint.base.BaseCheckpointSaver]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 3; AC5, AC9, AC10
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — async migration; build_graph now accepts checkpointer via DI;
#              tests use memory_checkpointer fixture (MemorySaver) instead of tmp_path SQLite;
#              isinstance check updated to BaseCheckpointSaver (abstract superclass of both
#              MemorySaver and AsyncSqliteSaver — AC10).
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; compilation + topology + checkpointer tests.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Test: build_graph(memory_checkpointer) compiles and returns CompiledStateGraph] => test_graph_compiles
# FUNC 9 [Test: all 7 node IDs are present in compiled graph] => test_all_node_ids_present
# FUNC 9 [Test: checkpointer is BaseCheckpointSaver instance] => test_checkpointer_is_base_checkpoint_saver
# FUNC 8 [Test: interrupt_after is configured for 3_Weight_Questioner] => test_interrupt_after_configured
# END_MODULE_MAP

import logging
import pytest

from langgraph.checkpoint.base import BaseCheckpointSaver

from src.features.decision_maker.graph import build_graph


# START_FUNCTION_test_graph_compiles
# START_CONTRACT:
# PURPOSE: Verify build_graph(memory_checkpointer) succeeds without raising exceptions
#          and returns a compiled graph object.
# INPUTS:
# - memory_checkpointer fixture => memory_checkpointer: MemorySaver
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): GraphCompilation; PATTERN(7): SmokeTest; PATTERN(8): DependencyInjection]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_graph_compiles(memory_checkpointer, caplog, ldd_capture):
    """
    Smoke test: build_graph(checkpointer) with a MemorySaver must complete without raising
    any exception and return a non-None compiled graph object that has both 'invoke' and
    'ainvoke' methods (async graph).

    Uses memory_checkpointer fixture (MemorySaver) instead of tmp_path SQLite — DI pattern
    per v2.0.0 contract (AC10).
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Build graph with MemorySaver checkpointer]
    graph = build_graph(memory_checkpointer)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert graph is a valid compiled async object]
    assert graph is not None, "build_graph() returned None"
    assert hasattr(graph, "invoke"), "Compiled graph must have an 'invoke' method"
    assert hasattr(graph, "ainvoke"), "Compiled graph must have an 'ainvoke' method for async use"
    assert hasattr(graph, "aget_state"), "Compiled graph must have 'aget_state' for async state reads"

    # Anti-Illusion: verify IMP:9 compilation success log was emitted
    imp9_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "build_graph" in log]
    assert len(imp9_logs) > 0, (
        "Critical LDD Error: build_graph must emit IMP:9 compilation success log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_graph_compiles


# START_FUNCTION_test_all_node_ids_present
# START_CONTRACT:
# PURPOSE: Verify all 7 node IDs from scenario_1_flow.xml are registered in the compiled graph.
# INPUTS:
# - memory_checkpointer fixture => memory_checkpointer: MemorySaver
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): GraphTopology; PATTERN(8): TopologyValidation; PATTERN(8): DependencyInjection]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_all_node_ids_present(memory_checkpointer, caplog, ldd_capture):
    """
    Verifies that the compiled graph contains all 7 node IDs matching the
    scenario_1_flow.xml Graph_Topology exactly:
    - 1_Context_Analyzer
    - 2_Tool_Node
    - 3_Weight_Questioner
    - 3.5_Weight_Parser
    - 4_Draft_Generator
    - 5_CoVe_Critique
    - 6_Final_Synthesizer

    Accesses the graph's internal node registry to confirm presence.
    Uses memory_checkpointer fixture (MemorySaver) per v2.0.0 DI pattern.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Build graph and extract node IDs]
    graph = build_graph(memory_checkpointer)

    # LangGraph compiled graphs expose nodes via .nodes property or internal graph attribute
    # CompiledStateGraph has a .graph attribute (the underlying Graph object)
    # which has a .nodes dict
    try:
        node_ids = set(graph.graph.nodes.keys())
    except AttributeError:
        # Fallback: try accessing nodes directly
        node_ids = set(graph.nodes.keys()) if hasattr(graph, "nodes") else set()
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert all 7 required node IDs are present]
    required_node_ids = {
        "1_Context_Analyzer",
        "2_Tool_Node",
        "3_Weight_Questioner",
        "3.5_Weight_Parser",
        "4_Draft_Generator",
        "5_CoVe_Critique",
        "6_Final_Synthesizer",
    }

    missing_nodes = required_node_ids - node_ids
    assert len(missing_nodes) == 0, (
        f"Missing node IDs in compiled graph: {missing_nodes}. "
        f"Found: {node_ids}"
    )

    # Anti-Illusion: verify we found at least the 7 required nodes
    assert len(node_ids.intersection(required_node_ids)) == 7, (
        f"Expected 7 matching node IDs but found {len(node_ids.intersection(required_node_ids))}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_all_node_ids_present


# START_FUNCTION_test_checkpointer_is_base_checkpoint_saver
# START_CONTRACT:
# PURPOSE: Verify that the compiled graph's checkpointer is a BaseCheckpointSaver instance
#          (abstract superclass of both MemorySaver and AsyncSqliteSaver — AC10).
# INPUTS:
# - memory_checkpointer fixture => memory_checkpointer: MemorySaver
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): BaseCheckpointSaver; PATTERN(8): InstanceAssertion;
#            PATTERN(8): DependencyInjection]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_checkpointer_is_base_checkpoint_saver(memory_checkpointer, caplog, ldd_capture):
    """
    Verifies that the checkpointer attached to the compiled graph is a BaseCheckpointSaver
    instance. This check uses the abstract superclass (v2.0.0 change) so the same assertion
    covers both MemorySaver (tests) and AsyncSqliteSaver (production), satisfying AC10
    (hybrid checkpointer requirement).

    v1.0.0 checked for SqliteSaver specifically; v2.0.0 checks BaseCheckpointSaver
    because build_graph now accepts checkpointer via DI.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Build graph and check checkpointer type]
    graph = build_graph(memory_checkpointer)
    checkpointer = graph.checkpointer
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert BaseCheckpointSaver instance]
    assert isinstance(checkpointer, BaseCheckpointSaver), (
        f"Expected BaseCheckpointSaver checkpointer but got: {type(checkpointer).__name__}. "
        f"build_graph() must attach the DI-provided checkpointer to the compiled graph."
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_checkpointer_is_base_checkpoint_saver


# START_FUNCTION_test_interrupt_after_configured
# START_CONTRACT:
# PURPOSE: Verify that the graph is configured with interrupt_after=["3_Weight_Questioner"].
# INPUTS:
# - memory_checkpointer fixture => memory_checkpointer: MemorySaver
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): HumanInTheLoop; PATTERN(7): ConfigurationValidation;
#            PATTERN(8): DependencyInjection]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_interrupt_after_configured(memory_checkpointer, caplog, ldd_capture):
    """
    Verifies that the compiled graph has interrupt_after configured to include
    "3_Weight_Questioner" as required by the plan (hard invariant) and scenario_1_flow.xml.
    The interrupt is what enables the human-in-the-loop gate for weight calibration.

    Uses memory_checkpointer fixture (MemorySaver) per v2.0.0 DI pattern.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Build graph and inspect interrupt configuration]
    graph = build_graph(memory_checkpointer)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert interrupt_after configuration]
    # CompiledStateGraph exposes interrupt_after via config or stream_channels
    # Access via the compiled graph's internal interrupt_after_nodes or config
    interrupt_after = None

    # Try multiple access paths for different LangGraph versions
    if hasattr(graph, "interrupt_after_nodes"):
        interrupt_after = graph.interrupt_after_nodes
    elif hasattr(graph, "_interrupt_after"):
        interrupt_after = graph._interrupt_after
    elif hasattr(graph, "config_schema"):
        pass  # Config schema access path — not needed for current LangGraph version

    # Alternative: verify via graph.graph (the underlying Graph)
    if interrupt_after is None and hasattr(graph, "graph"):
        try:
            interrupt_after = getattr(graph.graph, "interrupt_after_nodes", None)
        except AttributeError:
            pass

    # If we cannot access interrupt_after directly, verify via a structural test:
    # The compiled graph should have "3_Weight_Questioner" in its interrupt configuration.
    # We verify this indirectly by checking the graph was compiled with IMP:9 log confirmation.
    imp9_logs = [
        log for log in high_imp_logs
        if "[IMP:9]" in log and "interrupt_after" in log
    ]

    if interrupt_after is not None:
        assert "3_Weight_Questioner" in interrupt_after, (
            f"interrupt_after must include '3_Weight_Questioner'. Got: {interrupt_after}"
        )
    else:
        # Verify via LDD log that interrupt_after was set correctly during compile
        assert len(imp9_logs) > 0, (
            "Could not verify interrupt_after directly; expected IMP:9 build_graph log "
            "containing 'interrupt_after' to confirm the configuration was applied"
        )
        assert any("3_Weight_Questioner" in log for log in imp9_logs), (
            "IMP:9 build_graph log must reference '3_Weight_Questioner' in interrupt_after"
        )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_interrupt_after_configured
