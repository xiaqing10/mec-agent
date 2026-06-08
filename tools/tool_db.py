import json
import re

from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB
from langchain_core.tools import tool


@tool
def query_mec_abnormal() -> str:
    """查询当前所有异常设备的统计信息。
    从MySQL数据库获取，包括各项目异常设备数量、物理机离线、容器离线、图片为0、图片偏少等概览统计。
    """
    import pymysql
    from datetime import date

    try:
        conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                               database=MYSQL_DB, charset="utf8mb4", connect_timeout=3,
                               cursorclass=pymysql.cursors.DictCursor)
    except Exception as e:
        return json.dumps({"error": f"数据库连接失败: {e}"}, ensure_ascii=False)

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT md.project,
                       COUNT(*) AS total,
                       SUM(CASE WHEN md.ssh_status = 0 THEN 1 ELSE 0 END) AS ssh_offline,
                       SUM(CASE WHEN pm.is_healthy = 0 THEN 1 ELSE 0 END) AS pm_unhealthy,
                       SUM(CASE WHEN pm.running_containers = 0 OR pm.running_containers IS NULL THEN 1 ELSE 0 END) AS no_container,
                       SUM(CASE WHEN md.event_jpg_count = 0 THEN 1 ELSE 0 END) AS zero_img,
                       SUM(CASE WHEN md.event_jpg_count > 0 AND md.event_jpg_count < 100 THEN 1 ELSE 0 END) AS low_img
                FROM mec_device md
                LEFT JOIN physical_machine pm ON md.physical_machine_id = pm.id
                GROUP BY md.project
                ORDER BY SUM(CASE WHEN md.ssh_status = 0 OR pm.is_healthy = 0 OR md.event_jpg_count = 0 THEN 1 ELSE 0 END) DESC
            """)
            rows = cursor.fetchall()
    except Exception as e:
        conn.close()
        return json.dumps({"error": f"数据库查询失败: {e}"}, ensure_ascii=False)

    conn.close()

    col_keys = ["ssh_offline", "pm_unhealthy", "no_container", "zero_img", "low_img"]
    col_headers = {
        "ssh_offline": "物理机SSH离线",
        "pm_unhealthy": "物理机异常",
        "no_container": "无容器",
        "zero_img": "图片为0",
        "low_img": "图片偏少(<100)",
    }
    col_separators = {
        "ssh_offline": "--------------",
        "pm_unhealthy": "-----------",
        "no_container": "--------",
        "zero_img": "---------",
        "low_img": "---------------",
    }

    active_cols = []
    for k in col_keys:
        if any(r[k] != 0 for r in rows):
            active_cols.append(k)

    lines = [f"📊 **异常项目概览**\n"]
    lines.append(f"数据来源: MySQL数据库（非实时，更新至 {date.today()}）")
    lines.append(f"> 本数据仅来自数据库，不涉及飞书报告\n")
    lines.append("| 项目 | 总设备 | 异常数 | 物理机SSH离线 | 物理机异常 | 无容器 | 图片为0 | 图片偏少(<100) |")
    lines.append("|------|--------|--------|--------------|-----------|--------|---------|---------------|")

    total_abnormal = 0
    for r in rows:
        abnormal = r["ssh_offline"] + r["pm_unhealthy"] + r["zero_img"]
        total_abnormal += abnormal
        def fmt(v):
            return str(v) if v is not None else "-"
        lines.append(
            f"| {r['project']} | {fmt(r['total'])} | {fmt(abnormal)} | {fmt(r['ssh_offline'])} | "
            f"{fmt(r['pm_unhealthy'])} | {fmt(r['no_container'])} | {fmt(r['zero_img'])} | {fmt(r['low_img'])} |"
        )

    lines.append(f"\n🔄 共 **{total_abnormal}** 台异常设备")
    return "\n".join(lines)


@tool
def query_mec_device_from_db(ip: str) -> str:
    """从MySQL数据库查询MEC设备的完整状态信息，无需SSH连接。

    当设备SSH不可达时，数据库记录是了解设备状态的重要途径。
    返回设备的基本信息、SSH在线状态、系统指标（CPU/内存/硬盘）、
    物理机信息、supervisor进程状态、离线时长、历史图片数据等。

    Args:
        ip: 设备IP地址或设备名（如 mec_1002、zk26_690）
    """
    from query_sensor_status import get_device_db_info, format_device_db_info
    from diagnose_mec import _resolve_device

    if not ip:
        return json.dumps({"error": "未指定设备IP或设备名"}, ensure_ascii=False)

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, _ = _resolve_device(ip)
        if resolved_ip != ip:
            ip = resolved_ip
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        msg = f"数据库中未找到设备 '{ip}'，请检查设备名是否正确，或直接使用IP地址"
        return json.dumps({"error": msg}, ensure_ascii=False)

    db_info = get_device_db_info(ip)
    if not db_info or not db_info.get("name"):
        return json.dumps({"error": f"数据库中没有设备 {ip} 的记录"}, ensure_ascii=False)

    return format_device_db_info(db_info)


@tool
def query_mec_project_from_db(project: str) -> str:
    """从MySQL数据库查询指定项目的设备整体状态，无需解析飞书报告。

    返回项目下所有设备的汇总统计：总设备数、在线/离线设备数、
    物理机健康率、容器健康率、传感器（摄像头/雷达）在线率、
    今日图片统计、以及所有异常设备列表。

    Args:
        project: 项目名，从数据库 mec_device 表的 project 字段获取，如德会、德会隧道、柯诸、汕梅、汉宜、沈海、绵九、贵阳、青海、南京仙新路、山西灵石 等
    """
    from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB
    import pymysql

    if not project:
        return json.dumps({"error": "未指定项目名"}, ensure_ascii=False)

    try:
        conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                               database=MYSQL_DB, charset="utf8mb4", connect_timeout=3,
                               cursorclass=pymysql.cursors.DictCursor)
    except Exception as e:
        return json.dumps({"error": f"数据库连接失败: {e}"}, ensure_ascii=False)

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT md.id, md.name, md.host, md.is_active, md.ssh_status,
                       md.cpu_usage, md.mem_usage, md.disk_usage,
                       md.last_connected, md.last_ssh_check,
                       md.event_jpg_count, md.last_event_check,
                       pm.is_healthy AS pm_healthy,
                       pm.running_containers
                FROM mec_device md
                LEFT JOIN physical_machine pm ON md.physical_machine_id = pm.id
                WHERE md.project = %s
                ORDER BY md.name
            """, (project,))
            devices = cursor.fetchall()

            if not devices:
                return json.dumps({"error": f"数据库中没有项目 '{project}' 的设备记录"}, ensure_ascii=False)

            cursor.execute("""
                SELECT
                    COUNT(DISTINCT c.id) AS total_cameras,
                    SUM(CASE WHEN c.status = 1 THEN 1 ELSE 0 END) AS online_cameras,
                    COUNT(DISTINCT r.id) AS total_radars,
                    SUM(CASE WHEN r.status = 1 THEN 1 ELSE 0 END) AS online_radars
                FROM mec_device md
                JOIN pole p ON p.mec_device_id = md.id
                LEFT JOIN camera c ON c.pole_id = p.id
                LEFT JOIN radar r ON r.pole_id = p.id
                WHERE md.project = %s
            """, (project,))
            sensor_row = cursor.fetchone()

            from datetime import date
            today = date.today()
            cursor.execute("""
                SELECT COUNT(*) AS device_count, SUM(eh.jpg_count) AS total_jpg
                FROM event_image_history eh
                JOIN mec_device md ON eh.device_id = md.id
                WHERE md.project = %s AND eh.count_date = %s
            """, (project, today))
            img_row = cursor.fetchone()

    except Exception as e:
        conn.close()
        return json.dumps({"error": f"数据库查询失败: {e}"}, ensure_ascii=False)

    conn.close()

    total = len(devices)
    active = sum(1 for d in devices if d.get("is_active"))
    pm_healthy = sum(1 for d in devices if d.get("pm_healthy"))
    container_ssh_online = sum(1 for d in devices if d.get("ssh_status"))
    zero_img = sum(1 for d in devices if d.get("event_jpg_count") is not None and d["event_jpg_count"] == 0)
    low_img = sum(1 for d in devices if d.get("event_jpg_count") is not None and d["event_jpg_count"] < 100)

    abnormal_devices = []
    for d in devices:
        pm_online = bool(d.get("pm_healthy"))
        container_online = bool(d.get("ssh_status"))
        jpg = d.get("event_jpg_count")

        pm_status = "物理机在线" if pm_online else "物理机离线"
        container_status = "容器在线" if container_online else "容器离线"

        parts = [pm_status, container_status]
        if not container_online:
            parts.append("数据不可靠(容器离线)")
        elif jpg is None:
            parts.append("图片无数据")
        elif jpg == 0:
            parts.append("今日图片为0")
        elif jpg < 100:
            parts.append(f"今日图片偏低({jpg}张)")
        else:
            parts.append("正常")

        if not pm_online or not container_online or jpg is None or jpg == 0:
            abnormal_devices.append({
                "name": d["name"], "ip": d["host"],
                "issues": parts,
                "cpu": d.get("cpu_usage"), "mem": d.get("mem_usage"), "disk": d.get("disk_usage"),
            })

    total_cam = sensor_row["total_cameras"] or 0
    online_cam = sensor_row["online_cameras"] or 0
    total_rad = sensor_row["total_radars"] or 0
    online_rad = sensor_row["online_radars"] or 0

    lines = [f"📊 **项目 {project} 数据库状态概览**\n"]
    lines.append(f"数据更新时间: {devices[0].get('last_ssh_check', '未知') or '未知'}")
    lines.append(f"> ⚠️ 以下数据来自MySQL数据库，非实时SSH数据\n")

    lines.append("### 设备统计")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总设备数 | {total} |")
    lines.append(f"| 激活设备 | {active}/{total} |")
    lines.append(f"| 物理机健康 | {pm_healthy}/{total} |")
    lines.append(f"| 容器SSH在线 | {container_ssh_online}/{total} |")
    lines.append(f"| 今日图片为0 | {zero_img} |")
    lines.append(f"| 今日图片偏低(<100) | {low_img} |")

    lines.append("\n### 传感器统计")
    cam_rate = f"{online_cam}/{total_cam}" if total_cam > 0 else "无数据"
    rad_rate = f"{online_rad}/{total_rad}" if total_rad > 0 else "无数据"
    lines.append(f"| 类型 | 在线率 |")
    lines.append(f"|------|--------|")
    lines.append(f"| 摄像头 | {cam_rate} |")
    lines.append(f"| 雷达 | {rad_rate} |")

    if img_row and img_row["total_jpg"] is not None:
        lines.append(f"\n今日项目总图片: {img_row['total_jpg']} 张（{img_row['device_count']}台设备有数据）")

    if abnormal_devices:
        lines.append(f"\n### 异常设备列表（{len(abnormal_devices)}台）\n")
        lines.append("| 设备名 | IP | 物理机 | 容器 | 图片 | CPU | 内存 | 硬盘 |")
        lines.append("|--------|-----|--------|------|------|-----|------|------|")
        for ad in abnormal_devices[:30]:
            cpu = f"{ad['cpu']:.0f}%" if ad['cpu'] is not None else "-"
            mem = f"{ad['mem']:.0f}%" if ad['mem'] is not None else "-"
            disk = f"{ad['disk']:.0f}%" if ad['disk'] is not None else "-"
            issues = ad['issues']
            pm = "❌ 离线" if issues[0] == "物理机离线" else "✅ 在线"
            container = "❌ 离线" if issues[1] == "容器离线" else "✅ 在线"
            img = issues[2] if len(issues) > 2 else "正常"
            lines.append(f"| {ad['name']} | {ad['ip']} | {pm} | {container} | {img} | {cpu} | {mem} | {disk} |")
        if len(abnormal_devices) > 30:
            lines.append(f"\n...还有 {len(abnormal_devices) - 30} 台异常设备未列出")
    else:
        lines.append("\n✅ 项目下所有设备正常运行，无异常")

    return "\n".join(lines)