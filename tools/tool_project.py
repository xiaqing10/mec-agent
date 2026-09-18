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
    """使用统一 LLM Gateway 对最新飞书 MEC 监控报告进行深度分析。"""
    import mec_analyze
    from llm_gateway import invoke_text

    report_text, error = mec_analyze.fetch_latest_mec_message()
    if error or not report_text:
        return json.dumps({"error": f"获取报告失败: {error}"}, ensure_ascii=False)

    project_scope = f"重点项目：{project}" if project else "范围：全局"
    prompt = f"""你是一位资深MEC边缘计算运维专家。
请基于以下飞书监控报告做深度分析，只使用报告中有证据支持的信息，不要猜测。

{project_scope}

分析：
1. 当前整体健康状况；
2. 关键异常及证据；
3. 与历史对比时只说明报告实际提供的趋势；
4. 建议按影响和紧急程度组织；
5. 说明仍缺失的关键证据。

监控报告：
{report_text}
"""
    try:
        content = invoke_text(
            "你是一位资深MEC边缘计算运维专家，基于监控数据分析，不编造设备状态。",
            prompt,
            timeout=45,
            max_tokens=4096,
            retry=1,
        )
        return json.dumps({
            "schema_version": "1.0",
            "type": "feishu_llm_analysis_result",
            "status": "normal" if content else "warning",
            "stage": "deep",
            "project": project,
            "next_action": "report",
            "analysis": content,
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("飞书LLM分析失败: %s", exc)
        return json.dumps({
            "schema_version": "1.0",
            "type": "feishu_llm_analysis_result",
            "status": "warning",
            "stage": "deep",
            "project": project,
            "next_action": "report",
            "analysis": "",
            "error": str(exc)[:500],
        }, ensure_ascii=False)

