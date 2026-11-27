"""拼配领料与退料流转路由：领出、耗用、收货台实收、袋状态两工作台回查。

注意：/api/material-bags 与资源路由 /api/{module} 同形，本路由须在资源路由之前注册。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.material_stock import material_stock

router = APIRouter(prefix="/api", tags=["原料领退流转"])


class IssuePayload(BaseModel):
    袋号: str
    request_id: str = ""


class ConsumePayload(BaseModel):
    袋号: str
    耗用重量: float = Field(gt=0)
    request_id: str = ""


class ReceivePayload(BaseModel):
    实收重量: float | None = None


@router.get("/material-bags")
def list_material_bags() -> dict[str, Any]:
    """原料袋看板：位置、在库量、占用配方（一袋一个有效位置）。"""
    return {"ok": True, "items": material_stock.list_bags()}


@router.get("/material-bags/{seal}/status")
def material_bag_status(seal: str) -> dict[str, Any]:
    """一袋原料的办理状态：两个工作台各办成了什么、未办成原因、实际入库重量。"""
    status = material_stock.bag_status(seal)
    if status is None:
        raise HTTPException(status_code=404, detail=f"原料袋 {seal} 不存在")
    return {"ok": True, "status": status}


@router.post("/blending/{entry_id}/issue")
def issue_material(entry_id: int, payload: IssuePayload) -> dict[str, Any]:
    """拼配领出整袋原料（在库 -> 在用）。"""
    service = service_for("blending")
    issue, message = service.issue_material(entry_id, payload.袋号.strip(),
                                            request_id=payload.request_id)
    if issue is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "issue": issue}


@router.post("/blending/{entry_id}/consume")
def consume_material(entry_id: int, payload: ConsumePayload) -> dict[str, Any]:
    """登记配方对某袋原料的耗用。"""
    service = service_for("blending")
    issue, message = service.record_usage(entry_id, payload.袋号.strip(),
                                          payload.耗用重量,
                                          request_id=payload.request_id)
    if issue is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "issue": issue}


@router.get("/blending/{entry_id}/usage")
def usage_trace(entry_id: int) -> dict[str, Any]:
    """配方实际使用来源和用量回查（含退料签与重新取料记录）。"""
    service = service_for("blending")
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"拼配批次 {entry_id} 不存在")
    return {"ok": True, "items": service.usage_trace(entry_id)}


@router.post("/material_return/{entry_id}/receive")
def receive_return(entry_id: int, payload: ReceivePayload) -> dict[str, Any]:
    """料房收货台实收：登记实收重量并归仓；重复实收不多记。"""
    service = service_for("material_return")
    entry, message = service.receive(entry_id, payload.实收重量)
    if entry is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": entry}
