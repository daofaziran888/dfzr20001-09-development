"""原料袋台账与领退流转：维护"一袋一位置"不变量，支撑拼配领出、耗用、退料归仓与重新取料。

业务不变量：
1. 一袋原料任一时刻只有一个有效位置：在库 / 在用 / 待收（已归仓即回到在库）。
2. 只有仍未被消耗的重量可以归仓：申请退回 = 领出 - 已耗用。
3. 新开配方只能领「在库」的袋；在用 / 待收的袋禁止再次领出。
4. 已归仓的重量不会被原配方确认自动扣；确认配方时按真实剩余重新取料。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store

# 袋的合法位置（任一时刻恰好其一）
POSITIONS = ("在库", "在用", "待收")


class MaterialStockService:
    """原料袋 + 拼配领用台账（非模块服务，供拼配配方与配方退料签共用）。"""

    # ── 袋台账 ──

    def register_bag(self, seal: str, batch_no: str, weight: float,
                     grading_id: int | None = None) -> dict[str, Any]:
        """登记一袋原料入库；按袋号幂等，重复登记直接返回原袋。"""
        existing = store.find_bag(seal)
        if existing is not None:
            return existing
        bag = {
            "id": store.next_id(store.bags()),
            "袋号": seal,
            "批次号": batch_no,
            "来源分级ID": grading_id,
            "总量": round(float(weight), 3),
            "在库量": round(float(weight), 3),
            "位置": "在库",
            "占用配方ID": None,
        }
        store.bags().append(bag)
        store.log_event("material_bag", bag["id"], "登记入袋",
                        {"袋号": seal, "总量": bag["总量"], "批次号": batch_no})
        return bag

    def list_bags(self) -> list[dict[str, Any]]:
        """袋看板：位置、在库量与当前占用配方。"""
        result = []
        for bag in store.bags():
            row = dict(bag)
            holder = bag.get("占用配方ID")
            recipe = store.find("blending", int(holder)) if holder is not None else None
            if recipe is not None:
                row["占用配方"] = recipe.get("登记编号", "")
            elif holder is not None:
                row["占用配方"] = f"配方#{holder}"
            else:
                row["占用配方"] = ""
            issue = self.active_issue_for_bag(bag["袋号"])
            row["当前领用ID"] = issue["id"] if issue else None
            result.append(row)
        return result

    def active_issue_for_bag(self, seal: str) -> dict[str, Any] | None:
        """袋当前有效的领用（在用或待收）。"""
        for issue in store.issues():
            if issue.get("袋号") == seal and issue.get("状态") in ("在用", "待收"):
                return issue
        return None

    def issues_for_recipe(self, recipe_id: int) -> list[dict[str, Any]]:
        """配方名下的全部领用（含已归仓与重新取料）。"""
        return [i for i in store.issues() if int(i.get("配方ID", 0)) == int(recipe_id)]

    def remaining(self, issue: dict[str, Any]) -> float:
        """领用剩余可退重量 = 领出 - 已耗用 - 已退回。"""
        left = store.parse_weight(issue.get("领出重量"))
        left -= store.parse_weight(issue.get("已耗用重量"))
        left -= store.parse_weight(issue.get("退回重量"))
        return round(max(left, 0.0), 3)

    # ── 领出（在库 -> 在用）──

    def issue_bag(self, seal: str, recipe_id: int,
                  request_id: str = "") -> tuple[dict[str, Any] | None, str]:
        """拼配领出整袋原料；只有在库的袋可领。"""
        if request_id:
            cached = store.check_idempotent(f"issue:{request_id}")
            if cached:
                return cached.get("issue"), f"幂等: {cached.get('message', '已执行')}"
        bag = store.find_bag(seal)
        if bag is None:
            return None, f"原料袋 {seal} 不存在"
        recipe = store.find("blending", int(recipe_id))
        if recipe is None:
            return None, f"拼配批次 {recipe_id} 不存在"
        if recipe.get("status") in ("已确认", "已撤回"):
            return None, f"拼配批次 {recipe_id} 已{recipe.get('status')}，不能再领料"
        position = bag.get("位置")
        if position == "待收":
            return None, f"原料袋 {seal} 退料待收中，料房尚未实收，新开配方不能领"
        if position == "在用":
            holder = bag.get("占用配方ID")
            if holder is not None and int(holder) == int(recipe_id):
                return None, f"原料袋 {seal} 已由本配方领用，请勿重复领取"
            return None, f"原料袋 {seal} 仍在配方#{holder}实际占用，禁止给另一配方再次领"
        weight = store.parse_weight(bag.get("在库量"))
        if weight <= 0:
            return None, f"原料袋 {seal} 库存为空，不能领出"

        issue = {
            "id": store.next_id(store.issues()),
            "袋号": seal,
            "配方ID": int(recipe_id),
            "批次号": bag.get("批次号", ""),
            "领出重量": weight,
            "已耗用重量": 0.0,
            "退回重量": 0.0,
            "状态": "在用",
            "来源领用ID": None,
            "登记时间": time.time(),
        }
        store.issues().append(issue)
        bag["在库量"] = 0.0
        bag["位置"] = "在用"
        bag["占用配方ID"] = int(recipe_id)
        store.log_event("material_bag", bag["id"], "拼配领出",
                        {"袋号": seal, "配方ID": int(recipe_id),
                         "领出重量": weight, "issue_id": issue["id"]})
        message = f"配方#{recipe_id} 已领出原料袋 {seal}（{weight}kg）"
        if request_id:
            store.record_idempotent(f"issue:{request_id}",
                                    {"issue": dict(issue), "message": message})
        return issue, message

    # ── 耗用登记 ──

    def record_consumption(self, issue_id: int, weight: float,
                           request_id: str = "") -> tuple[dict[str, Any] | None, str]:
        """登记领用的耗用重量；待收期间禁止继续耗用，保住待退的实物。"""
        if request_id:
            cached = store.check_idempotent(f"consume:{request_id}")
            if cached:
                return cached.get("issue"), f"幂等: {cached.get('message', '已执行')}"
        issue = store.find_issue(issue_id)
        if issue is None:
            return None, f"领用记录 {issue_id} 不存在"
        status = issue.get("状态")
        if status == "待收":
            return None, (f"袋 {issue['袋号']} 退料待收中，禁止继续耗用；"
                          f"请先撤回退料签或由料房实收")
        if status == "已归仓":
            return None, f"袋 {issue['袋号']} 的领用已退料归仓，不能再登记耗用"
        weight = round(float(weight), 3)
        if weight <= 0:
            return None, "耗用重量须大于0"
        consumed = store.parse_weight(issue.get("已耗用重量"))
        issued = store.parse_weight(issue.get("领出重量"))
        if consumed + weight > issued + 1e-9:
            return None, (f"耗用超出领出重量：袋 {issue['袋号']} 领出 {issued}kg，"
                          f"已耗用 {consumed}kg，本次 {weight}kg 超出剩余")
        issue["已耗用重量"] = round(consumed + weight, 3)
        bag = store.find_bag(issue["袋号"])
        if bag:
            store.log_event("material_bag", bag["id"], "登记耗用",
                            {"袋号": issue["袋号"], "issue_id": issue["id"],
                             "本次耗用": weight, "累计耗用": issue["已耗用重量"]})
        message = f"袋 {issue['袋号']} 已登记耗用 {weight}kg，累计 {issue['已耗用重量']}kg"
        if request_id:
            store.record_idempotent(f"consume:{request_id}",
                                    {"issue": dict(issue), "message": message})
        return issue, message

    def apply_declared_consumption(self, issue: dict[str, Any],
                                   declared: float) -> tuple[bool, str]:
        """退料签登记时把申报耗用并入台账（只增不减，不改写已登记的真实耗用）。"""
        declared = round(float(declared), 3)
        consumed = store.parse_weight(issue.get("已耗用重量"))
        issued = store.parse_weight(issue.get("领出重量"))
        if declared < 0:
            return False, "已耗用重量不能为负"
        if declared < consumed - 1e-9:
            return False, (f"申报耗用 {declared}kg 少于台账已登记耗用 {consumed}kg，"
                           f"请按真实耗用填写")
        if declared > issued + 1e-9:
            return False, f"申报耗用 {declared}kg 超出领出重量 {issued}kg"
        if declared > consumed:
            issue["已耗用重量"] = declared
            bag = store.find_bag(issue["袋号"])
            if bag:
                store.log_event("material_bag", bag["id"], "登记耗用",
                                {"袋号": issue["袋号"], "issue_id": issue["id"],
                                 "本次耗用": round(declared - consumed, 3),
                                 "累计耗用": declared, "来源": "退料签申报"})
        return True, ""

    # ── 退料流转（在用 -> 待收 -> 在库）──

    def mark_pending_receipt(self, issue: dict[str, Any]) -> None:
        """提交退料：袋 在用 -> 待收（料房已核对封签，袋子仍在原工位）。"""
        bag = store.find_bag(issue["袋号"])
        issue["状态"] = "待收"
        if bag:
            bag["位置"] = "待收"
            store.log_event("material_bag", bag["id"], "登记待收",
                            {"袋号": issue["袋号"], "issue_id": issue["id"],
                             "说明": "料房已核对封签，袋子仍在原工位"})

    def unmark_pending_receipt(self, issue: dict[str, Any]) -> None:
        """撤回待收：袋回到在用。"""
        bag = store.find_bag(issue["袋号"])
        issue["状态"] = "在用"
        if bag and bag.get("位置") == "待收":
            bag["位置"] = "在用"
            store.log_event("material_bag", bag["id"], "撤回待收",
                            {"袋号": issue["袋号"], "issue_id": issue["id"]})

    def receive_return(self, issue: dict[str, Any], actual_weight: float) -> None:
        """实收归仓：袋 待收 -> 在库，实收重量回到可领库存。"""
        bag = store.find_bag(issue["袋号"])
        actual_weight = round(float(actual_weight), 3)
        issue["退回重量"] = actual_weight
        issue["状态"] = "已归仓"
        if bag:
            bag["位置"] = "在库"
            bag["在库量"] = actual_weight
            bag["占用配方ID"] = None
            store.log_event("material_bag", bag["id"], "实收归仓",
                            {"袋号": issue["袋号"], "issue_id": issue["id"],
                             "实收重量": actual_weight})

    # ── 确认配方时按真实剩余重新取料 ──

    def reissue_for_confirm(self, recipe_id: int) -> tuple[bool, str]:
        """确认配方前把已归仓的领用重新取回工位。

        - 退料签仍待收：料房尚未实收，不能确认（未真实退回的料不能当作在手）。
        - 已归仓的领用：从仓库重新领出真实退回的重量，重新办理一次领出；
          已归仓的料不被原确认自动扣，而是按真实剩余重新取料。
        两阶段执行：先全部校验，再统一落账，避免半途状态。
        """
        retakes: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for issue in self.issues_for_recipe(recipe_id):
            status = issue.get("状态")
            if status == "待收":
                return False, (f"袋 {issue['袋号']} 的退料签仍待收，料房尚未实收，"
                               f"不能确认配方")
            if status == "已归仓":
                bag = store.find_bag(issue["袋号"])
                if bag is None:
                    return False, f"袋 {issue['袋号']} 台账缺失，无法重新取料"
                if bag.get("位置") != "在库":
                    holder = bag.get("占用配方ID")
                    return False, (f"袋 {issue['袋号']} 退回后已被配方#{holder}领用，"
                                   f"本配方无法重新取料")
                if store.parse_weight(bag.get("在库量")) <= 0:
                    return False, f"袋 {issue['袋号']} 在库量不足，无法重新取料"
                retakes.append((issue, bag))
        for issue, bag in retakes:
            weight = store.parse_weight(bag["在库量"])
            new_issue = {
                "id": store.next_id(store.issues()),
                "袋号": bag["袋号"],
                "配方ID": int(recipe_id),
                "批次号": bag.get("批次号", ""),
                "领出重量": weight,
                "已耗用重量": 0.0,
                "退回重量": 0.0,
                "状态": "在用",
                "来源领用ID": issue["id"],
                "登记时间": time.time(),
            }
            store.issues().append(new_issue)
            bag["在库量"] = 0.0
            bag["位置"] = "在用"
            bag["占用配方ID"] = int(recipe_id)
            store.log_event("material_bag", bag["id"], "确认重新取料",
                            {"袋号": bag["袋号"], "配方ID": int(recipe_id),
                             "领出重量": weight, "来源领用ID": issue["id"],
                             "issue_id": new_issue["id"]})
        return True, ""

    # ── 两个工作台的办理状态回查 ──

    def bag_status(self, seal: str) -> dict[str, Any] | None:
        """一袋原料的办理状态：拼配侧与料房侧各办成了什么、未办成原因、实际入库重量。"""
        bag = store.find_bag(seal)
        if bag is None:
            return None
        issues = [i for i in store.issues() if i.get("袋号") == seal]
        slips = [s for s in store.rows("material_return") if s.get("袋号") == seal]
        active = self.active_issue_for_bag(seal)
        latest = active or (issues[-1] if issues else None)

        # 拼配侧：领出、耗用、剩余
        blending_side: dict[str, Any] = {"状态": "未领用"}
        if latest is not None:
            recipe = store.find("blending", int(latest["配方ID"]))
            blending_side = {
                "配方ID": latest["配方ID"],
                "配方编号": (recipe or {}).get("登记编号", ""),
                "领出重量": latest.get("领出重量"),
                "已耗用重量": latest.get("已耗用重量"),
                "退回重量": latest.get("退回重量"),
                "剩余可退": self.remaining(latest),
                "状态": latest.get("状态"),
            }

        # 料房侧：退料签与实收重量（待收期间实收为空）
        room_side: dict[str, Any] = {"状态": "无退料签"}
        open_slips = [s for s in slips if s.get("status") in ("草稿", "待收")]
        done_slips = [s for s in slips if s.get("status") == "已归仓"]
        shown = open_slips or done_slips or slips
        if shown:
            slip = shown[-1]
            room_side = {
                "退料签ID": slip.get("id"),
                "登记编号": slip.get("登记编号", ""),
                "状态": slip.get("status"),
                "申请退回重量": slip.get("申请退回重量"),
                "实收重量": slip.get("实收重量") if slip.get("status") == "已归仓" else None,
            }

        # 未办成的一侧给出原因
        reason = ""
        position = bag.get("位置")
        if position == "待收":
            reason = (f"退料签#{room_side.get('退料签ID')} 待收：料房尚未在收货台实收，"
                      f"袋子仍在原工位")
        elif position == "在用" and latest is not None and self.remaining(latest) <= 0:
            reason = (f"实物已经用掉：领出 {latest.get('领出重量')}kg 已全部耗用，"
                      f"无可退重量")
        elif position == "在用":
            reason = "原料在配方工位在用，尚未提交退料"

        # 办理时间线：同一袋的多次办理（领出、耗用、待收、实收、重新取料）
        slip_ids = {int(s["id"]) for s in slips}
        events = [
            e for e in store.get_events()
            if (e["module"] == "material_bag" and int(e["entry_id"]) == int(bag["id"]))
            or (e["module"] == "material_return" and int(e["entry_id"]) in slip_ids)
        ]
        events.sort(key=lambda e: e["ts"])

        return {
            "袋号": seal,
            "批次号": bag.get("批次号", ""),
            "位置": position,
            "总量": bag.get("总量"),
            "在库量": bag.get("在库量"),
            "占用配方ID": bag.get("占用配方ID"),
            "拼配侧": blending_side,
            "料房侧": room_side,
            "未办成原因": reason,
            "领用办理": issues,
            "退料签": slips,
            "办理记录": events,
        }


# 全局单例
material_stock = MaterialStockService()
