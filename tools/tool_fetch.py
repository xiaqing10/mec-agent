import json

from langchain_core.tools import tool


@tool
def feishu_fetch_report() -> str:
    """从飞书获取最新的MEC全局监控报告原文。
    用于查看最新的原始日志数据，不进行任何分析。
    """
    import mec_analyze
    report_text, error = mec_analyze.fetch_latest_mec_message()
    if error:
        return json.dumps({"error": f"获取报告失败: {error}"}, ensure_ascii=False)
    if not report_text:
        return json.dumps({"error": "未获取到报告"}, ensure_ascii=False)
    return report_text[:]