"""业务路由汇总：所有模块共用一套资源路由，流水线路由独立注册。"""
from __future__ import annotations

from app.routers import material, resource, pipeline_routes

# material 含 /api/material-bags，与资源路由 /api/{module} 同形，须先注册。
ROUTERS = [material, resource, pipeline_routes]