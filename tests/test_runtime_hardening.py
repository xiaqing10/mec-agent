from agent import AgentState
from tools import TOOLS
from prompt_config import load_agent_system_prompt


def test_request_context_fields_are_per_turn_optional():
    annotations = AgentState.__annotations__
    assert "request_project" in annotations
    assert "request_ip" in annotations
    assert "request_model" in annotations


def test_memory_is_registered_as_agent_tool():
    names = {getattr(tool, "name", "") for tool in TOOLS}
    assert "memory" in names


def test_prompt_is_externalized():
    assert load_agent_system_prompt().strip()
