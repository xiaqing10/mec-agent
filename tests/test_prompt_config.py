from prompt_config import load_agent_system_prompt


def test_agent_prompt_is_externalized():
    prompt = load_agent_system_prompt()
    assert "你是智慧交通/MEC运维智能体" in prompt
    assert "mec_diagnose_device" in prompt
    assert "deep_analysis_recommended=true" in prompt
