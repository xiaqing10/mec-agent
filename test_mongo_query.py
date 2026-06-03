#!/usr/bin/env python3
"""验证 MongoDB flowStat 数据结构和重复情况"""
import json
from datetime import datetime
from pymongo import MongoClient

MONGO_HOST = "172.172.5.6"
MONGO_PORT = 27017
MONGO_DB = "radarData"
ROAD_NAME = "K7+197"


def query_flow(start, end):
    """查询指定时间段的 flowStat 数据"""
    client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000)
    db = client[MONGO_DB]

    match = {"RoadName": ROAD_NAME, "EndTime": {"$gte": start, "$lte": end}}

    # 1) 原始记录数
    raw_count = db["flowStat"].count_documents(match)

    # 2) 按 (EndTime, Direction, LaneNum) 分组检查重复
    dup_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"EndTime": "$EndTime", "Direction": "$Direction", "LaneNum": "$LaneNum"},
            "count": {"$sum": 1},
            "totalSum": {"$sum": "$TotalCount"},
        }},
        {"$sort": {"_id.EndTime": 1, "_id.Direction": 1, "_id.LaneNum": 1}},
    ]
    dup_results = list(db["flowStat"].aggregate(dup_pipeline))

    # 3) 按方向汇总 - 不排除双向（对比用）
    agg_all = [
        {"$match": match},
        {"$match": {"TotalCount": {"$gt": 0}}},
        {"$group": {
            "_id": {"Direction": "$Direction"},
            "totalVehicles": {"$sum": "$TotalCount"},
            "records": {"$sum": 1},
        }},
        {"$sort": {"totalVehicles": -1}},
    ]
    dir_all = list(db["flowStat"].aggregate(agg_all))

    # 4) 按方向汇总 - 排除双向（正确方式）
    agg_no_double = [
        {"$match": match},
        {"$match": {"TotalCount": {"$gt": 0}}},
        {"$match": {"Direction": {"$ne": "双向"}}},
        {"$group": {
            "_id": {"Direction": "$Direction"},
            "totalVehicles": {"$sum": "$TotalCount"},
            "records": {"$sum": 1},
        }},
        {"$sort": {"totalVehicles": -1}},
    ]
    dir_no_double = list(db["flowStat"].aggregate(agg_no_double))

    # 5) 抽样原始记录
    sample_pipeline = [
        {"$match": match},
        {"$sort": {"EndTime": 1, "Direction": 1, "LaneNum": 1}},
        {"$limit": 20},
        {"$project": {"_id": 0, "EndTime": 1, "Direction": 1, "LaneNum": 1, "TotalCount": 1}},
    ]
    samples = list(db["flowStat"].aggregate(sample_pipeline))

    client.close()

    # 计算对比
    total_with_double = sum(d["totalVehicles"] for d in dir_all)
    total_no_double = sum(d["totalVehicles"] for d in dir_no_double)

    return {
        "road": ROAD_NAME,
        "time_range": [start.isoformat(), end.isoformat()],
        "raw_count": raw_count,
        "dup_check": [{"group": d["_id"], "count": d["count"], "totalSum": d["totalSum"]} for d in dup_results],
        "summary_with_double": [{"direction": d["_id"]["Direction"], "total": d["totalVehicles"], "records": d["records"]} for d in dir_all],
        "summary_no_double": [{"direction": d["_id"]["Direction"], "total": d["totalVehicles"], "records": d["records"]} for d in dir_no_double],
        "total_with_double": total_with_double,
        "total_no_double": total_no_double,
        "samples": samples,
    }


def main():
    results = {}

    print("查询 2026-06-02 00:00:00 ~ 00:05:00 ...")
    t1 = datetime(2026, 6, 2, 0, 0, 0)
    t2 = datetime(2026, 6, 2, 0, 5, 0)
    results["5min"] = query_flow(t1, t2)

    print("查询 2026-06-02 00:00:00 ~ 01:00:00 ...")
    t3 = datetime(2026, 6, 2, 0, 0, 0)
    t4 = datetime(2026, 6, 2, 1, 0, 0)
    results["1hour"] = query_flow(t3, t4)

    def convert(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, '__class__') and obj.__class__.__name__ == 'ObjectId':
            return str(obj)
        return str(obj)

    print("\n" + "=" * 60)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=convert))


if __name__ == "__main__":
    main()
