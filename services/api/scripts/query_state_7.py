# -*- coding: utf-8 -*-
"""查询现有用户 / org / model_instances 数据形态（#7 角色差异化验收前置）。
只读，不修改任何数据。
"""
import json
import sys

from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=3000)
db = client["zy_agents"]

out = {
    "users": list(
        db.users.find({}, {"username": 1, "display_name": 1, "org_unit_id": 1, "status": 1})
    ),
    "org_units": list(db.org_units.find({})),
    "model_instances": list(
        db.model_instances.find({}, {"org_unit_id": 1, "model_name": 1, "display_name": 1, "status": 1})
    ),
    "role_bindings": list(
        db.role_bindings.find({}, {"org_unit_id": 1, "subject_type": 1, "subject_id": 1, "role_code": 1})
    ),
    "knowledge_docs": list(
        db.knowledge_documents.find({}, {"org_unit_id": 1, "title": 1})
    ),
}

for row in out["users"] + out["org_units"] + out["model_instances"] + out["role_bindings"] + out["knowledge_docs"]:
    row["_id"] = str(row["_id"])
    if "org_unit_id" in row:
        row["org_unit_id"] = str(row["org_unit_id"])

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
