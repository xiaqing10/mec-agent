import json

from langchain_core.tools import tool


@tool
def mec_diagnose_project(project: str) -> str:
    """诊断指定项目下所有异常设备。

    优先从MySQL数据库获取项目设备状态和异常列表，如果数据库没有该项目数据，
    则回退到飞书监控报告解析。然后逐台SSH诊断，汇总结果。

    Args:
        project: 项目名，如 德会、德会隧道、柯诸、汕梅、汉宜、沈海、绵九、贵阳、青海、南京仙新路、山西灵石 等
    """
    from diagnose_project import diagnose_project as run_diagnose

    if not project:
        return json.dumps({"error": "未指定项目名"}, ensure_ascii=False)

    result = run_diagnose(project)
    return json.dumps(result, ensure_ascii=False)


@tool
def feishu_analyze_logs(project: str = "") -> str:
    """分析MEC监控日志。从飞书获取最新监控报告，解析为结构化数据，
    进行P0-P3分级告警（P0=完全离线最严重），
    并与历史对比（持续/新增/恢复/恶化/好转）。

    Args:
        project: 可选，指定要分析的项目名。不指定则分析全局报告。
    """
    import mec_analyze
    from code_analyze import parse_mec_report, compare_with_history, generate_report, load_structured_history, save_report_to_history

    report_text, error = mec_analyze.fetch_latest_mec_message()
    if error or not report_text:
        return json.dumps({"error": f"获取报告失败: {error}"}, ensure_ascii=False)

    parsed = parse_mec_report(report_text)
    if not parsed:
        return json.dumps({"error": "解析报告失败"}, ensure_ascii=False)

    history = load_structured_history()
    comparison = compare_with_history(parsed, history)
    report_text_output, _ = generate_report(parsed, comparison, history)

    phys_off_summary = {}
    for pname, pdata in parsed.get("projects", {}).items():
        phys_off = pdata.get("physical_offline_devices", [])
        if phys_off:
            phys_off_summary[pname] = {
                "count": len(phys_off),
                "devices": [{"name": d.get("name", ""), "ip": d.get("ip", "")} for d in phys_off]
            }

    result = {"timestamp": parsed.get("timestamp", ""), "comparison": comparison, "report": report_text_output}

    if project:
        project_data = parsed.get("projects", {}).get(project)
        if project_data:
            result["project_analysis"] = project_data
        else:
            result["note"] = f"未在报告中找到项目 '{project}' 的数据"

    save_report_to_history(parsed)
    return json.dumps(result, ensure_ascii=False)


@tool
def feishu_llm_analyze_logs(project: str = "") -> str:
    """使用LLM深度分析MEC监控日志。比普通分析更深入，
    会给出整体概况、突出问题、趋势变化和关键建议。

    Args:
        project: 可选，指定要分析的项目名。不指定则分析全局。
    """
    import mec_analyze
    from code_analyze import parse_mec_report
    import urllib.request
    import urllib.error
    from agent import get_current_model_config
    cfg = get_current_model_config()
    LLM_API_KEY = cfg["api_key"]
    LLM_BASE_URL = cfg["base_url"]
    LLM_MODEL = cfg["model"]

    report_text, error = mec_analyze.fetch_latest_mec_message()
    if error or not report_text:
        return json.dumps({"error": f"获取报告失败: {error}"}, ensure_ascii=False)

    prompt = """你是一位资深MEC边缘计算运维专家。以下是从飞书获取的MEC设备监控报告，请进行智能分析。

报告内容:
{report}

请分析：
1. 整体概况：当前各项目健康状况
2. 突出问题：最严重的项目及其问题
3. 趋势变化：与历史相比的恶化/好转情况
4. 关键建议：需要优先处理的事项

请用中文回答，简洁专业。"""

    if project:
        prompt = prompt.format(report=report_text)
        prompt += f"\n\n请重点关注项目【{project}】的情况，给出针对该项目的详细分析。"
    else:
        prompt = prompt.format(report=report_text)

    url = f"{LLM_BASE_URL}/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "你是一位资深MEC边缘计算运维专家，精通边缘计算设备监控和故障排查。请基于监控报告数据给出专业的分析。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 16384
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read().decode())
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            return content
        return "LLM分析返回为空"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')[:500]
        return f"LLM API HTTP {e.code}: {body}"
    except Exception as e:
        return f"LLM API请求异常: {e}"