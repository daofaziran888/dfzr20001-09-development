"""配方退料签业务测试：拼配领出 -> 耗用 -> 提交退料 -> 待收 -> 实收归仓 -> 重新取料。

覆盖需求场景：一袋 8 公斤领出、用掉 5 公斤、剩 3 公斤退料归仓，
旧配方确认时按真实剩余重新取料；同一袋原料多次办理全程可查。
"""
from __future__ import annotations

import unittest

from app.store import store
from app.services import service_for
from app.services.material_stock import material_stock


def _new_recipe(tag: str) -> dict:
    """登记一张拼配草稿。"""
    blending = service_for("blending")
    entry, errors = blending.create_entry({
        "原料比例": "60:40",
        "拼配日期": "2026-11-27",
        "口感评分": "88",
        "香气描述": f"退料测试-{tag}",
    })
    assert entry is not None, errors
    return entry


def _new_bag(seal: str, weight: float) -> dict:
    return material_stock.register_bag(seal, "BATCH-RETURN-TEST", weight)


class TestMaterialReturnFlow(unittest.TestCase):
    """完整退料流转：8kg 领出、耗用 5kg、退 3kg、实收归仓、旧配方确认重新取料。"""

    def setUp(self):
        self.blending = service_for("blending")
        self.returns = service_for("material_return")
        self.recipe = _new_recipe("flow")
        self.bag = _new_bag("SEAL-T-FLOW", 8.0)
        self.rid = int(self.recipe["id"])

    def test_issue_consume_return_receive_reissue_chain(self):
        # 1. 拼配领出一袋 8 公斤
        issue, msg = self.blending.issue_material(self.rid, "SEAL-T-FLOW")
        self.assertIsNotNone(issue, msg)
        self.assertEqual(issue["领出重量"], 8.0)
        self.assertEqual(self.bag["位置"], "在用")
        self.assertEqual(self.bag["在库量"], 0.0)
        self.assertEqual(self.bag["占用配方ID"], self.rid)

        # 2. 用掉 5 公斤
        issue, msg = self.blending.record_usage(self.rid, "SEAL-T-FLOW", 5.0)
        self.assertIsNotNone(issue, msg)
        self.assertEqual(issue["已耗用重量"], 5.0)

        # 3. 把剩下 3 公斤提交退料
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-FLOW", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        self.assertEqual(slip["领出重量"], 8.0)
        self.assertEqual(slip["申请退回重量"], 3.0)
        self.assertEqual(slip["status"], "草稿")

        # 重交退料不多记
        dup, dup_errors = self.returns.create_entry(
            {"袋号": "SEAL-T-FLOW", "已耗用重量": "5"})
        self.assertIsNone(dup)
        self.assertTrue(any("重交退料不多记" in e for e in dup_errors))

        # 4. 料房核对封签、登记待收；袋子还在原工位
        slip, msg = self.returns.run_action(int(slip["id"]), "提交退料")
        self.assertIsNotNone(slip, msg)
        self.assertEqual(slip["status"], "待收")
        self.assertEqual(self.bag["位置"], "待收")

        # 待收期间：另一配方不能领（未真实退回）
        other = _new_recipe("other")
        issue2, msg2 = self.blending.issue_material(int(other["id"]), "SEAL-T-FLOW")
        self.assertIsNone(issue2)
        self.assertIn("待收", msg2)

        # 待收期间：旧配方也不能确认（退料未实收）
        blocked, msg3 = self.blending.run_action(self.rid, "确认配方")
        self.assertIsNone(blocked)
        self.assertIn("待收", msg3)

        # 5. 料房在收货台办实收，实际入库 3 公斤
        slip, msg = self.returns.receive(int(slip["id"]), 3.0)
        self.assertIsNotNone(slip, msg)
        self.assertEqual(slip["status"], "已归仓")
        self.assertEqual(slip["实收重量"], 3.0)
        self.assertEqual(self.bag["位置"], "在库")
        self.assertEqual(self.bag["在库量"], 3.0)
        self.assertIsNone(self.bag["占用配方ID"])

        # 重复实收不多记
        again, msg4 = self.returns.receive(int(slip["id"]), 3.0)
        self.assertIsNotNone(again)
        self.assertIn("不多记", msg4)
        self.assertEqual(self.bag["在库量"], 3.0)

        # 6. 打开旧配方确认：按真实剩余重新取料 3 公斤
        confirmed, msg5 = self.blending.run_action(self.rid, "确认配方")
        self.assertIsNotNone(confirmed, msg5)
        self.assertEqual(confirmed["status"], "已确认")
        self.assertEqual(self.bag["位置"], "在用")
        self.assertEqual(self.bag["在库量"], 0.0)

        # 同一袋原料两次办理：原领用已归仓 + 重新取料在用
        issues = [i for i in store.issues() if i["袋号"] == "SEAL-T-FLOW"]
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["状态"], "已归仓")
        self.assertEqual(issues[0]["退回重量"], 3.0)
        self.assertEqual(issues[1]["状态"], "在用")
        self.assertEqual(issues[1]["领出重量"], 3.0)
        self.assertEqual(issues[1]["来源领用ID"], issues[0]["id"])

        # 已归仓的料没有被原确认自动扣：耗用仍是 5，退回仍是 3
        self.assertEqual(issues[0]["已耗用重量"], 5.0)

        # 7. 已确认配方回查实际使用来源和用量
        usage = self.blending.usage_trace(self.rid)
        self.assertEqual(len(usage), 2)
        total_used = sum(store.parse_weight(u["已耗用重量"]) for u in usage)
        self.assertEqual(total_used, 5.0)
        sources = {u["来源"] for u in usage}
        self.assertEqual(sources, {"原始领用", "重新取料"})

        # 重复确认不多记
        again2, msg6 = self.blending.run_action(self.rid, "确认配方")
        self.assertIsNone(again2)
        self.assertIn("已确认", msg6)
        self.assertEqual(len([i for i in store.issues() if i["袋号"] == "SEAL-T-FLOW"]), 2)


class TestReturnGuards(unittest.TestCase):
    """退料与领料的位置守卫：一袋一个有效位置。"""

    def setUp(self):
        self.blending = service_for("blending")
        self.returns = service_for("material_return")

    def test_fully_consumed_bag_cannot_return(self):
        """实物已经用掉的袋不能退料。"""
        recipe = _new_recipe("used-up")
        _new_bag("SEAL-T-USED", 8.0)
        rid = int(recipe["id"])
        issue, msg = self.blending.issue_material(rid, "SEAL-T-USED")
        self.assertIsNotNone(issue, msg)
        issue, msg = self.blending.record_usage(rid, "SEAL-T-USED", 8.0)
        self.assertIsNotNone(issue, msg)
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-USED", "已耗用重量": "8"})
        self.assertIsNone(slip)
        self.assertTrue(any("实物已经用掉" in e for e in errors))

    def test_bag_in_use_blocked_for_other_recipe(self):
        """仍在原配方实际占用的袋，禁止给另一配方再次领。"""
        r1 = _new_recipe("holder")
        r2 = _new_recipe("intruder")
        _new_bag("SEAL-T-HOLD", 6.0)
        issue, msg = self.blending.issue_material(int(r1["id"]), "SEAL-T-HOLD")
        self.assertIsNotNone(issue, msg)
        issue2, msg2 = self.blending.issue_material(int(r2["id"]), "SEAL-T-HOLD")
        self.assertIsNone(issue2)
        self.assertIn("实际占用", msg2)

    def test_other_recipe_takes_only_after_receipt(self):
        """新开配方不能在未真实退回时领；实收后另一配方可取这 3 公斤。"""
        r1 = _new_recipe("first")
        r2 = _new_recipe("second")
        bag = _new_bag("SEAL-T-HAND", 8.0)
        rid1 = int(r1["id"])
        self.blending.issue_material(rid1, "SEAL-T-HAND")
        self.blending.record_usage(rid1, "SEAL-T-HAND", 5.0)
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-HAND", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        self.returns.run_action(int(slip["id"]), "提交退料")

        # 未真实退回前另一配方不能领
        blocked, bmsg = self.blending.issue_material(int(r2["id"]), "SEAL-T-HAND")
        self.assertIsNone(blocked)
        self.assertIn("待收", bmsg)

        # 实收 3 公斤后另一配方取料成功
        self.returns.receive(int(slip["id"]), 3.0)
        issue2, msg2 = self.blending.issue_material(int(r2["id"]), "SEAL-T-HAND")
        self.assertIsNotNone(issue2, msg2)
        self.assertEqual(issue2["领出重量"], 3.0)
        self.assertEqual(bag["位置"], "在用")
        self.assertEqual(bag["占用配方ID"], int(r2["id"]))

    def test_withdraw_pending_return_restores_in_use(self):
        """待收撤回后袋回到在用，可重新登记退料。"""
        recipe = _new_recipe("withdraw")
        bag = _new_bag("SEAL-T-WD", 8.0)
        rid = int(recipe["id"])
        self.blending.issue_material(rid, "SEAL-T-WD")
        self.blending.record_usage(rid, "SEAL-T-WD", 5.0)
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-WD", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        self.returns.run_action(int(slip["id"]), "提交退料")
        self.assertEqual(bag["位置"], "待收")

        slip, msg = self.returns.run_action(int(slip["id"]), "撤回记录")
        self.assertIsNotNone(slip, msg)
        self.assertEqual(slip["status"], "已撤回")
        self.assertEqual(bag["位置"], "在用")

        # 撤回后可重新登记退料
        slip2, errors2 = self.returns.create_entry(
            {"袋号": "SEAL-T-WD", "已耗用重量": "5"})
        self.assertIsNotNone(slip2, errors2)
        self.assertEqual(slip2["申请退回重量"], 3.0)

    def test_consume_blocked_while_pending_receipt(self):
        """待收期间禁止继续耗用，保住待退的实物。"""
        recipe = _new_recipe("pending-consume")
        _new_bag("SEAL-T-PC", 8.0)
        rid = int(recipe["id"])
        self.blending.issue_material(rid, "SEAL-T-PC")
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-PC", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        self.returns.run_action(int(slip["id"]), "提交退料")
        issue, msg = self.blending.record_usage(rid, "SEAL-T-PC", 1.0)
        self.assertIsNone(issue)
        self.assertIn("待收", msg)

    def test_receive_weight_cannot_exceed_request(self):
        """实收重量不能超过申请退回重量。"""
        recipe = _new_recipe("over-receive")
        _new_bag("SEAL-T-OR", 8.0)
        rid = int(recipe["id"])
        self.blending.issue_material(rid, "SEAL-T-OR")
        slip, errors = self.returns.create_entry(
            {"袋号": "SEAL-T-OR", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        self.returns.run_action(int(slip["id"]), "提交退料")
        entry, msg = self.returns.receive(int(slip["id"]), 5.0)
        self.assertIsNone(entry)
        self.assertIn("不能超过申请退回重量", msg)


class TestBagStatusInquiry(unittest.TestCase):
    """两个工作台的办理状态回查。"""

    def test_status_shows_both_sides_and_actual_weight(self):
        """待收期间回查两边分别办成什么；实收后回查实际入库重量。"""
        blending = service_for("blending")
        returns = service_for("material_return")
        recipe = _new_recipe("status")
        _new_bag("SEAL-T-ST", 8.0)
        rid = int(recipe["id"])
        blending.issue_material(rid, "SEAL-T-ST")
        blending.record_usage(rid, "SEAL-T-ST", 5.0)
        slip, errors = returns.create_entry({"袋号": "SEAL-T-ST", "已耗用重量": "5"})
        self.assertIsNotNone(slip, errors)
        returns.run_action(int(slip["id"]), "提交退料")

        # 待收期间：拼配侧已办领出/耗用/退料申请，料房侧待收、实收为空
        status = material_stock.bag_status("SEAL-T-ST")
        self.assertEqual(status["位置"], "待收")
        self.assertEqual(status["拼配侧"]["领出重量"], 8.0)
        self.assertEqual(status["拼配侧"]["已耗用重量"], 5.0)
        self.assertEqual(status["拼配侧"]["剩余可退"], 3.0)
        self.assertEqual(status["料房侧"]["状态"], "待收")
        self.assertIsNone(status["料房侧"]["实收重量"])
        self.assertIn("待收", status["未办成原因"])

        # 实收后：实际入库重量 3kg，位置回到在库
        returns.receive(int(slip["id"]), 3.0)
        status = material_stock.bag_status("SEAL-T-ST")
        self.assertEqual(status["位置"], "在库")
        self.assertEqual(status["料房侧"]["实收重量"], 3.0)
        self.assertEqual(status["在库量"], 3.0)

        # 办理记录包含同一袋的两次办理：领出与归仓
        actions = [e["action"] for e in status["办理记录"]]
        self.assertIn("拼配领出", actions)
        self.assertIn("实收归仓", actions)

    def test_consumed_up_bag_status_reason(self):
        """实物已经用掉的袋，回查给出原因。"""
        blending = service_for("blending")
        recipe = _new_recipe("reason")
        _new_bag("SEAL-T-RS", 4.0)
        rid = int(recipe["id"])
        blending.issue_material(rid, "SEAL-T-RS")
        blending.record_usage(rid, "SEAL-T-RS", 4.0)
        status = material_stock.bag_status("SEAL-T-RS")
        self.assertIn("实物已经用掉", status["未办成原因"])


class TestGradingRegistersBag(unittest.TestCase):
    """精制分级确认后原料自动入袋，供拼配领出与退料流转。"""

    def test_grading_confirm_creates_bag(self):
        grading = service_for("grading")
        entry, errors = grading.create_entry({
            "筛号规格": "6号筛",
            "碎末比例": "4",
            "等级判定": "一级",
            "评审人员": "测试评审",
            "上游重量": "10",
        })
        self.assertIsNotNone(entry, errors)
        confirmed, msg = grading.run_action(int(entry["id"]), "确认分级")
        self.assertIsNotNone(confirmed, msg)
        seal = f"SEAL-G{int(entry['id']):04d}"
        bag = store.find_bag(seal)
        self.assertIsNotNone(bag)
        self.assertEqual(bag["位置"], "在库")
        self.assertEqual(bag["在库量"], 9.6)  # 10kg * (1 - 4%)
        self.assertEqual(bag["来源分级ID"], int(entry["id"]))

        # 重复确认不重复入袋
        count = len([b for b in store.bags() if b["袋号"] == seal])
        grading.run_action(int(entry["id"]), "确认分级")
        self.assertEqual(len([b for b in store.bags() if b["袋号"] == seal]), count)


if __name__ == "__main__":
    unittest.main()
