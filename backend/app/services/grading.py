"""精制分级领域服务：按筛号规格分选、碎末比例控制、等级判定，校验上游干燥。

确认分级后，产出按袋入库（一袋一号），供拼配领出与退料流转使用。
"""
from __future__ import annotations
from typing import Any
from app.services.base import BaseModuleService
from app.services.material_stock import material_stock
from app.store import store

class GradingService(BaseModuleService):
    MODULE = "grading"
    ENTITY = "分级结果"
    OPERATION_TERM = "确认分级"
    LIST_FIELDS = ["筛号规格", "碎末比例", "等级判定", "评审人员", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["筛号规格", "碎末比例", "等级判定", "评审人员"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认分级": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    VALID_GRADES = ["特级", "一级", "二级", "三级", "四级", "等外"]

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        grade = values.get("等级判定", "")
        if grade not in self.VALID_GRADES:
            errors.append(f"等级判定须为{', '.join(self.VALID_GRADES)}之一")
        debris = store.parse_weight(values.get("碎末比例", "100"))
        if debris < 0 or debris > 100:
            errors.append("碎末比例格式无效")
        # 上游工序校验
        upstream_module = values.get("上游工序")
        upstream_id = values.get("上游记录ID")
        if upstream_module and upstream_id is not None:
            upstream = store.find(upstream_module, int(upstream_id))
            if upstream is None:
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 不存在")
            elif upstream.get("status") != "已确认":
                errors.append(f"上游记录 {upstream_module}/{upstream_id} 未确认，不能进行分级")
        return errors

    def auto_grade(self, debris_rate: float, sieve: str) -> str:
        """根据碎末率和筛号规格自动判定等级。"""
        if debris_rate <= 2:
            return "特级"
        elif debris_rate <= 5:
            return "一级"
        elif debris_rate <= 10:
            return "二级"
        elif debris_rate <= 20:
            return "三级"
        else:
            return "等外"

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """分级确认后更新批次产出重量，并把产出按袋入库供拼配领出。"""
        if action == "确认分级":
            debris = store.parse_weight(entry.get("碎末比例", "0"))
            input_weight = store.parse_weight(entry.get("上游重量", "0"))
            if input_weight > 0:
                entry["产出重量"] = round(input_weight * (1.0 - debris / 100.0), 3)
            output = store.parse_weight(entry.get("产出重量", "0"))
            if output > 0:
                # 按袋号幂等，重复确认不会重复入袋
                material_stock.register_bag(
                    f"SEAL-G{int(entry['id']):04d}",
                    str(entry.get("批次号", "")),
                    output,
                    grading_id=int(entry["id"]),
                )
        return True, None

    def get_gradings_for_blending(self, batch_no: str) -> list[dict[str, Any]]:
        """获取指定批次可用于拼配的分级结果。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("批次号") == batch_no and r.get("status") == "已确认"]

    def get_by_grade(self, grade: str) -> list[dict[str, Any]]:
        """按等级获取分级结果。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("等级判定") == grade and r.get("status") == "已确认"]