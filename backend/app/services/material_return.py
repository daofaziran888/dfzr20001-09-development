"""配方退料签领域服务：拼配领出后，仍未被消耗的原料凭退料签退回仓库。

流转：草稿 --提交退料--> 待收（料房核对封签，袋仍在原工位） --实收归仓--> 已归仓。
退料签对应一个真实领用及该袋实际状态；只有仍未被消耗的重量可以归仓；
重交退料、重复实收、重复确认均不多记。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.material_stock import material_stock
from app.store import store


class MaterialReturnService(BaseModuleService):
    MODULE = "material_return"
    ENTITY = "退料签"
    OPERATION_TERM = "实收归仓"
    LIST_FIELDS = ["袋号", "领出重量", "已耗用重量", "申请退回重量", "实收重量",
                   "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["袋号", "已耗用重量"]
    STATUS_ORDER = ["草稿", "待收", "已归仓", "已撤回"]
    ACTION_RULES = {"提交退料": "待收", "实收归仓": "已归仓", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    # ── 登记退料签 ──

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        seal = str(values.get("袋号")).strip()
        bag = store.find_bag(seal)
        if bag is None:
            return None, [f"原料袋 {seal} 不存在"]
        # 退料签必须对应一个真实领用及该袋实际状态
        issue = material_stock.active_issue_for_bag(seal)
        if issue is None:
            if bag.get("位置") == "在库":
                return None, [f"原料袋 {seal} 在库中，没有可退的拼配领用"]
            return None, [f"原料袋 {seal} 没有有效的拼配领用，退料签须对应一个真实领用"]
        if issue.get("状态") != "在用":
            return None, [f"袋 {seal} 的领用已是「{issue.get('状态')}」，不能重复提交退料"]
        # 同一领用只允许一张活动退料签：重交退料不多记
        for row in store.rows(self.MODULE):
            if (row.get("issue_id") == issue["id"]
                    and row.get("status") in ("草稿", "待收")):
                return None, [
                    f"袋 {seal} 已存在退料签#{row['id']}（{row['status']}），重交退料不多记"
                ]

        # 申报耗用并入台账（只增不减），只有仍未被消耗的重量可以归仓
        declared = store.parse_weight(values.get("已耗用重量"))
        ok, msg = material_stock.apply_declared_consumption(issue, declared)
        if not ok:
            return None, [msg]
        remaining = material_stock.remaining(issue)
        if remaining <= 0:
            return None, [
                f"袋 {seal} 实物已经用掉（领出 {issue['领出重量']}kg 已全部耗用），无可退重量"
            ]

        values["袋号"] = seal
        values["领出重量"] = issue["领出重量"]
        values["已耗用重量"] = issue["已耗用重量"]
        values["申请退回重量"] = remaining
        values["实收重量"] = ""
        values["issue_id"] = issue["id"]
        values["配方ID"] = issue["配方ID"]
        if not str(values.get("登记编号") or "").strip():
            values["登记编号"] = f"RET-{seal}-{int(issue['id']):03d}"
        return super().create_entry(values)

    # ── 流转钩子 ──

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        if action == "提交退料":
            if entry.get("status") != "草稿":
                return False, "只有草稿退料签可以提交退料"
            issue = store.find_issue(int(entry.get("issue_id", 0)))
            if issue is None or issue.get("状态") != "在用":
                return False, "原领用状态已变化，不能登记待收"
            # 登记待收前复核实物：签后若又被耗用，剩余须仍覆盖申请量
            remaining = material_stock.remaining(issue)
            want = store.parse_weight(entry.get("申请退回重量"))
            if remaining <= 0:
                return False, f"袋 {entry.get('袋号')} 实物已经用掉，无可退重量，请撤回退料签"
            if remaining < want:
                return False, (f"袋 {entry.get('袋号')} 剩余 {remaining}kg 少于申请退回 "
                               f"{want}kg，请撤回后按真实剩余重新登记")
            material_stock.mark_pending_receipt(issue)
            return True, None
        if action == "实收归仓":
            if entry.get("status") != "待收":
                return False, "退料签须先提交退料、登记待收后，才能在收货台实收"
            issue = store.find_issue(int(entry.get("issue_id", 0)))
            if issue is None or issue.get("状态") != "待收":
                return False, "原领用不在待收状态，不能实收"
            want = store.parse_weight(entry.get("申请退回重量"))
            actual = store.parse_weight(entry.get("实收重量")) or want
            if actual <= 0:
                return False, "实收重量须大于0"
            if actual > want + 1e-9:
                return False, f"实收重量 {actual}kg 不能超过申请退回重量 {want}kg"
            entry["实收重量"] = actual
            material_stock.receive_return(issue, actual)
            return True, None
        return True, None

    def on_withdraw(self, entry: dict[str, Any], action: str) -> bool:
        status = entry.get("status")
        if status == "已归仓":
            return False  # 已归仓的料不能再撤回
        if status == "待收":
            issue = store.find_issue(int(entry.get("issue_id", 0)))
            if issue is not None and issue.get("状态") == "待收":
                material_stock.unmark_pending_receipt(issue)
        return True

    # ── 收货台实收 ──

    def receive(self, entry_id: int,
                actual_weight: float | None = None) -> tuple[dict[str, Any] | None, str]:
        """料房在收货台办实收：可带实收重量；重复实收不多记。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"退料签 {entry_id} 不存在"
        if entry.get("status") == "已归仓":
            return entry, (f"退料签 {entry_id} 已实收归仓"
                           f"（实收 {entry.get('实收重量')}kg），重复实收不多记")
        if actual_weight is not None:
            entry["实收重量"] = round(float(actual_weight), 3)
        return self.run_action(entry_id, "实收归仓")
