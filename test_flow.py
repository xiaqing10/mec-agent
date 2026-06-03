#!/usr/bin/env python3
"""验证 flow 集合查询结果"""
from datetime import datetime
from pymongo import MongoClient

MONGO_HOST = "172.172.5.6"
MONGO_PORT = 27017
MONGO_DB = "radarData"
ROAD_NAME = "K7+197"


def _build_ts_regex(start_time, end_time):
    """时间范围转正则"""
    def _parse(s):
        if isinstance(s, datetime):
            return s
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try: return datetime.strptime(s, fmt)
            except: pass
        return None
    s = _parse(start_time) if start_time else None
    e = _parse(end_time) if end_time else None
    if s and e and s.date() == e.date() and s.hour == e.hour:
        min_s, min_e = s.minute, e.minute
        if min_s == 0 and min_e == 59:
            return f"^{s.strftime('%Y-%m-%d')} {s.hour:02d}:"
        # 生成分钟字符类
        minute_str = "".join(str(i) for i in range(min_s, min_e + 1))
        return f"^{s.strftime('%Y-%m-%d')} {s.hour:02d}:0[{minute_str}]"
    if s and e and s.date() == e.date():
        return f"^{s.strftime('%Y-%m-%d')} "
    if s:
        return f"^{s.strftime('%Y-%m-%d')} "
    return f"^{datetime.now().strftime('%Y-%m-%d')} "


def query_flow(start_time, end_time):
    """查询 flow 集合，只取双向+ALL 记录"""
    client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=30000, connectTimeoutMS=30000, socketTimeoutMS=120000)
    db = client[MONGO_DB]

    regex = _build_ts_regex(start_time, end_time)
    match = {"Timestamp": {"$regex": regex}, "Stats.RoadName": ROAD_NAME}

    cursor = db["flow"].find(match, {"Stats": 1, "Timestamp": 1}).sort("Timestamp", 1)

    total = 0
    for doc in cursor:
        ts = doc.get("Timestamp", "")
        for s in doc.get("Stats", []):
            if s.get("RoadName") == ROAD_NAME and s.get("Direction") == "双向" and s.get("LaneNum") == "ALL":
                tc = s.get("TotalCount", 0)
                if tc > 0:
                    total += tc
    client.close()
    return total


def main():
    user_data = [
        {"t": "00:00", "total": 35},
        {"t": "00:05", "total": 37},
        {"t": "00:10", "total": 33},
        {"t": "00:15", "total": 35},
        {"t": "00:20", "total": 29},
        {"t": "00:25", "total": 32},
    ]

    print("5分钟区间对比:")
    print(f"{'区间':<6s} {'flow':>5s} {'用户':>5s} {'差异':>4s}")
    print("-" * 25)

    for i, u in enumerate(user_data):
        start_min = i * 5
        end_min = start_min + 4
        t1 = datetime(2026, 6, 2, 0, start_min, 0)
        t2 = datetime(2026, 6, 2, 0, end_min, 0)
        total = query_flow(t1, t2)
        print(f"{u['t']:<6s} {total:>5d} {u['total']:>5d} {abs(total-u['total']):>4d}")

    print("\n1小时汇总 (00:00~01:00):")
    t1 = datetime(2026, 6, 2, 0, 0, 0)
    t2 = datetime(2026, 6, 2, 0, 59, 0)
    total = query_flow(t1, t2)
    print(f"  Total: {total}")


if __name__ == "__main__":
    main()
