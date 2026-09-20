from ._shared import set_diag_progress_callback, reset_diag_progress_callback, get_diag_progress_callback, _notify_progress, _summarize_log_errors, _build_diag_result
from ._diag_cache import cache_diag_data, get_diag_cache, clear_diag_cache
from .tool_context import resolve_mec_device, resolve_mec_project
from .tool_device import mec_diagnose_device, mec_device_info, mec_llm_diagnose_device
from .tool_project import mec_diagnose_project, feishu_analyze_logs, feishu_llm_analyze_logs
from .tool_db import query_mec_abnormal, query_mec_device_from_db, query_mec_project_from_db
from .tool_ssh import mec_ssh_exec
from .tool_dingtalk import push_to_dingtalk
from .tool_fetch import feishu_fetch_report
from .tool_help import help_info
from .tool_repair import mec_repair_device
from .tool_image import query_mec_event_records, query_mec_event_image, query_mec_project_event_stats
from .tool_memory import memory
from .tool_mongodb import (
    query_server_traffic_flow,
    query_server_events,
    query_server_event_stats,
    query_server_device_metrics,
    query_server_traffic_pattern,
    query_server_analysis_report,
)
from .tool_evolve import generate_improvement_report

TOOLS = [
    resolve_mec_device,
    resolve_mec_project,
    mec_diagnose_device,
    mec_diagnose_project,
    mec_device_info,
    feishu_analyze_logs,
    feishu_llm_analyze_logs,
    mec_llm_diagnose_device,
    feishu_fetch_report,
    query_mec_abnormal,
    push_to_dingtalk,
    mec_ssh_exec,
    help_info,
    memory,
    query_mec_device_from_db,
    query_mec_project_from_db,
    mec_repair_device,
    query_mec_event_records,
    query_mec_event_image,
    query_mec_project_event_stats,
    query_server_traffic_flow,
    query_server_events,
    query_server_event_stats,
    query_server_device_metrics,
    query_server_traffic_pattern,
    query_server_analysis_report,
    generate_improvement_report,
]


# Tools that are explicitly safe to expose to the conversational LLM.
# Deterministic workflows, raw SSH, and LLM-invoked deep-analysis tools are
# intentionally excluded. They may still be imported and invoked by trusted
# workflow code.
AGENT_TOOL_NAMES = {
    "resolve_mec_device", "resolve_mec_project", "mec_device_info",
    "query_mec_abnormal", "query_mec_device_from_db", "query_mec_project_from_db",
    "query_mec_event_records", "query_mec_event_image", "query_mec_project_event_stats",
    "query_server_traffic_flow", "query_server_events", "query_server_event_stats",
    "query_server_device_metrics", "query_server_traffic_pattern",
    "query_server_analysis_report", "feishu_analyze_logs", "feishu_fetch_report",
    "help_info", "memory", "mec_repair_device", "push_to_dingtalk",
    "generate_improvement_report",
}
AGENT_TOOLS = [t for t in TOOLS if getattr(t, "name", "") in AGENT_TOOL_NAMES]

__all__ = [
    "TOOLS", "AGENT_TOOLS",
    "set_diag_progress_callback", "reset_diag_progress_callback", "get_diag_progress_callback",
    "resolve_mec_device", "resolve_mec_project",
    "mec_diagnose_device", "mec_device_info", "mec_llm_diagnose_device",
    "mec_diagnose_project", "feishu_analyze_logs", "feishu_llm_analyze_logs",
    "query_mec_abnormal", "query_mec_device_from_db", "query_mec_project_from_db",
    "mec_ssh_exec", "push_to_dingtalk", "feishu_fetch_report", "help_info",
    "memory",
    "mec_repair_device",
    "query_mec_event_records", "query_mec_event_image", "query_mec_project_event_stats",
    "query_server_traffic_flow", "query_server_events", "query_server_event_stats",
    "query_server_device_metrics", "query_server_traffic_pattern", "query_server_analysis_report",
    "generate_improvement_report",
]