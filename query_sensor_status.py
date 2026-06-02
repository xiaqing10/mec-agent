#!/usr/bin/env python3
"""
传感器状态查询模块 - 从MySQL查询设备关联的摄像头和雷达在线状态

关联关系:
  mec_device.host (设备IP) → pole.mec_device_id → camera.pole_id / radar.pole_id

用法:
  from query_sensor_status import get_sensor_status, lookup_device
  sensors = get_sensor_status("10.145.4.1")
  devices = lookup_device("mak1_220")
"""

import pymysql
import pymysql.cursors
import re

from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB


def _get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        charset="utf8mb4",
        connect_timeout=3,
        cursorclass=pymysql.cursors.DictCursor,
    )


def lookup_device(query: str, project: str = None) -> list:
    """查找设备，支持IP或设备名称，可按项目过滤。

    搜索策略（按顺序）：
      1. 精确匹配设备名/IP
      2. LIKE模糊匹配（%query%）
      3. 按项目过滤+后缀搜索（如 "柯诸" + "690" → myk23_690）
      4. 仅按项目过滤列出所有设备

    Args:
        query: 设备IP或设备名称
        project: 可选项目名，用于缩小搜索范围

    Returns:
        [{"name": str, "ip": str, "project": str, "pole": str, "host": str}, ...]
        若找不到返回空列表
    """
    query = query.strip()
    is_ip = bool(re.match(r'^\d+\.\d+\.\d+\.\d+$', query))
    results = []

    try:
        conn = _get_conn()
    except Exception as e:
        return []

    try:
        with conn.cursor() as cursor:
            if is_ip:
                cursor.execute(
                    """
                    SELECT DISTINCT md.name, md.host, md.project, p.name AS pole
                    FROM mec_device md
                    LEFT JOIN pole p ON p.mec_device_id = md.id
                    WHERE md.host = %s
                    """,
                    (query,),
                )
            else:
                # 1. 精确匹配
                project_filter = f"AND md.project = %s" if project else ""
                params = [query, query]
                if project:
                    params.append(project)
                cursor.execute(
                    f"""
                    SELECT DISTINCT md.name, md.host, md.project, p.name AS pole
                    FROM mec_device md
                    LEFT JOIN pole p ON p.mec_device_id = md.id
                    WHERE md.name = %s {project_filter}
                    """,
                    params[:1 + (1 if project else 0)],
                )
                if cursor.rowcount == 0:
                    # 2. LIKE模糊匹配
                    like_params = [f"%{query}%"]
                    if project:
                        like_params.append(project)
                    cursor.execute(
                        f"""
                        SELECT DISTINCT md.name, md.host, md.project, p.name AS pole
                        FROM mec_device md
                        LEFT JOIN pole p ON p.mec_device_id = md.id
                        WHERE md.name LIKE %s {project_filter}
                        ORDER BY md.name
                        LIMIT 20
                        """,
                        like_params,
                    )
            rows = cursor.fetchall()

            # 3. 如果还没找到，且有project，尝试项目+后缀数字搜索
            #    例如 query="zk26_690" + project="柯诸" → 搜索 project=柯诸 AND name LIKE '%\_690'
            if not rows and project and not is_ip:
                # 提取query中的数字后缀（如 zk26_690 → 690, 690 → 690）
                suffix_match = re.search(r'(\d+)$', query)
                if suffix_match:
                    suffix = suffix_match.group(1)
                    cursor.execute(
                        """
                        SELECT DISTINCT md.name, md.host, md.project, p.name AS pole
                        FROM mec_device md
                        LEFT JOIN pole p ON p.mec_device_id = md.id
                        WHERE md.project = %s AND md.name LIKE %s
                        ORDER BY md.name
                        LIMIT 20
                        """,
                        (project, f"%\\_{suffix}"),
                    )
                    rows = cursor.fetchall()

            seen = set()
            for r in rows:
                key = (r["host"], r["project"])
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "name": r["name"],
                        "ip": r["host"],
                        "project": r["project"],
                        "pole": r["pole"] or "",
                        "host": r["host"],
                    })
    except Exception:
        pass
    finally:
        conn.close()

    return results


def get_sensor_status(device_ip: str, project: str = None) -> dict:
    result = {
        "cameras": [], "radars": [],
        "total_cameras": 0, "total_radars": 0,
        "offline_cameras": 0, "offline_radars": 0, "offline_total": 0,
        "project": project or "",
    }

    if not device_ip:
        return result

    try:
        conn = _get_conn()
    except Exception as e:
        result["_error"] = str(e)
        return result

    try:
        with conn.cursor() as cursor:
            params = [device_ip]
            project_filter = ""
            if project:
                project_filter = "AND md.project = %s"
                params.append(project)

            cursor.execute(
                f"""
                SELECT c.name, c.ip, c.status, c.last_check, md.project, p.name AS pole
                FROM mec_device md
                JOIN pole p ON p.mec_device_id = md.id
                JOIN camera c ON c.pole_id = p.id
                WHERE md.host = %s {project_filter}
                GROUP BY c.id
                """,
                params,
            )
            cameras = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT r.name, r.ip, r.status, r.last_check, md.project, p.name AS pole
                FROM mec_device md
                JOIN pole p ON p.mec_device_id = md.id
                JOIN radar r ON r.pole_id = p.id
                WHERE md.host = %s {project_filter}
                GROUP BY r.id
                """,
                params,
            )
            radars = cursor.fetchall()

        for cam in cameras:
            s = cam["status"] == 1 if cam["status"] is not None else False
            result["cameras"].append({
                "name": cam["name"], "ip": cam["ip"], "status": s,
                "last_check": str(cam["last_check"]) if cam["last_check"] else "",
                "project": cam["project"] or "", "pole": cam["pole"] or "",
            })
            if not s:
                result["offline_cameras"] += 1

        for rad in radars:
            s = rad["status"] == 1 if rad["status"] is not None else False
            result["radars"].append({
                "name": rad["name"], "ip": rad["ip"], "status": s,
                "last_check": str(rad["last_check"]) if rad["last_check"] else "",
                "project": rad["project"] or "", "pole": rad["pole"] or "",
            })
            if not s:
                result["offline_radars"] += 1

        result["total_cameras"] = len(cameras)
        result["total_radars"] = len(radars)
        result["offline_total"] = result["offline_cameras"] + result["offline_radars"]

    except Exception as e:
        result["_error"] = str(e)
    finally:
        conn.close()

    return result


def format_sensor_status(sensor_info: dict) -> str:
    if sensor_info.get("_error"):
        return ""
    if sensor_info["total_cameras"] == 0 and sensor_info["total_radars"] == 0:
        return ""

    lines = []
    project = sensor_info.get("project", "")
    prefix = f"[{project}] " if project else ""

    if sensor_info["cameras"]:
        online = sensor_info["total_cameras"] - sensor_info["offline_cameras"]
        lines.append(f"\U0001f4f7 {prefix}摄像头: {online}/{sensor_info['total_cameras']} 在线")
        for cam in sensor_info["cameras"]:
            icon = "\u2705" if cam["status"] else "\u274c"
            extra = f" [{cam.get('project','')}]" if cam.get('project') and cam['project'] != project else ""
            lines.append(f"  {icon} {cam['name']} ({cam['ip']}){extra}")

    if sensor_info["radars"]:
        online = sensor_info["total_radars"] - sensor_info["offline_radars"]
        lines.append(f"\U0001f6e1 {prefix}雷达: {online}/{sensor_info['total_radars']} 在线")
        for rad in sensor_info["radars"]:
            icon = "\u2705" if rad["status"] else "\u274c"
            extra = f" [{rad.get('project','')}]" if rad.get('project') and rad['project'] != project else ""
            lines.append(f"  {icon} {rad['name']} ({rad['ip']}){extra}")

    if sensor_info["offline_total"] > 0:
        lines.append(f"\u26a0\ufe0f 共 {sensor_info['offline_total']} 个传感器离线")

    return "\n".join(lines)


def format_sensor_status_short(sensor_info: dict) -> str:
    if sensor_info.get("_error"):
        return ""
    if sensor_info["total_cameras"] == 0 and sensor_info["total_radars"] == 0:
        return ""

    parts = []
    project = sensor_info.get("project", "")
    prefix = f"{project} " if project else ""
    if sensor_info["cameras"]:
        online = sensor_info["total_cameras"] - sensor_info["offline_cameras"]
        parts.append(f"{prefix}摄像头 {online}/{sensor_info['total_cameras']}")
    if sensor_info["radars"]:
        online = sensor_info["total_radars"] - sensor_info["offline_radars"]
        parts.append(f"{prefix}雷达 {online}/{sensor_info['total_radars']}")
    return " | ".join(parts)


def get_device_db_info(device_ip: str) -> dict:
    """从MySQL获取设备的完整数据库信息（当SSH不可达时的回退数据源）。

    返回设备的在线状态、离线时长、系统指标、物理机信息、进程状态、历史图片等。

    Args:
        device_ip: 设备IP地址

    Returns:
        {
            "name": str, "ip": str, "project": str, "is_active": bool,
            "ssh_status": bool, "last_ssh_check": str,
            "last_connected": str, "offline_duration": str,
            "cpu_usage": float, "mem_usage": float, "disk_usage": float,
            "event_jpg_count": int, "last_event_check": str,
            "physical_machine": {...},
            "supervisor_processes": [...],
            "image_history": [...],
        }
    """
    result = {
        "ip": device_ip, "name": "", "project": "",
        "is_active": None, "ssh_status": None, "last_ssh_check": "",
        "last_connected": "", "offline_duration": "",
        "cpu_usage": None, "mem_usage": None, "mem_total_gb": None,
        "disk_usage": None, "disk_total_gb": None,
        "event_jpg_count": None, "last_event_check": "",
        "physical_machine": {}, "supervisor_processes": [], "image_history": [],
    }

    if not device_ip:
        return result

    try:
        conn = _get_conn()
    except Exception:
        return result

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT md.id, md.name, md.host, md.project, md.is_active,
                       md.ssh_status, md.last_ssh_check, md.last_connected,
                       md.cpu_usage, md.mem_usage, md.mem_total_gb,
                       md.disk_usage, md.disk_total_gb,
                       md.event_jpg_count, md.last_event_check,
                       md.system_uptime, md.last_stats_update,
                       md.physical_machine_id,
                       pm.hostname, pm.host AS pm_host, pm.is_healthy,
                       pm.last_check AS pm_last_check, pm.first_failure_at,
                       pm.running_containers, pm.gpu_info, pm.gpu_count,
                       pm.disk_usage_percent AS pm_disk_usage,
                       pm.memory_total_gb AS pm_mem_total
                FROM mec_device md
                LEFT JOIN physical_machine pm ON md.physical_machine_id = pm.id
                WHERE md.host = %s
                """,
                (device_ip,),
            )
            row = cursor.fetchone()

            if not row:
                return result

            result["name"] = row["name"] or ""
            result["project"] = row["project"] or ""
            result["is_active"] = bool(row["is_active"]) if row["is_active"] is not None else None
            result["ssh_status"] = bool(row["ssh_status"]) if row["ssh_status"] is not None else None
            result["last_ssh_check"] = str(row["last_ssh_check"]) if row["last_ssh_check"] else ""
            result["last_connected"] = str(row["last_connected"]) if row["last_connected"] else ""
            result["cpu_usage"] = row["cpu_usage"]
            result["mem_usage"] = row["mem_usage"]
            result["mem_total_gb"] = row["mem_total_gb"]
            result["disk_usage"] = row["disk_usage"]
            result["disk_total_gb"] = row["disk_total_gb"]
            result["event_jpg_count"] = row["event_jpg_count"]
            result["last_event_check"] = str(row["last_event_check"]) if row["last_event_check"] else ""
            result["system_uptime"] = row["system_uptime"]
            result["last_stats_update"] = str(row["last_stats_update"]) if row["last_stats_update"] else ""

            if row["last_connected"]:
                try:
                    from datetime import datetime
                    last = row["last_connected"]
                    if isinstance(last, str):
                        last = datetime.fromisoformat(last)
                    now = datetime.now()
                    delta = now - last
                    days = delta.days
                    hours = delta.seconds // 3600
                    if days > 0:
                        result["offline_duration"] = f"{days}天{hours}小时"
                    else:
                        result["offline_duration"] = f"{hours}小时"
                except Exception:
                    pass

            if row["physical_machine_id"]:
                result["physical_machine"] = {
                    "hostname": row["hostname"] or "",
                    "host": row["pm_host"] or "",
                    "is_healthy": bool(row["is_healthy"]) if row["is_healthy"] is not None else None,
                    "last_check": str(row["pm_last_check"]) if row["pm_last_check"] else "",
                    "first_failure_at": str(row["first_failure_at"]) if row["first_failure_at"] else "",
                    "running_containers": row["running_containers"],
                    "gpu_info": row["gpu_info"] or "",
                    "gpu_count": row["gpu_count"],
                    "disk_usage": row["pm_disk_usage"],
                    "mem_total_gb": row["pm_mem_total"],
                }

            device_id = row["id"]

            cursor.execute(
                """
                SELECT program_name, pid, status, uptime_str, started_at, updated_at
                FROM supervisor_process
                WHERE device_id = %s
                ORDER BY program_name
                """,
                (device_id,),
            )
            procs = cursor.fetchall()
            for p in procs:
                result["supervisor_processes"].append({
                    "name": p["program_name"],
                    "pid": p["pid"],
                    "status": "running" if p["status"] == 1 else "stopped",
                    "uptime": p["uptime_str"] or "",
                    "started_at": str(p["started_at"]) if p["started_at"] else "",
                    "updated_at": str(p["updated_at"]) if p["updated_at"] else "",
                })

            cursor.execute(
                """
                SELECT count_date, jpg_count, updated_at
                FROM event_image_history
                WHERE device_id = %s
                ORDER BY count_date DESC
                LIMIT 14
                """,
                (device_id,),
            )
            imgs = cursor.fetchall()
            for img in imgs:
                result["image_history"].append({
                    "date": str(img["count_date"]),
                    "count": img["jpg_count"],
                    "updated_at": str(img["updated_at"]) if img["updated_at"] else "",
                })

    except Exception:
        pass
    finally:
        conn.close()

    return result


def format_device_db_info(db_info: dict) -> str:
    """将数据库信息格式化为可读字符串，用于诊断结果补充。"""
    if not db_info or not db_info.get("name"):
        return ""

    parts = []
    name = db_info.get("name", "")
    project = db_info.get("project", "")
    if name:
        parts.append(f"设备名: {name}")
    if project:
        parts.append(f"项目: {project}")

    ssh_ok = db_info.get("ssh_status")
    last_check = db_info.get("last_ssh_check", "")
    if ssh_ok is not None:
        parts.append(f"容器SSH: {'在线 ✓' if ssh_ok else '离线 ❌'}（最近检查: {last_check or '未知'}）")
    else:
        parts.append(f"容器SSH: 无记录")

    offline = db_info.get("offline_duration", "")
    last_conn = db_info.get("last_connected", "")
    if offline:
        parts.append(f"离线时长: {offline}（最后连接: {last_conn}）")
    elif last_conn:
        parts.append(f"最后连接: {last_conn}")

    cpu = db_info.get("cpu_usage")
    mem = db_info.get("mem_usage")
    mem_total = db_info.get("mem_total_gb")
    disk = db_info.get("disk_usage")
    disk_total = db_info.get("disk_total_gb")
    stats = []
    if cpu is not None:
        stats.append(f"CPU: {cpu:.1f}%")
    if mem is not None:
        mem_str = f"内存: {mem:.1f}%"
        if mem_total:
            mem_str += f" ({mem_total:.0f}GB)"
        stats.append(mem_str)
    if disk is not None:
        disk_str = f"硬盘: {disk:.1f}%"
        if disk_total:
            disk_str += f" ({disk_total:.0f}GB)"
        stats.append(disk_str)
    if stats:
        last_stats = db_info.get("last_stats_update", "")
        stats_suffix = f"（更新: {last_stats}）" if last_stats else ""
        parts.append("系统指标: " + ", ".join(stats) + stats_suffix)

    jpg = db_info.get("event_jpg_count")
    last_ec = db_info.get("last_event_check", "")
    if jpg is not None:
        parts.append(f"今日图片: {jpg} 张（检查: {last_ec or '未知'}）")

    pm = db_info.get("physical_machine", {})
    if pm:
        pm_parts = []
        if pm.get("is_healthy") is not None:
            pm_parts.append(f"健康: {'是 ✓' if pm['is_healthy'] else '否 ❌'}")
        if pm.get("running_containers") is not None:
            pm_parts.append(f"运行容器数: {pm['running_containers']}")
        if pm.get("last_check"):
            pm_parts.append(f"最近检查: {pm['last_check']}")
        if pm.get("first_failure_at"):
            pm_parts.append(f"首次故障: {pm['first_failure_at']}")
        if pm.get("gpu_info"):
            pm_parts.append(f"GPU: {pm['gpu_info']}")
        if pm_parts:
            parts.append("物理机: " + ", ".join(pm_parts))

    procs = db_info.get("supervisor_processes", [])
    if procs:
        running = [p for p in procs if p["status"] == "running"]
        stopped = [p for p in procs if p["status"] != "running"]
        proc_str = f"{len(running)}/{len(procs)} 运行中"
        if stopped:
            proc_str += f"（异常: {', '.join(p['name'] for p in stopped)}）"
        last_proc = procs[0].get("updated_at", "") if procs else ""
        if last_proc:
            proc_str += f"（更新: {last_proc}）"
        parts.append(f"进程: {proc_str}")

    history = db_info.get("image_history", [])
    if history:
        h_parts = []
        for h in history[:7]:
            h_parts.append(f"{h['date']}: {h['count']}张")
        parts.append("近7天图片: " + ", ".join(h_parts))

    return "\n".join(parts)


if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "10.145.4.1"

    devices = lookup_device(query)
    if devices:
        for d in devices:
            print(f"设备: {d['name']} | IP: {d['ip']} | 项目: {d['project']} | 杆位: {d['pole']}")
            info = get_sensor_status(d['ip'], d['project'])
            print(format_sensor_status(info))
            print()
    else:
        print(f"未找到设备: {query}")
