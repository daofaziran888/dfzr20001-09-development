"""拼配配方领域服务：管理拼配配方，校验原料比例、记录口感评分与香气，关联多批分级结果。

原料领退：配方可领出整袋原料、登记耗用；确认配方时按真实剩余重新取料，
已归仓的料不被原确认自动扣，仍在原配方实际占用的袋禁止给另一配方再次领。
"""
from __future__ import annotations
from typing import Any
from app.services.base import BaseModuleService
from app.services.material_stock import material_stock
from app.store import store

class BlendingService(BaseModuleService):
    MODULE = "blending"
    ENTITY = "拼配批次"
    OPERATION_TERM = "确认配方"
    LIST_FIELDS = ["原料比例", "拼配日期", "口感评分", "香气描述", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["原料比例", "拼配日期"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认配方": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        ratio = values.get("原料比例", "")
        score = store.parse_weight(values.get("口感评分", "0"))
        if score < 0 or score > 100:
            errors.append("口感评分应在0-100之间")
        if ratio:
            self._check_ratio_sum(ratio, errors)
        # 校验关联的分级结果
        grading_ids = values.get("grading_ids")
        if grading_ids:
            try:
                ids = [int(x.strip()) for x in str(grading_ids).split(",") if x.strip()]
                for gid in ids:
                    g = store.find("grading", gid)
                    if g is None:
                        errors.append(f"分级结果 {gid} 不存在")
                    elif g.get("status") != "已确认":
                        errors.append(f"分级结果 {gid} 未确认，不能用于拼配")
            except (ValueError, TypeError):
                errors.append("grading_ids 格式应为逗号分隔的数字列表")
        return errors

    def _check_ratio_sum(self, ratio_str: str, errors: list[str]) -> None:
        """校验原料比例之和是否为100。"""
        try:
            parts = ratio_str.replace("：", ":").split(":")
            weights = [store.parse_weight(p) for p in parts if store.parse_weight(p) > 0]
            if weights and abs(sum(weights) - 100) > 5:
                errors.append("原料比例之和应接近100%")
        except Exception:
            pass

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """确认拼配时检查上游分级是否已确认，并按真实剩余重新取料。"""
        if action == "确认配方":
            batch_no = entry.get("批次号", "")
            if batch_no:
                lineage = store.get_batch_lineage_nodes(batch_no)
                for n in lineage:
                    if n["module"] == "grading":
                        grade_entry = store.find("grading", n["entry_id"])
                        if grade_entry and grade_entry.get("status") != "已确认":
                            return False, "该批次的精制分级尚未确认，请先确认分级后配方才能生效"
            # 回仓后草稿仍关联原领用：确认时按真实剩余重新取料；
            # 已归仓的料不被原确认自动扣，待收的退料签未实收前不能确认。
            ok, msg = material_stock.reissue_for_confirm(int(entry["id"]))
            if not ok:
                return False, msg
        return True, None

    # ── 原料领用 / 耗用 / 用量回查 ──

    def issue_material(self, entry_id: int, seal: str,
                       request_id: str = "") -> tuple[dict[str, Any] | None, str]:
        """拼配领出整袋原料（只有在库的袋可领）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"拼配批次 {entry_id} 不存在"
        return material_stock.issue_bag(seal, entry_id, request_id=request_id)

    def record_usage(self, entry_id: int, seal: str, weight: float,
                     request_id: str = "") -> tuple[dict[str, Any] | None, str]:
        """登记配方对某袋原料的耗用。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"拼配批次 {entry_id} 不存在"
        issue = material_stock.active_issue_for_bag(seal)
        if issue is None:
            return None, f"袋 {seal} 当前没有在用领用"
        if int(issue["配方ID"]) != int(entry_id):
            return None, f"袋 {seal} 被配方#{issue['配方ID']}占用，不属于配方#{entry_id}"
        return material_stock.record_consumption(int(issue["id"]), weight,
                                                 request_id=request_id)

    def usage_trace(self, entry_id: int) -> list[dict[str, Any]]:
        """已确认配方回查实际使用来源和用量（含退料签与重新取料记录）。"""
        result = []
        for issue in material_stock.issues_for_recipe(entry_id):
            slips = [s for s in store.rows("material_return")
                     if int(s.get("issue_id", 0) or 0) == int(issue["id"])]
            result.append({
                **dict(issue),
                "来源": "重新取料" if issue.get("来源领用ID") else "原始领用",
                "剩余可退": material_stock.remaining(issue),
                "退料签": slips,
            })
        return result

    def get_formula_by_batch(self, batch_no: str) -> dict[str, Any] | None:
        """获取指定批次的拼配配方。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("批次号") == batch_no and r.get("status") == "已确认":
                return r
        return None

    def list_grading_sources(self, entry_id: int) -> list[dict[str, Any]]:
        """列出拼配配方引用的分级结果。"""
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return []
        grading_ids = row.get("grading_ids", "")
        result = []
        try:
            ids = [int(x.strip()) for x in str(grading_ids).split(",") if x.strip()]
            for gid in ids:
                g = store.find("grading", gid)
                if g:
                    result.append(g)
        except (ValueError, TypeError):
            pass
        return result