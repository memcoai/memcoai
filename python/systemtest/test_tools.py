"""ListTools against the live service, run with a creator credential.

A tool silently reported unavailable here would mean the access-set filtering
wired into session.tools() is misreading the service's own response -- the
class of bug the fake server in tests/ cannot catch, since it never talks to
the real authorization stack.
"""

from __future__ import annotations

from memcoai import Memco

# Written out rather than imported from memcoai.agent: agent._OPERATIONS is
# private, and this system test verifies the SDK's public behaviour against an
# independent expectation rather than against the implementation's own idea of
# what it offers.
EXPECTED_TOOLS = {
    "memco_search",
    "memco_get_memory",
    "memco_create_memory",
    "memco_enrich_memory",
    "memco_share_feedback",
    "memco_revert_memory",
}


def test_a_creator_credential_is_offered_every_tool(client: Memco, domain: str) -> None:
    """A creator credential sees every tool as available.

    Checked in the raw catalog and in a session's own filtered toolset.
    """
    catalog = client.memory.list_tools()
    assert catalog, "the service returned no tools"
    unavailable = [tool.name for tool in catalog if not tool.available]
    assert not unavailable, f"reported unavailable to this creator credential: {unavailable}"

    session = client.memory.start_session(domain)
    offered = {tool.name for tool in session.tools()}
    assert offered == EXPECTED_TOOLS, f"expected every offered tool, got: {sorted(offered)}"
