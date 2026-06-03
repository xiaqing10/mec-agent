#!/usr/bin/env python3
"""验证 MongoDB flowStat 数据：排除双向记录 vs 不排除"""
import json
from datetime import datetime
from pymongo import MongoClient

MONGO_HOST = "172.172.5.6"
MONGO_PORT = 27017
MONGO_DB = "radarData"
ROAD_NAME = "K7+197"


def query_flow(start, end):
    client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000)
    db = client[MONGO_DB]
    match = {"RoadName": ROAD_NAME, "EndTime": {"$gte": start, "$lte": end}}

    raw_count = db["flowStat"].count_documents(match)

    def agg(direction_filter=None):
        pipe = [{"$match": match}, {"$match": {"TotalCount": {"$gt": 0}}}]
        if direction_filter:
            pipe.append({"$match": direction_filter})
        pipe += [
            {"$group": {"_id": "$Direction", "total": {"$sum": "$TotalCount"}, "rec": {"$sum": 1}}},
            {"$sort": {"total": -1}},
        ]
        return list(db["flowStat"].aggregate(pipe))

    all_dirs = agg()
    no_double = agg({"Direction": {"$ne": "双向"}})

    client.close()
    return {
        "time": f"{start.strftime('%H:%M')}~{end.strftime('%H:%M')}",
        "raw_count": raw_count,
        "包含双向": {d["_id"]: d["total"] for d in all_dirs},
        "排除双向": {d["_id"]: d["total"] for d in no_double},
        "total_with": sum(d["total"] for d in all_dirs),
        "total_without": sum(d["total"] for d in no_double),
    }


def main():
    r1 = query_flow(datetime(2026, 6, 2, 0, 0, 0), datetime(2026, 6, 2, 0, 5, 0))
    r2 = query_flow(datetime(2026, 6, 2, 0, 0, 0), datetime(2026, 6, 2, 1, 0, 0))

    print(f"K7+197 流量对比\n{'='*50}")
    for label, r in [("5分钟", r1), ("1小时", r2)]:
        print(f"\n【{label} {r['time']}】 原始记录={r['raw_count']}")
        print(f"  包含双向: {r['包含双向']}  → 合计={r['total_with']}")
        print(f"  排除双向: {r['排除双向']}  → 合计={r['total_without']}")


if __name__ == "__main__":
    main()
