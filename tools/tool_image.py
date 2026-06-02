import json
import re

from langchain_core.tools import tool
from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB

EVENT_TYPES = {
    0: "无事件", 1: "逆行", 2: "大车超高速", 3: "小车超高速", 4: "大车超低速",
    5: "小车超低速", 6: "停车", 7: "占用应急车道行驶", 8: "压线", 9: "变道",
    10: "占用应急车道停车", 11: "占用应急车道逆行", 12: "行人非法闯入", 13: "动物闯进",
    14: "抛撒物", 15: "货车走主干道", 16: "非机动车闯禁", 17: "非法穿越导流线区域",
    18: "导流线区域停车", 19: "未保持安全车距", 20: "机动车驶离",
    21: "轻度拥堵", 22: "中度拥堵", 23: "重度拥堵", 24: "急加速", 25: "急减速",
    26: "急转弯", 31: "施工",
}


@tool
def query_mec_event_records(ip: str = "", project: str = "", date: str = "", event_type: int = -1, plate: str = "", start_time: str = "", end_time: str = "", device_name: str = "", limit: int = 20) -> str:
    """从数据库查询设备或项目的事件记录列表，无需SSH连接。

    支持按项目、设备、日期、事件类型、车牌、时间段、设备名等多条件组合过滤。
    每条记录可通过 record_id 调用 fetch_event_image 查看对应图片。

    Args:
        ip: 设备IP地址（可选，与project二选一）
        project: 项目名（可选，与ip二选一），如德会、德会隧道、柯诸等
        date: 日期（YYYY-MM-DD），默认为今天
        event_type: 事件类型编号或中文名（可选），如1/逆行，14/抛撒物。不传或传-1则查全部
        plate: 车牌模糊匹配（可选），如"沪A"
        start_time: 起始时间（可选），格式 HH:MM，如"10:00"
        end_time: 结束时间（可选），格式 HH:MM，如"11:00"
        device_name: 设备名模糊匹配（可选），如"mec_01"
        limit: 最多返回条数，默认20条
    """
    from datetime import date as dt_date
    import pymysql

    if not ip and not project:
        return json.dumps({"error": "请指定设备IP或项目名"}, ensure_ascii=False)

    if not date:
        date = dt_date.today().strftime("%Y-%m-%d")

    if isinstance(event_type, str):
        rev_map = {v: k for k, v in EVENT_TYPES.items()}
        event_type = rev_map.get(event_type, -1)

    try:
        conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                               database=MYSQL_DB, charset="utf8mb4", connect_timeout=5,
                               cursorclass=pymysql.cursors.DictCursor,
                               read_timeout=10, write_timeout=5)
    except Exception as e:
        return json.dumps({"error": f"数据库连接失败: {e}"}, ensure_ascii=False)

    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")

            params = []
            where_clauses = []

            if ip:
                where_clauses.append("md.host = %s")
                params.append(ip)
            else:
                where_clauses.append("md.project = %s")
                params.append(project)

            where_clauses.append("er.date = %s")
            params.append(date)

            if event_type >= 0:
                where_clauses.append("er.event = %s")
                params.append(event_type)

            if plate:
                where_clauses.append("er.plate LIKE %s")
                params.append(f"%{plate}%")

            if device_name:
                where_clauses.append("md.name LIKE %s")
                params.append(f"%{device_name}%")

            if start_time:
                where_clauses.append("TIME(er.event_time) >= %s")
                params.append(start_time)

            if end_time:
                where_clauses.append("TIME(er.event_time) <= %s")
                params.append(end_time)

            where_sql = " AND ".join(where_clauses)
            sql = f"""
                SELECT er.id, er.event_time, er.event, er.plate, er.plate_color,
                       er.vehicle_type, er.vehicle_color, er.velocity,
                       er.lane_id, er.mileage, er.image_filename, er.ftp_dir,
                       md.name AS device_name, md.host
                FROM event_record er
                JOIN mec_device md ON er.device_id = md.id
                WHERE {where_sql}
                ORDER BY er.event_time DESC
                LIMIT %s
            """
            params.append(limit)
            cursor.execute(sql, tuple(params))
            records = cursor.fetchall()
    except Exception as e:
        conn.close()
        return json.dumps({"error": f"数据库查询失败: {e}"}, ensure_ascii=False)

    conn.close()

    if not records:
        scope = f"项目 {project}" if project else f"设备 {ip}"
        return json.dumps({"message": f"{scope} 在 {date} 无事件记录"}, ensure_ascii=False)

    scope = f"项目 {project}" if project else f"设备 {ip}"
    lines = [f"\U0001f4cb **{scope} {date} 事件记录**（共 {len(records)} 条）\n"]
    if project:
        lines.append("| 序号 | 设备名 | 时间 | 事件类型 | 车牌 | 车速(km/h) | 车道 | record_id |")
        lines.append("|------|--------|------|----------|------|-----------|------|-----------|")
    else:
        lines.append("| 序号 | 时间 | 事件类型 | 车牌 | 车速(km/h) | 车道 | record_id |")
        lines.append("|------|------|----------|------|-----------|------|-----------|")

    for i, r in enumerate(records, 1):
        et = EVENT_TYPES.get(r.get("event"), f"未知({r.get('event')})")
        etime = r.get("event_time").strftime("%H:%M:%S") if r.get("event_time") else "-"
        plate = r.get("plate") or "-"
        vel = f"{r['velocity']:.0f}" if r.get("velocity") is not None else "-"
        lane = str(r.get("lane_id")) if r.get("lane_id") is not None else "-"
        rid = r["id"]
        if project:
            dev = r.get("device_name") or r.get("host", "-")
            lines.append(f"| {i} | {dev} | {etime} | {et} | {plate} | {vel} | {lane} | {rid} |")
        else:
            lines.append(f"| {i} | {etime} | {et} | {plate} | {vel} | {lane} | {rid} |")

    lines.append(f"\n\U0001f4a1 想看某条事件图片，请使用 record_id 调用 fetch_event_image 工具")
    return "\n".join(lines)


@tool
def query_mec_project_event_stats(project: str, date: str = "") -> str:
    """查询项目下的事件统计汇总，包括事件总数、按事件类型分类、按设备分类。

    Args:
        project: 项目名，如德会、德会隧道、柯诸等
        date: 日期（YYYY-MM-DD），默认为今天
    """
    from datetime import date as dt_date
    import pymysql

    if not project:
        return json.dumps({"error": "未指定项目名"}, ensure_ascii=False)

    if not date:
        date = dt_date.today().strftime("%Y-%m-%d")

    try:
        conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                               database=MYSQL_DB, charset="utf8mb4", connect_timeout=5,
                               cursorclass=pymysql.cursors.DictCursor,
                               read_timeout=10, write_timeout=5)
    except Exception as e:
        return json.dumps({"error": f"数据库连接失败: {e}"}, ensure_ascii=False)

    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM event_record er
                JOIN mec_device md ON er.device_id = md.id
                WHERE md.project = %s AND er.date = %s
            """, (project, date))
            total_row = cursor.fetchone()
            total = total_row["total"] if total_row else 0

            cursor.execute("""
                SELECT er.event, COUNT(*) AS cnt
                FROM event_record er
                JOIN mec_device md ON er.device_id = md.id
                WHERE md.project = %s AND er.date = %s
                GROUP BY er.event
                ORDER BY cnt DESC
            """, (project, date))
            by_type = cursor.fetchall()

            cursor.execute("""
                SELECT md.name AS device_name, md.host, COUNT(*) AS cnt
                FROM event_record er
                JOIN mec_device md ON er.device_id = md.id
                WHERE md.project = %s AND er.date = %s
                GROUP BY md.id, md.name, md.host
                ORDER BY cnt DESC
            """, (project, date))
            by_device = cursor.fetchall()
    except Exception as e:
        conn.close()
        return json.dumps({"error": f"数据库查询失败: {e}"}, ensure_ascii=False)

    conn.close()

    if total == 0:
        return json.dumps({"message": f"项目 {project} 在 {date} 无事件记录"}, ensure_ascii=False)

    lines = [f"\U0001f4ca **项目 {project} {date} 事件统计**\n"]
    lines.append(f"总事件数: **{total}** 条\n")

    if by_type:
        lines.append("### 按事件类型")
        lines.append("| 事件类型 | 数量 |")
        lines.append("|----------|------|")
        for r in by_type:
            et = EVENT_TYPES.get(r["event"], f"未知({r['event']})")
            lines.append(f"| {et} | {r['cnt']} |")

    if by_device:
        lines.append("\n### 按设备")
        lines.append("| 设备名 | IP | 事件数 |")
        lines.append("|--------|-----|--------|")
        for r in by_device:
            lines.append(f"| {r['device_name']} | {r['host']} | {r['cnt']} |")

    lines.append(f"\n想看某类事件的具体图片，请使用 query_event_records(project='{project}', event_type=类型编号) 查询")
    return "\n".join(lines)


def _process_event_image(image_bytes: bytes, obj_x=None, obj_y=None, obj_w=None, obj_h=None) -> bytes:
    from PIL import Image, ImageDraw
    import io

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_w, img_h = img.size

    MAX_WIDTH = 800
    if img_w > MAX_WIDTH:
        ratio = MAX_WIDTH / img_w
        new_w = MAX_WIDTH
        new_h = int(img_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img_w, img_h = img.size

    if obj_x is not None and obj_y is not None and obj_w is not None and obj_h is not None:
        draw = ImageDraw.Draw(img)
        x1 = obj_x * img_w
        y1 = obj_y * img_h
        x2 = (obj_x + obj_w) * img_w
        y2 = (obj_y + obj_h) * img_h
        draw.rectangle([x1, y1, x2, y2], outline="#00FF00", width=4)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@tool
def query_mec_event_image(record_id: int) -> str:
    """根据事件记录ID，从远程设备抓取事件图片到本地并返回图片URL。

    图片会自动缩放至800px宽，如有目标框数据则绘制绿色矩形框。

    Args:
        record_id: 事件记录ID（从 query_mec_event_records 的结果中获取）
    """
    import pymysql
    from config import EVENT_IMAGE_TEMP_DIR
    from pathlib import Path

    try:
        conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                               database=MYSQL_DB, charset="utf8mb4", connect_timeout=5,
                               cursorclass=pymysql.cursors.DictCursor,
                               read_timeout=10, write_timeout=5)
    except Exception as e:
        return json.dumps({"error": f"数据库连接失败: {e}"}, ensure_ascii=False)

    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            cursor.execute("""
                SELECT er.id, er.ftp_dir, er.image_filename, er.date,
                       er.obj_x, er.obj_y, er.obj_w, er.obj_h,
                       md.host, md.name AS device_name
                FROM event_record er
                JOIN mec_device md ON er.device_id = md.id
                WHERE er.id = %s
            """, (record_id,))
            record = cursor.fetchone()
    except Exception as e:
        conn.close()
        return json.dumps({"error": f"数据库查询失败: {e}"}, ensure_ascii=False)

    conn.close()

    if not record:
        return json.dumps({"error": f"事件记录 {record_id} 不存在"}, ensure_ascii=False)

    ftp_dir = record.get("ftp_dir") or record["date"].strftime("%Y-%m-%d")
    filename = record.get("image_filename")
    host = record["host"]

    if not filename:
        return json.dumps({"error": f"事件记录 {record_id} 无图片文件名"}, ensure_ascii=False)

    cache_dir = Path(str(EVENT_IMAGE_TEMP_DIR)) / str(record_id)
    cache_file = cache_dir / filename
    if cache_file.exists():
        return f"![事件图片](/event_image/{record_id}/{filename})"

    from diagnose_mec.ssh import ssh_exec, find_physical_user, _get_device_credentials, CONTAINER_PORT, CONTAINER_USER

    remote_path = f"/home/files/nfsroot/{ftp_dir}/{filename}"
    alt_path = None
    if filename.lower().endswith('.mp4'):
        alt_path = f"/home/files/nfsroot/{ftp_dir}/{filename[:-4]}.jpg"

    def _try_read(path):
        """Try to read a remote file via base64. Returns (stdout, stderr, code)."""
        nonlocal host
        stdout, stderr, code = ssh_exec(host, CONTAINER_PORT, CONTAINER_USER,
                                         f"base64 {path}", exec_timeout=30)
        if code == 0 and stdout:
            return stdout, stderr, code
        physical_user, login_method = find_physical_user(host)
        if not physical_user:
            return stdout, stderr, code
        is_password = login_method == "password"
        password = ""
        if is_password:
            creds = _get_device_credentials(host)
            password = creds.get("pm_password") or creds.get("password", "")
        from diagnose_mec.ssh import _docker_cmd
        dc = _docker_cmd
        stdout2, stderr2, code2 = ssh_exec(
            host, 22, physical_user,
            dc(physical_user, f"docker exec dev bash -l -c 'base64 {path}' 2>&1"),
            exec_timeout=30, password=password
        )
        return stdout2, stderr2, code2

    stdout, stderr, code = _try_read(remote_path)

    if (code != 0 or not stdout) and alt_path:
        stdout, stderr, code = _try_read(alt_path)
        if code == 0 and stdout:
            remote_path = alt_path

    if code != 0 or not stdout:
        err = (stderr or stdout or "").strip()[:100]
        return json.dumps({"error": f"读取远程图片失败: {err}"}, ensure_ascii=False)

    import base64
    try:
        image_data = base64.b64decode(stdout.strip())
    except Exception as e:
        return json.dumps({"error": f"图片解码失败: {e}"}, ensure_ascii=False)

    image_data = _process_event_image(
        image_data,
        obj_x=record.get("obj_x"),
        obj_y=record.get("obj_y"),
        obj_w=record.get("obj_w"),
        obj_h=record.get("obj_h"),
    )

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(image_data)

    return f"![事件图片](/event_image/{record_id}/{filename})"