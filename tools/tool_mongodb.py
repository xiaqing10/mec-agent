import json
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from config import MONGO_HOST, MONGO_PORT, MONGO_DB
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# 内部辅助：获取 MongoDB 连接
# ---------------------------------------------------------------------------
_EVENT_TYPE_MAP = {
    0: "无事件", 1: "逆行", 2: "大车超高速", 3: "小车超高速",
    4: "大车超低速", 5: "小车超低速", 6: "停车", 7: "占用应急车道行驶",
    8: "压线", 9: "变道", 11: "占用应急车道逆行", 12: "行人非法闯入",
    14: "抛撒物", 15: "货车走主干道", 16: "非机动车闯禁",
    17: "非法穿越导流线区域", 18: "导流线区域停车", 19: "未保持安全车距",
    20: "机动车驶离", 21: "轻度拥堵", 22: "中度拥堵", 23: "重度拥堵",
    24: "急加速", 25: "急减速", 26: "急转弯", 27: "未定义事件",
    31: "施工",
}
_EVENT_NAME_TO_ID = {v: k for k, v in _EVENT_TYPE_MAP.items()}

_VEHICLE_TYPE_MAP = {
    0: "未知", 1: "客车", 2: "公交车", 3: "货车", 4: "非机动车", 5: "行人",
}


def _resolve_event_type(val: str) -> int:
    """将 event_type 参数解析为整数编号。支持数字字符串或中文名称。"""
    if not val or val == "-1":
        return -1
    try:
        return int(val)
    except ValueError:
        return _EVENT_NAME_TO_ID.get(val, -1)


def _to_bool(val) -> bool:
    """将任意类型转布尔，兼容字符串/数字/布尔。"""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "y")
    return bool(val)


def _get_mongo():
    from pymongo import MongoClient
    client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000)
    return client[MONGO_DB]


def _fmt_event(e, detail=False):
    t = _EVENT_TYPE_MAP.get(e.get("event"), f"未知({e.get('event')})")
    vt = _VEHICLE_TYPE_MAP.get(e.get("vehicleType"), f"未知({e.get('vehicleType')})")
    ts = e.get("creatTime", e.get("timestamp", ""))
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
    elif hasattr(ts, "strftime"):
        ts = ts.strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"• [{ts}] 事件={t}({e.get('event')}) | "
        f"设备={e.get('devNo','')} | "
        f"车道={e.get('LaneId','')} | "
        f"方向={'上行' if e.get('direction')==0 else '下行' if e.get('direction')==1 else e.get('direction')} | "
        f"车速={e.get('velocity','')}km/h | "
        f"车型={vt} | "
        f"车牌={e.get('plate','') or '无'}"
    )
    if detail:
        line += (
            f"\n  经纬度=({e.get('lat','')},{e.get('lng','')}) | "
            f"图像={e.get('ftpImg','')} | "
            f"视频={e.get('ftpVideo','')}"
        )
    return line


def _parse_time_filter(start_time, end_time, field="creatTime"):
    """将用户时间字符串转为 MongoDB 查询条件。

    Args:
        field: 目标字段名，"creatTime" 或 "EndTime"
    """
    query = {}
    if start_time:
        try:
            query["$gte"] = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                query["$gte"] = datetime.strptime(start_time, "%Y-%m-%d")
            except ValueError:
                pass
    if end_time:
        try:
            query["$lte"] = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                query["$lte"] = datetime.strptime(end_time, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                pass
    return {field: query} if query else {}


def _build_timestamp_regex(start_time: str, end_time: str) -> dict:
    """将时间范围转为 Timestamp 字段的正则匹配条件。

    flow 集合的 Timestamp 字段格式: "2026-06-02 00:00:00.38"
    需要生成正则表达式来匹配时间范围内的记录。

    Returns:
        MongoDB $regex 正则表达式
    """
    def _parse_ts(s):
        if isinstance(s, datetime):
            return s
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    start_dt = _parse_ts(start_time) if start_time else None
    end_dt = _parse_ts(end_time) if end_time else None

    if not start_dt and not end_dt:
        now = datetime.now()
        return {"$regex": now.strftime("^%Y-%m-%d %H:%M")}

    if start_dt and end_dt:
        if start_dt.date() == end_dt.date():
            if start_dt.hour == end_dt.hour:
                date_str = start_dt.strftime("%Y-%m-%d")
                hour = start_dt.hour
                min_start = start_dt.minute
                min_end = end_dt.minute
                if min_start == 0 and min_end == 59:
                    return {"$regex": f"^{date_str} {hour:02d}:"}
                # 生成分钟字符类
                minute_str = "".join(str(i) for i in range(min_start, min_end + 1))
                return {"$regex": f"^{date_str} {hour:02d}:0[{minute_str}]"}
            else:
                date_str = start_dt.strftime("%Y-%m-%d")
                hours = list(range(start_dt.hour, end_dt.hour + 1))
                if len(hours) <= 3:
                    hour_class = "".join(str(h) for h in hours)
                    return {"$regex": f"^{date_str} [{hour_class}]:"}
                else:
                    return {"$regex": f"^{date_str} "}
        else:
            # 跨天: 简化处理，匹配日期范围
            return {"$regex": f"^{start_dt.strftime('%Y-%m-%d')} "}

    if start_dt:
        return {"$gte": start_dt.strftime("%Y-%m-%d %H:%M:%S")}
    if end_dt:
        return {"$lte": end_dt.strftime("%Y-%m-%d %H:%M:%S")}

    return {"$regex": datetime.now().strftime("^%Y-%m-%d %H:%M")}


def _llm_analysis(prompt: str, system: str = "") -> str:
    """内部调用 LLM 对聚合数据进行二次分析。"""
    from config import AVAILABLE_MODELS
    cfg = AVAILABLE_MODELS.get("deepseek-v4-flash", {})
    url = f"{cfg.get('base_url', 'https://ark.cn-beijing.volces.com/api/coding/v3')}/chat/completions"
    api_key = cfg.get("api_key", "")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": "deepseek-v4-flash",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM 分析调用失败: {e}"


# ---------------------------------------------------------------------------
# Tool 1: 查询断面流量
# ---------------------------------------------------------------------------
@tool
def query_server_traffic_flow(
    road_name: str = "",
    start_time: str = "",
    end_time: str = "",
    direction: str = "",
) -> str:
    """查询指定路段和时间段的断面流量数据。

    数据来源：MongoDB radarData.flow 集合（1分钟粒度，嵌套数组结构）。
    每条文档包含 Stats 数组，每个元素是一条车道的记录。
    包含：各车道车流量（TotalCount/CarCount/TruckCount/BusCount/VanCount/NonVehicleCount）、
    平均速度（AvgVelocity）、时间占有率（TimeOccupancy）、空间占有率（SpaceOccupancy）。

    计算规则：
    - 流量（TotalCount/CarCount 等）：各分钟记录累加
    - 占有率（TimeOccupancy/SpaceOccupancy）：各分钟记录取平均
    - 平均车速（AvgVelocity）：加权平均（每分钟速度 × 该分钟流量 / 总流量）

    注意：同一时间点存在"上行"、"下行"、"双向"三种记录，其中"双向"="上行"+"下行"。
    本工具只统计上行和下行，排除双向记录以避免重复计算。

    Args:
        road_name: 道路名/桩号，如 "K7+197"、"K6+230"（可选，不传则查所有）
        start_time: 开始时间，格式 "2026-05-01 00:00:00" 或 "2026-05-01"（可选）
        end_time: 结束时间，格式同上（可选，默认最近一小时）
        direction: 行驶方向，"上行"/"下行"（可选，默认不限制，但会排除"双向"记录）
    """
    db = _get_mongo()

    # flow 集合中 Timestamp 字段存储的是实际数据时间（字符串格式）
    # creatTime 字段存储的是入库时间，两者数据不同
    # 用户看到的数据来自 Timestamp 字段，所以用 Timestamp 过滤
    if start_time or end_time:
        # 将时间字符串转为 Timestamp 正则格式
        # 例如 "2026-06-02 00:00:00" -> "2026-06-02 00:00"
        # 或者 "2026-06-02 00:00:00" ~ "2026-06-02 00:05:00" -> "2026-06-02 00:0[0-4]"
        ts_match = _build_timestamp_regex(start_time, end_time)
        match = {"Timestamp": ts_match}
    else:
        # 默认最近15分钟
        now = datetime.now()
        match = {"Timestamp": {"$regex": now.strftime("^%Y-%m-%d %H:%M")}}

    if road_name:
        match["Stats.RoadName"] = road_name

    try:
        # 查询 flow 集合，在 Python 中过滤 Stats 数组
        # flow 集合中每个时间点有多个车道记录：Lane=1,2,3,4,5,6,0,ALL
        # 双向+ALL 是汇总记录，各车道记录是明细
        cursor = db["flow"].find(match, {"Stats": 1, "Timestamp": 1}).sort("Timestamp", 1)

        # 双向汇总聚合
        agg = {}
        # 车道聚合：{roadName: {laneNum: {data}}}
        lane_agg = {}
        for doc in cursor:
            for s in doc.get("Stats", []):
                rn = s.get("RoadName", "")
                dr = s.get("Direction", "")
                ln = s.get("LaneNum", "")
                tc = s.get("TotalCount", 0)

                if road_name and rn != road_name:
                    continue
                if tc <= 0:
                    continue

                # 双向+ALL 汇总记录
                if dr == "双向" and ln == "ALL":
                    if direction and direction != "双向":
                        continue
                    key = rn
                    if key not in agg:
                        agg[key] = {
                            "roadName": rn, "direction": "双向",
                            "records": 0, "totalCount": 0, "carCount": 0, "truckCount": 0,
                            "busCount": 0, "vanCount": 0, "nonVehicleCount": 0,
                            "weightedVel": 0, "timeOccupancySum": 0, "spaceOccupancySum": 0,
                        }
                    a = agg[key]
                    a["records"] += 1
                    a["totalCount"] += tc
                    a["carCount"] += s.get("CarCount", 0)
                    a["truckCount"] += s.get("TruckCount", 0)
                    a["busCount"] += s.get("BusCount", 0)
                    a["vanCount"] += s.get("VanCount", 0)
                    a["nonVehicleCount"] += s.get("NonVehicleCount", 0)
                    a["weightedVel"] += s.get("AvgVelocity", 0) * tc
                    a["timeOccupancySum"] += s.get("TimeOccupancy", 0)
                    a["spaceOccupancySum"] += s.get("SpaceOccupancy", 0)

                # 各车道记录（Lane=1~6 或 Lane=0）
                elif ln != "ALL":
                    if direction and dr != direction:
                        continue
                    if rn not in lane_agg:
                        lane_agg[rn] = {}
                    if ln not in lane_agg[rn]:
                        lane_agg[rn][ln] = {
                            "roadName": rn, "direction": dr, "laneNum": ln,
                            "records": 0, "totalCount": 0, "carCount": 0, "truckCount": 0,
                            "weightedVel": 0, "timeOccupancySum": 0, "spaceOccupancySum": 0,
                        }
                    la = lane_agg[rn][ln]
                    la["records"] += 1
                    la["totalCount"] += tc
                    la["carCount"] += s.get("CarCount", 0)
                    la["truckCount"] += s.get("TruckCount", 0)
                    la["weightedVel"] += s.get("AvgVelocity", 0) * tc
                    la["timeOccupancySum"] += s.get("TimeOccupancy", 0)
                    la["spaceOccupancySum"] += s.get("SpaceOccupancy", 0)

        results = sorted(agg.values(), key=lambda x: -x["totalCount"])
    except Exception as e:
        return json.dumps({"error": f"流量查询失败: {e}"}, ensure_ascii=False)

    if not results:
        return "⚠️ 指定条件下未查询到流量数据。"

    lines = ["📊 **断面流量统计**\n"]
    if start_time or end_time:
        lines.append(f"时间: {start_time or '不限'} ~ {end_time or '不限'}")
    else:
        lines.append(f"时间: 最近一小时")
    lines.append("")

    # 双向汇总表
    lines.append("**双向汇总:**")
    lines.append("| 路段 | 方向 | 总车数 | 小车 | 货车 | 客车 | 非机动车 | 均速(km/h) | 时间占有率 | 空间占有率 |")
    lines.append("|------|------|--------|------|------|------|----------|------------|------------|------------|")
    for r in results:
        recs = max(r["records"], 1)
        avg_vel = r["weightedVel"] / max(r["totalCount"], 1)
        # 双向+ALL 记录的时间占有率可能为0，优先从 Lane=0 的记录获取
        tm = r["timeOccupancySum"] / recs
        sp = r["spaceOccupancySum"] / recs
        rn = r["roadName"]
        if tm == 0 and rn in lane_agg and "0" in lane_agg[rn]:
            # 从 Lane=0 记录获取时间占有率和空间占有率
            lane0 = lane_agg[rn]["0"]
            lane0_recs = max(lane0["records"], 1)
            tm = lane0["timeOccupancySum"] / lane0_recs
            sp = lane0["spaceOccupancySum"] / lane0_recs
        lines.append(
            f"| {r['roadName']} | {r['direction']} | "
            f"{r['totalCount']} | {r['carCount']} | {r['truckCount']} | "
            f"{r['busCount']} | {r['nonVehicleCount']} | "
            f"{avg_vel:.1f} | {tm*100:.1f}% | {sp*100:.1f}% |"
        )

    # 车道明细表
    if lane_agg:
        lines.append("\n**车道明细:**")
        lines.append("| 路段 | 方向 | 车道 | 总车数 | 小车 | 货车 | 均速(km/h) |")
        lines.append("|------|------|------|--------|------|------|------------|")
        for rn in sorted(lane_agg.keys()):
            for ln in sorted(lane_agg[rn].keys(), key=lambda x: (x != "0", x)):
                la = lane_agg[rn][ln]
                recs = max(la["records"], 1)
                avg_vel = la["weightedVel"] / max(la["totalCount"], 1)
                lines.append(
                    f"| {la['roadName']} | {la['direction']} | {la['laneNum']} | "
                    f"{la['totalCount']} | {la['carCount']} | {la['truckCount']} | "
                    f"{avg_vel:.1f} |"
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: 查询事件记录
# ---------------------------------------------------------------------------
@tool
def query_server_events(
    dev_no: str = "",
    start_time: str = "",
    end_time: str = "",
event_type: str = "-1",
    limit: int = 50,
    detail: bool = False,
    show_image: str = "false",
) -> str:
    """查询雷达事件记录。

    数据来源：MongoDB radarData.event 集合。
    事件类型:
        -1=全部 0=无事件 1=逆行 2=大车超高速 3=小车超高速 4=大车超低速 5=小车超低速
        6=停车 7=占用应急车道行驶 8=压线 9=变道 11=占用应急车道逆行
        12=行人非法闯入 14=抛撒物 15=货车走主干道 16=非机动车闯禁
        17=非法穿越导流线区域 18=导流线区域停车 19=未保持安全车距 20=机动车驶离
        21=轻度拥堵 22=中度拥堵 23=重度拥堵 24=急加速 25=急减速 26=急转弯 31=施工

    Args:
        dev_no: 设备编号，如 "k9_820"、"k5_812"（可选）
        start_time: 开始时间，格式 "2026-05-01 00:00:00" 或 "2026-05-01"
        end_time: 结束时间，格式同上
        event_type: 事件类型编号（可选，-1 表示全部），也支持中文名称如"非机动车闯禁"，默认 "-1"
        limit: 最大返回条数（默认 50，最多 200）
        detail: 是否返回详细位置和媒体信息（默认 False）
show_image: 是否显示事件图片（默认 False，传 "True" 即可显示图片，通过 HTTP 读取 ftpImg）
    """
    db = _get_mongo()
    match = {}
    if dev_no:
        match["devNo"] = dev_no
    et = _resolve_event_type(event_type)
    if et >= 0:
        match["event"] = et
    show_image_bool = _to_bool(show_image)
    time_filter = _parse_time_filter(start_time, end_time)
    if time_filter:
        match.update(time_filter)
    else:
        # 默认查询今天的数据
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        match["creatTime"] = {"$gte": today}

    try:
        cursor = (
            db["event"]
            .find(match, {"_id": 0})
            .sort("creatTime", -1)
            .limit(min(limit, 200))
        )
        events = list(cursor)
    except Exception as e:
        return json.dumps({"error": f"事件查询失败: {e}"}, ensure_ascii=False)

    if not events:
        return "⚠️ 未查询到匹配的事件记录。"

    lines = [f"📋 **事件记录（共{len(events)}条）**\n"]
    if dev_no:
        lines.append(f"设备: {dev_no}")
    if et >= 0:
        lines.append(f"事件类型: {_EVENT_TYPE_MAP.get(et, et)}({et})")
    if start_time or end_time:
        lines.append(f"时间: {start_time or '不限'} ~ {end_time or '不限'}")
    lines.append("")

    for e in events:
        lines.append(_fmt_event(e, detail=detail))

    if show_image_bool:
        for e in events:
            ftp_img = e.get("ftpImg", "")
            ftp_dir = e.get("ftpDir", "")
            dev_no_val = e.get("devNo", "")
            if ftp_img and ftp_dir and dev_no_val:
                dev_part = dev_no_val.upper().replace("_", "-")
                from config import EVENT_IMAGE_BASE_URL
                url = f"{EVENT_IMAGE_BASE_URL}/{dev_part}/{ftp_dir}/{ftp_img}"
                try:
                    req = urllib.request.Request(url)
                    resp = urllib.request.urlopen(req, timeout=5)
                    img_bytes = resp.read()
                    import base64
                    b64 = base64.b64encode(img_bytes).decode()
                    evt_type = _EVENT_TYPE_MAP.get(e.get("event"), f"事件{e.get('event')}")
                    img_tag = f"\n![{evt_type}](data:image/jpeg;base64,{b64})"
                    lines.append(img_tag)
                except Exception:
                    pass

    # 按事件类型汇总
    type_counts = {}
    for e in events:
        et = e.get("event")
        type_counts[et] = type_counts.get(et, 0) + 1
    if len(type_counts) > 1:
        lines.append("\n**事件类型分布:**")
        for et, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {_EVENT_TYPE_MAP.get(et, f'未知({et})')}: {cnt}次")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: 事件统计
# ---------------------------------------------------------------------------
@tool
def query_server_event_stats(
    start_time: str = "",
    end_time: str = "",
    dev_no: str = "",
) -> str:
    """按事件类型统计事件数量分布。

    可用于：了解一段时间内各类交通事件（违章/事故/异常）的发生频率。

    Args:
        start_time: 开始时间，格式 "2026-05-01 00:00:00" 或 "2026-05-01"
        end_time: 结束时间，格式同上
        dev_no: 设备编号（可选，不传则统计全部设备）
    """
    db = _get_mongo()
    match = {}
    if dev_no:
        match["devNo"] = dev_no
    time_filter = _parse_time_filter(start_time, end_time)
    if time_filter:
        match.update(time_filter)

    try:
        pipeline = [
            {"$match": match},
            {"$group": {"_id": "$event", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        results = list(db["event"].aggregate(pipeline, allowDiskUse=True))
    except Exception as e:
        return json.dumps({"error": f"事件统计查询失败: {e}"}, ensure_ascii=False)

    if not results:
        return "⚠️ 指定条件下无事件数据。"

    total = sum(r["count"] for r in results)
    lines = [f"📊 **事件统计（共{total}条）**\n"]
    if dev_no:
        lines.append(f"设备: {dev_no}")
    if start_time or end_time:
        lines.append(f"时间: {start_time or '不限'} ~ {end_time or '不限'}")
    lines.append("")
    lines.append("| 事件类型 | 事件编号 | 数量 | 占比 |")
    lines.append("|----------|----------|------|------|")
    for r in results:
        et = r["_id"]
        name = _EVENT_TYPE_MAP.get(et, f"未知({et})")
        pct = r["count"] / total * 100
        lines.append(f"| {name} | {et} | {r['count']} | {pct:.1f}% |")

    # 违章类汇总（逆行/超高速/超低速/占用应急车道/压线等）
    violation_types = {1, 2, 3, 4, 5, 7, 8, 9, 11, 15, 16, 17, 18, 19}
    violation_count = sum(r["count"] for r in results if r["_id"] in violation_types)
    congestion_types = {21, 22, 23}
    congestion_count = sum(r["count"] for r in results if r["_id"] in congestion_types)
    lines.append(f"\n📌 违章事件合计: {violation_count}次 ({violation_count/total*100:.1f}%)")
    lines.append(f"📌 拥堵事件合计: {congestion_count}次 ({congestion_count/total*100:.1f}%)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: 查询设备运行指标
# ---------------------------------------------------------------------------
@tool
def query_server_device_metrics(
    dev_name: str = "",
    start_time: str = "",
    end_time: str = "",
    limit: int = 20,
) -> str:
    """查询 MEC 设备运行健康指标。

    数据来源：MongoDB radarData.metric 集合。
    包含：CPU使用率、内存使用率、磁盘使用率、GPU使用率、温度、
    各状态告警标志（摄像头/雷达/融合/事件/流量/SSD/诊断等）。

    Args:
        dev_name: 设备名，如 "k5_812"（可选，不传则查所有设备最新记录）
        start_time: 开始时间
        end_time: 结束时间
        limit: 最大返回条数（默认 20，最多 100）
    """
    db = _get_mongo()
    match = {}
    if dev_name:
        match["devName"] = dev_name
    time_filter = _parse_time_filter(start_time, end_time)
    if time_filter:
        match.update(time_filter)

    try:
        cursor = (
            db["metric"]
            .find(match, {"_id": 0})
            .sort("creatTime", -1)
            .limit(min(limit, 100))
        )
        metrics = list(cursor)
    except Exception as e:
        return json.dumps({"error": f"设备指标查询失败: {e}"}, ensure_ascii=False)

    if not metrics:
        return "⚠️ 未查询到设备指标数据。"

    lines = [f"📊 **设备运行指标（共{len(metrics)}条记录）**\n"]
    if dev_name:
        lines.append(f"设备: {dev_name}")
    if start_time or end_time:
        lines.append(f"时间: {start_time or '不限'} ~ {end_time or '不限'}")
    lines.append("")
    lines.append("| 设备 | 时间 | CPU% | 内存% | 磁盘% | GPU | 温度°C | 告警 | 诊断 |")
    lines.append("|------|------|------|-------|-------|-----|--------|------|------|")
    for m in metrics:
        ts = m.get("creatTime", "")
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%m-%d %H:%M")
        alarm = "⚠️ " if m.get("isAlarm") else "✅"
        diag_info = ""
        if m.get("diagnostics"):
            codes = {d.get("Error Codes", "") for d in m["diagnostics"] if d.get("Error Codes")}
            diag_info = ",".join(sorted(codes)) if codes else "有"
        lines.append(
            f"| {m.get('devName','')} | {ts} | {m.get('cpu',0)*100:.1f}% | "
            f"{m.get('mem',0)*100:.1f}% | {m.get('disk',0)*100:.1f}% | "
            f"{m.get('gpu','-')} | {m.get('temperature','-')} | {alarm} | {diag_info or '-'} |"
        )

    # 当前告警统计
    alarms = [m for m in metrics if m.get("isAlarm")]
    if alarms:
        lines.append(f"\n⚠️ **{len(alarms)}条记录含告警**")
        alarm_fields = [
            ("isCpuAlarm", "CPU"), ("isMemAlarm", "内存"), ("isDiskAlarm", "磁盘"),
            ("isTemperatureAlarm", "温度"), ("isGpuAlarm", "GPU"),
            ("isCameraStatusAlarm", "摄像头"), ("isRadarStatusAlarm", "雷达"),
            ("isRadarCameraFusionAlarm", "雷达相机融合"),
            ("isRadarFusionAlarm", "雷达融合"), ("isFusionAlarm", "融合"),
            ("isFusionTimeDifferAlarm", "融合时差"), ("isEventAlarm", "事件"),
            ("isFlowAlarm", "流量"), ("isSsdAlarm", "SSD"),
            ("isCameraOffsetAlertAlarm", "相机偏移"),
            ("isCameraInferAlarm", "相机推理"),
            ("isPitchDifferAlarm", "俯仰角差"), ("isHorizontalDifferAlarm", "水平角差"),
            ("isDiagnosticsAlarm", "诊断"),
        ]
        active = [(name, label) for name, label in alarm_fields if any(m.get(name) for m in alarms)]
        if active:
            lines.append("活跃告警类型: " + ", ".join(label for _, label in active))
    else:
        lines.append("\n✅ 当前无告警")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5: 综合交通流分析（时间序列）
# ---------------------------------------------------------------------------
@tool
def query_server_traffic_pattern(
    start_time: str = "",
    end_time: str = "",
    road_name: str = "",
    interval_minutes: int = 60,
) -> str:
    """综合交通流分析：按时间段粒度聚合流量数据，生成时间序列趋势。

    数据来源：MongoDB radarData.flow 集合（1分钟粒度，嵌套数组结构）。
    可用于：了解交通流量的日/时变化规律、识别早晚高峰、分析拥堵时段。

    计算规则：
    - 流量：各分钟记录累加
    - 占有率：各分钟记录取平均
    - 平均车速：加权平均（每分钟速度 × 该分钟流量 / 总流量）

    注意：同一时间点存在"上行"、"下行"、"双向"三种记录，其中"双向"="上行"+"下行"。
    本工具只统计上行和下行，排除双向记录以避免重复计算。

    Args:
        start_time: 开始时间，格式 "2026-05-01 00:00:00"
        end_time: 结束时间，格式同上
        road_name: 道路名/桩号（可选）
        interval_minutes: 聚合时间粒度（分钟），默认 60（即按小时聚合）
    """
    db = _get_mongo()

    # 使用 Timestamp 字段过滤（与 query_server_traffic_flow 一致）
    if start_time or end_time:
        ts_match = _build_timestamp_regex(start_time, end_time)
        match = {"Timestamp": ts_match}
    else:
        now = datetime.now()
        match = {"Timestamp": {"$regex": now.strftime("^%Y-%m-%d %H")}}

    if road_name:
        match["Stats.RoadName"] = road_name

    try:
        # 查询 flow 集合，在 Python 中过滤和聚合
        cursor = db["flow"].find(match, {"Stats": 1, "Timestamp": 1}).sort("Timestamp", 1)

        # 按 (roadName) 聚合
        agg = {}
        hourly = {}

        for doc in cursor:
            ts = doc.get("Timestamp", "")
            # 从 Timestamp 字符串中提取小时
            hour = int(ts[11:13]) if len(ts) >= 13 else 0

            for s in doc.get("Stats", []):
                rn = s.get("RoadName", "")
                dr = s.get("Direction", "")
                ln = s.get("LaneNum", "")
                tc = s.get("TotalCount", 0)

                if road_name and rn != road_name:
                    continue
                if dr != "双向" or ln != "ALL":
                    continue
                if tc <= 0:
                    continue

                key = rn
                if key not in agg:
                    agg[key] = {
                        "roadName": rn, "direction": "双向",
                        "totalVehicles": 0, "weightedVel": 0, "maxVel": 0, "minVel": float("inf"),
                        "timeOccSum": 0, "spaceOccSum": 0, "records": 0,
                        "truckCount": 0, "busCount": 0,
                    }
                a = agg[key]
                a["totalVehicles"] += tc
                a["weightedVel"] += s.get("AvgVelocity", 0) * tc
                a["maxVel"] = max(a["maxVel"], s.get("AvgVelocity", 0))
                a["minVel"] = min(a["minVel"], s.get("AvgVelocity", 0))
                a["timeOccSum"] += s.get("TimeOccupancy", 0)
                a["spaceOccSum"] += s.get("SpaceOccupancy", 0)
                a["records"] += 1
                a["truckCount"] += s.get("TruckCount", 0)
                a["busCount"] += s.get("BusCount", 0)

                # 时段分析
                hkey = (rn, hour)
                if hkey not in hourly:
                    hourly[hkey] = {"roadName": rn, "hour": hour, "totalVehicles": 0, "weightedVel": 0}
                h = hourly[hkey]
                h["totalVehicles"] += tc
                h["weightedVel"] += s.get("AvgVelocity", 0) * tc

        results = sorted(agg.values(), key=lambda x: -x["totalVehicles"])
        hourly_results = sorted(hourly.values(), key=lambda x: (x["roadName"], x["hour"]))
    except Exception as e:
        return json.dumps({"error": f"交通流分析失败: {e}"}, ensure_ascii=False)

    if not results:
        return "⚠️ 指定条件下无流量数据。"

    lines = ["📈 **交通流分析报告**\n"]
    if start_time or end_time:
        lines.append(f"时间范围: {start_time or '不限'} ~ {end_time or '不限'}")
    else:
        lines.append(f"时间范围: 最近6小时")
    lines.append(f"聚合粒度: {interval_minutes}分钟")
    lines.append("")

    total_all = sum(r["totalVehicles"] for r in results)
    lines.append(f"**总车流量: {total_all}辆**\n")

    lines.append("| 路段 | 方向 | 总车数 | 均速 | 最高速 | 最低速 | 货车 | 客车 | 时间占有率 | 空间占有率 |")
    lines.append("|------|------|--------|------|--------|--------|------|------|------------|------------|")
    for r in results:
        recs = max(r["records"], 1)
        avg_vel = r["weightedVel"] / max(r["totalVehicles"], 1)
        avg_tm = r["timeOccSum"] / recs
        avg_sp = r["spaceOccSum"] / recs
        lines.append(
            f"| {r['roadName']} | {r['direction']} | {r['totalVehicles']} | "
            f"{avg_vel:.1f} | {r['maxVel']:.1f} | {r['minVel']:.1f} | "
            f"{r['truckCount']} | {r['busCount']} | "
            f"{avg_tm*100:.1f}% | {avg_sp*100:.1f}% |"
        )

    # 时段分析
    lines.append("\n**时段流量趋势（按小时）:**\n")
    if hourly_results:
        lines.append("| 时段 | 路段 | 车流量 | 均速(km/h) |")
        lines.append("|------|------|--------|------------|")
        for h in hourly_results:
            avg_vel = h["weightedVel"] / max(h["totalVehicles"], 1)
            lines.append(
                f"| {h['hour']:02d}:00-{h['hour']+1:02d}:00 | "
                f"{h['roadName']} | {h['totalVehicles']} | {avg_vel:.1f} |"
            )

    # 拥堵分析
    congested = [r for r in results if r["records"] > 0 and (r["timeOccSum"] / r["records"]) > 0.3]
    if congested:
        lines.append(f"\n⚠️ **高占有率路段（可能拥堵）:** ")
        for r in congested:
            avg_tm = r["timeOccSum"] / max(r["records"], 1)
            avg_vel = r["weightedVel"] / max(r["totalVehicles"], 1)
            lines.append(f"  {r['roadName']} {r['direction']}: "
                         f"占有率{avg_tm*100:.1f}%, 均速{avg_vel:.1f}km/h")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6: 交通数据分析报告（LLM 二次分析）
# ---------------------------------------------------------------------------
@tool
def query_server_analysis_report(
    start_time: str = "",
    end_time: str = "",
    analysis_type: str = "summary",
) -> str:
    """交通数据分析报告。

    内部调用 LLM 对雷达交通数据（流量+事件+设备）进行二次分析，生成格式化报告。
    包括：流量趋势总结、异常识别、短时流量预测、事件-流量关联分析。

    Args:
        start_time: 开始时间，格式 "2026-05-01 00:00:00"
        end_time: 结束时间，格式同上
        analysis_type: 分析类型
          "summary" - 综合总结：流量概况 + 事件统计 + 设备健康
          "anomaly" - 异常识别：查找异常拥堵、事件高发时段/路段、设备异常
          "prediction" - 短时流量预测：基于历史趋势推断未来几小时流量变化
          "correlation" - 事件-流量关联分析：分析事件发生与流量变化的关系
    """
    db = _get_mongo()
    time_filter = _parse_time_filter(start_time, end_time, field="EndTime") or {
        "EndTime": {"$gte": datetime.now() - timedelta(hours=6)}
    }

    try:
        # 流量数据（使用 flowStat 预聚合表）
        flow_pipeline = [
            {"$match": dict(time_filter)},
            {"$match": {"TotalCount": {"$gt": 0}}},
            {
                "$group": {
                    "_id": None,
                    "totalVehicles": {"$sum": "$TotalCount"},
                    "avgVelocity": {"$avg": "$AvgVelocity"},
                    "maxVelocity": {"$max": "$AvgVelocity"},
                    "minVelocity": {"$min": "$AvgVelocity"},
                    "avgTimeOccupancy": {"$avg": "$TimeOccupancy"},
                    "truckCount": {"$sum": "$TruckCount"},
                    "busCount": {"$sum": "$BusCount"},
                    "records": {"$sum": 1},
                }
            },
        ]
        flow_data = list(db["flowStat"].aggregate(flow_pipeline, allowDiskUse=True))

        # 按小时时段流量分布
        hourly_pipeline = [
            {"$match": dict(time_filter)},
            {"$match": {"TotalCount": {"$gt": 0}}},
            {
                "$group": {
                    "_id": {"hour": {"$hour": "$EndTime"}},
                    "vehicles": {"$sum": "$TotalCount"},
                    "velocity": {"$avg": "$AvgVelocity"},
                    "occupancy": {"$avg": "$TimeOccupancy"},
                }
            },
            {"$sort": {"_id.hour": 1}},
        ]
        hourly_data = list(db["flowStat"].aggregate(hourly_pipeline, allowDiskUse=True))

        # 事件统计数据
        event_time_filter = _parse_time_filter(start_time, end_time, field="creatTime") or {}
        event_pipeline = [
            {"$match": {**event_time_filter, "event": {"$ne": 0}}},
            {"$group": {"_id": "$event", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        event_data = list(db["event"].aggregate(event_pipeline, allowDiskUse=True))

    except Exception as e:
        return json.dumps({"error": f"数据查询失败: {e}"}, ensure_ascii=False)

    # 组装数据摘要
    summary_lines = ["## 原始数据摘要\n"]
    if flow_data:
        f = flow_data[0]
        summary_lines.append(f"### 流量概况")
        summary_lines.append(f"- 总车流量: {f['totalVehicles']}辆")
        summary_lines.append(f"- 平均速度: {f['avgVelocity']:.1f} km/h")
        summary_lines.append(f"- 速度范围: {f['minVelocity']:.1f} ~ {f['maxVelocity']:.1f} km/h")
        summary_lines.append(f"- 平均时间占有率: {f['avgTimeOccupancy']*100:.1f}%")
        summary_lines.append(f"- 货车: {f['truckCount']}辆, 客车: {f['busCount']}辆")
        summary_lines.append(f"- 采样记录数: {f['records']}条")

    if hourly_data:
        summary_lines.append(f"\n### 按小时流量分布")
        peak_flow = max(hourly_data, key=lambda x: x["vehicles"])
        low_flow = min(hourly_data, key=lambda x: x["vehicles"])
        summary_lines.append(f"- 高峰时段: {peak_flow['_id']['hour']}:00, 车流量{peak_flow['vehicles']}辆, 均速{peak_flow['velocity']:.1f}km/h")
        summary_lines.append(f"- 低峰时段: {low_flow['_id']['hour']}:00, 车流量{low_flow['vehicles']}辆, 均速{low_flow['velocity']:.1f}km/h")
        peak_ocp = max(hourly_data, key=lambda x: x["occupancy"])
        if peak_ocp["occupancy"] > 0.3:
            summary_lines.append(f"- ⚠️ 高拥堵时段: {peak_ocp['_id']['hour']}:00, 占有率{peak_ocp['occupancy']*100:.1f}%")
        hourly_detail = ", ".join(f"{h['_id']['hour']}:00({h['vehicles']}辆/{h['velocity']:.0f}km/h)" for h in hourly_data)
        summary_lines.append(f"- 逐时详情: {hourly_detail}")

    if event_data:
        total_events = sum(r["count"] for r in event_data)
        summary_lines.append(f"\n### 事件统计（共{total_events}条非零事件）")
        for r in event_data:
            name = _EVENT_TYPE_MAP.get(r["_id"], f"未知({r['_id']})")
            summary_lines.append(f"- {name}({r['_id']}): {r['count']}次 ({r['count']/total_events*100:.1f}%)")

    data_summary = "\n".join(summary_lines)

    # 构建分析提示
    analysis_prompts = {
        "summary": f"""请基于以下交通数据，生成一份综合交通运行总结报告。

要求：
1. 流量概况（总车流、平均速度、高峰/低峰时段）
2. 事件概况（主要事件类型分布、违章/拥堵事件统计）
3. 整体交通运行评价（畅通/一般/拥堵）
4. 关注重点和建议

{data_summary}""",

        "anomaly": f"""请基于以下交通数据，识别交通运行中的异常情况。

要求：
1. 异常拥堵识别（时间占有率高于30%的时段）
2. 事件高发分析（哪种事件类型异常偏高）
3. 速度异常路段（远低于平均速度）
4. 综合异常评级和建议

{data_summary}""",

        "prediction": f"""请基于以下交通数据的历史趋势，预测未来几小时的交通流量变化。

要求：
1. 当前流量趋势分析
2. 基于历史时段的短时流量预测（未来2-4小时）
3. 可能出现的拥堵预警
4. 置信度说明

{data_summary}""",

        "correlation": f"""请基于以下交通数据，分析交通事件与流量之间的关系。

要求：
1. 事件高发时段与流量高峰的重合度分析
2. 不同类型事件与流量特征的关系（如：拥堵事件是否伴随速度下降）
3. 违章行为与流量密度的关系
4. 综合分析结论

{data_summary}""",
    }

    prompt = analysis_prompts.get(analysis_type, analysis_prompts["summary"])
    system = "你是一个专业的智慧交通数据分析专家。根据提供的交通数据摘要，生成专业的分析报告。使用中文、Markdown格式。"
    report = _llm_analysis(prompt, system)

    return (
        f"# 🚦 交通分析报告\n"
        f"分析类型: {analysis_type}\n"
        f"时间范围: {start_time or '不限'} ~ {end_time or '不限'}\n\n"
        f"---\n\n"
        f"{report}"
    )