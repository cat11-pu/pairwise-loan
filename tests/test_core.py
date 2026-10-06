"""分期还款计划内核的行为测试。

期望值全部写成结果本身：金额（分）、逐期明细与必须报错的调用。
在项目根目录执行：

    python3 -m unittest discover -s tests -v
"""

import unittest

from loan.core import (
    METHOD_ANNUITY,
    METHOD_PRINCIPAL,
    LoanError,
    Plan,
    annuity_payment,
    interest_for,
    overdue_penalty,
    round_half_up,
)


class KernelCase(unittest.TestCase):
    """共用小工具：把不该出现的异常也转成断言失败。"""

    def value(self, label, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - 内核抛错本身就说明行为不对
            self.fail("%s 抛出了 %s: %s" % (label, type(error).__name__, error))

    def refuses(self, error_type, label, function, *args, **kwargs):
        try:
            function(*args, **kwargs)
        except error_type:
            return
        except Exception as error:  # noqa: BLE001 - 报错类型不对同样算行为不对
            self.fail("%s 抛出了 %s: %s，期望 %s" % (label, type(error).__name__, error, error_type.__name__))
        self.fail("%s 没有报错，期望 %s" % (label, error_type.__name__))


class RoundingTest(KernelCase):
    """分位舍入与利息计算。"""

    def test_01_fractions_round_half_up(self):
        self.assertEqual(round_half_up(0, 7), 0)
        self.assertEqual(round_half_up(1, 2), 1)
        self.assertEqual(round_half_up(3, 2), 2)
        self.assertEqual(round_half_up(5, 4), 1)
        self.assertEqual(round_half_up(7, 4), 2)
        self.assertEqual(round_half_up(1, 3), 0)
        self.assertEqual(round_half_up(2, 3), 1)
        self.assertEqual(round_half_up(499999, 1000000), 0)
        self.assertEqual(round_half_up(500000, 1000000), 1)
        self.assertEqual(round_half_up(1000000, 1000000), 1)
        self.assertEqual(round_half_up(1500001, 1000000), 2)
        self.assertEqual(interest_for(100000, 10000), 1000)
        self.assertEqual(interest_for(120005, 4500), 540)
        self.assertEqual(interest_for(2499, 200), 0)
        self.assertEqual(interest_for(2500, 200), 1)
        self.assertEqual(interest_for(0, 10000), 0)


class AnnuityTest(KernelCase):
    """等额本息：每期还款额与逐期拆分。"""

    def test_02_annuity_payment_and_level_installments(self):
        self.assertEqual(annuity_payment(100000, 12, 10000), 8885)
        self.assertEqual(annuity_payment(120000, 24, 5000), 5318)
        self.assertEqual(annuity_payment(50000, 6, 10000), 8627)
        self.assertEqual(annuity_payment(200000, 24, 10000), 9415)
        self.assertEqual(annuity_payment(100000, 1, 10000), 101000)
        self.assertEqual(annuity_payment(100000, 12, 0), 8333)
        self.assertEqual(annuity_payment(10000, 5, 0), 2000)

        plan = self.value("等额本息计划", Plan, 100000, 12, 10000)
        payments = plan.payments()
        self.assertEqual(payments[:11], (8885,) * 11)
        self.assertEqual(payments[11], 8884)
        self.assertEqual(
            [item.interest for item in plan.installments],
            [1000, 921, 842, 761, 680, 598, 515, 431, 347, 261, 175, 88],
        )
        principals = [item.principal for item in plan.installments]
        self.assertEqual(
            principals,
            [7885, 7964, 8043, 8124, 8205, 8287, 8370, 8454, 8538, 8624, 8710, 8796],
        )
        for earlier, later in zip(principals, principals[1:]):
            self.assertTrue(later >= earlier)
        self.assertEqual(plan.installments[0].opening_balance, 100000)
        self.assertEqual(plan.installments[0].closing_balance, 92115)
        self.assertEqual(plan.installments[5].payment, 8885)
        self.assertEqual(plan.balance_after(10), 17506)

    def test_03_final_installment_clears_the_balance(self):
        plan = self.value("小额计划", Plan, 50000, 6, 10000)
        self.assertEqual(len(plan.installments), 6)
        balances = [item.closing_balance for item in plan.installments]
        for earlier, later in zip(balances, balances[1:]):
            self.assertTrue(later < earlier, "余额必须逐期下降: %r" % (balances,))
        self.assertEqual(balances[-1], 0)
        self.assertEqual(plan.balance_after(6), 0)
        last = plan.installments[-1]
        self.assertEqual(last.index, 6)
        self.assertEqual(last.principal, last.opening_balance)
        self.assertEqual(last.payment, last.opening_balance + last.interest)
        self.assertEqual(plan.payments(), (8627, 8627, 8627, 8627, 8627, 8630))
        self.assertEqual(plan.total_principal(), 50000)
        self.assertEqual(plan.total_interest(), 1765)
        self.assertEqual(plan.total_payment(), 51765)


class GraceTest(KernelCase):
    """宽限期只付利息不还本。"""

    def test_04_grace_period_pays_interest_only(self):
        plan = self.value("带宽限期的计划", Plan, 120000, 12, 5000, METHOD_ANNUITY, 3)
        self.assertEqual(len(plan.installments), 12)
        for item in plan.installments[:3]:
            self.assertEqual(item.principal, 0, "第 %d 期是宽限期内，不应摊本金" % item.index)
            self.assertEqual(item.opening_balance, 120000)
            self.assertEqual(item.closing_balance, 120000)
            self.assertEqual(item.interest, 600)
            self.assertEqual(item.payment, item.interest)
        self.assertEqual(plan.balance_after(3), 120000)
        self.assertEqual(plan.installments[3].principal, 13069)
        self.assertEqual(plan.installments[3].payment, 13669)
        self.assertEqual(plan.installments[3].opening_balance, 120000)
        self.assertEqual(plan.total_principal(), 120000)
        self.assertEqual(plan.total_interest(), 4821)
        self.assertEqual(plan.balance_after(12), 0)


class EqualPrincipalTest(KernelCase):
    """等额本金：每期本金相同，末期收尾。"""

    def test_05_equal_principal_split(self):
        plan = self.value("等额本金计划", Plan, 120005, 12, 4500, METHOD_PRINCIPAL, 2)
        self.assertEqual(
            [item.principal for item in plan.installments],
            [0, 0, 12000, 12000, 12000, 12000, 12000, 12000, 12000, 12000, 12000, 12005],
        )
        self.assertEqual(
            [item.interest for item in plan.installments],
            [540, 540, 540, 486, 432, 378, 324, 270, 216, 162, 108, 54],
        )
        self.assertEqual(
            plan.payments(),
            (540, 540, 12540, 12486, 12432, 12378, 12324, 12270, 12216, 12162, 12108, 12059),
        )
        self.assertEqual(plan.balance_after(11), 12005)
        self.assertEqual(plan.total_principal(), 120005)
        self.assertEqual(plan.total_interest(), 4050)
        self.assertEqual(plan.total_principal() + plan.total_interest(), plan.total_payment())
        self.assertEqual(plan.balance_after(12), 0)

        plain = self.value("无宽限期的等额本金", Plan, 180000, 18, 6000, METHOD_PRINCIPAL)
        self.assertEqual([item.principal for item in plain.installments], [10000] * 18)
        self.assertEqual(plain.total_interest(), 10260)
        self.assertEqual(plain.installments[0].payment, 11080)
        self.assertEqual(plain.installments[17].payment, 10060)


class PrepaymentTest(KernelCase):
    """提前还款后的重算与对账。"""

    def test_06_prepayment_recomputes_the_remaining_installments(self):
        original = self.value("原计划", Plan, 200000, 24, 10000)
        self.assertEqual(original.total_interest(), 25951)
        self.assertEqual(original.balance_after(6), 154383)
        self.assertEqual(original.payments()[6], 9415)

        revised = self.value("重算计划", original.prepay, 6, 30000)
        self.assertEqual(revised.prepaid, 30000)
        self.assertEqual(len(revised.installments), 24)
        self.assertEqual([item.index for item in revised.installments], list(range(1, 25)))
        for before, after in zip(original.installments[:6], revised.installments[:6]):
            self.assertEqual(after.payment, before.payment)
            self.assertEqual(after.principal, before.principal)
        self.assertEqual(revised.balance_after(6), 154383 - 30000)
        self.assertEqual(revised.installments[6].opening_balance, 154383 - 30000)
        for earlier, later in zip(revised.installments, revised.installments[1:]):
            self.assertEqual(earlier.closing_balance, later.opening_balance)
        self.assertTrue(revised.payments()[6] < original.payments()[6])
        self.assertTrue(revised.total_interest() < original.total_interest())
        self.assertEqual(revised.total_interest(), 23020)
        self.assertEqual(revised.total_principal() + 30000, 200000)
        self.assertEqual(revised.total_payment() + 30000, revised.total_interest() + 200000)
        self.assertEqual(revised.balance_after(24), 0)


class PenaltyTest(KernelCase):
    """逾期罚息与宽限天数。"""

    def test_07_overdue_penalty_and_grace_days(self):
        self.assertEqual(overdue_penalty(50000, 10, 500), 250)
        self.assertEqual(overdue_penalty(50000, 10, 500, 10), 0)
        self.assertEqual(overdue_penalty(50000, 3, 500, 5), 0)
        self.assertEqual(overdue_penalty(50000, 10, 500, 4), 150)
        self.assertEqual(overdue_penalty(50000, 0, 500), 0)
        self.assertEqual(overdue_penalty(0, 30, 500), 0)
        self.assertEqual(overdue_penalty(1000, 1, 500), 1)
        self.assertEqual(overdue_penalty(999, 1, 500), 0)
        self.assertEqual(overdue_penalty(123456, 7, 400, 2), 247)


class ReconciliationTest(KernelCase):
    """总利息与总还款额对账。"""

    def test_08_totals_reconcile_with_the_installments(self):
        plan = self.value("等额本息计划", Plan, 100000, 12, 10000)
        self.assertEqual(plan.total_interest(), 6619)
        self.assertEqual(plan.total_principal(), 100000)
        self.assertEqual(plan.total_payment(), 106619)
        self.assertEqual(plan.total_payment(), plan.total_interest() + plan.total_principal())
        self.assertEqual(plan.total_payment(), sum(plan.payments()))

        level = self.value("等额本金计划", Plan, 180000, 18, 6000, METHOD_PRINCIPAL)
        self.assertEqual(level.total_interest(), 10260)
        self.assertEqual(level.total_principal(), 180000)
        self.assertEqual(level.total_payment(), level.total_interest() + 180000)

        revised = self.value("重算计划", plan.prepay, 4, 20000)
        self.assertEqual(revised.total_interest(), 5708)
        self.assertEqual(revised.total_principal(), 80000)
        self.assertEqual(revised.total_principal() + 20000, 100000)
        self.assertEqual(revised.total_payment(), 85708)
        self.assertEqual(revised.total_payment() + 20000, revised.total_interest() + 100000)


class TermsTest(KernelCase):
    """非法参数必须被拒绝。"""

    def test_09_invalid_terms_are_refused(self):
        for bad in (0, -1, -100000, 100000.0, "100000", None, True):
            self.refuses(LoanError, "本金 %r" % (bad,), Plan, bad, 12, 10000)
        self.refuses(LoanError, "期数为零", Plan, 100000, 0, 10000)
        self.refuses(LoanError, "期数为负", Plan, 100000, -3, 10000)
        self.refuses(LoanError, "利率为负", Plan, 100000, 12, -1)
        self.refuses(LoanError, "宽限期等于期数", Plan, 100000, 12, 10000, METHOD_ANNUITY, 12)
        self.refuses(LoanError, "宽限期为负", Plan, 100000, 12, 10000, METHOD_ANNUITY, -1)
        self.refuses(LoanError, "未知还款方式", Plan, 100000, 12, 10000, "bullet")
        self.refuses(LoanError, "本金不足以分摊", Plan, 9, 12, 10000, METHOD_PRINCIPAL)
        self.refuses(LoanError, "利息余额为负", interest_for, -1, 10000)
        self.refuses(LoanError, "月利率为负", interest_for, 1000, -1)
        self.refuses(LoanError, "分母为零", round_half_up, 3, 0)
        self.refuses(LoanError, "分母为负", round_half_up, 3, -7)
        self.refuses(LoanError, "分子为负", round_half_up, -3, 7)
        self.refuses(LoanError, "逾期金额为负", overdue_penalty, -1, 3, 500)
        self.refuses(LoanError, "逾期天数为负", overdue_penalty, 1000, -1, 500)
        self.refuses(LoanError, "罚息率为负", overdue_penalty, 1000, 3, -1)
        self.refuses(LoanError, "宽限天数为负", overdue_penalty, 1000, 3, 500, -1)
        self.refuses(LoanError, "利息期数为零", annuity_payment, 100000, 0, 10000)

        plan = self.value("普通计划", Plan, 100000, 12, 10000)
        self.refuses(LoanError, "期数下界", plan.balance_after, 0)
        self.refuses(LoanError, "期数上界", plan.balance_after, 13)
        self.refuses(LoanError, "提前还款期数为零", plan.prepay, 0, 1000)
        self.refuses(LoanError, "提前还款期数越界", plan.prepay, 12, 1000)
        self.refuses(LoanError, "提前还款额为负", plan.prepay, 3, -5)
        self.refuses(LoanError, "提前还款额为零", plan.prepay, 3, 0)
        self.refuses(LoanError, "提前还款额等于剩余本金", plan.prepay, 11, plan.balance_after(11))
        self.refuses(LoanError, "提前还款额超过剩余本金", plan.prepay, 3, plan.balance_after(3) + 1)


class BoundaryTest(KernelCase):
    """零利率、单期与最小本金等边界计划仍然自洽。"""

    def test_10_zero_rate_and_boundary_plans(self):
        plan = self.value("零利率等额本息", Plan, 10000, 5, 0)
        self.assertEqual(plan.payments(), (2000, 2000, 2000, 2000, 2000))
        self.assertEqual(plan.total_interest(), 0)
        self.assertEqual(plan.total_principal(), 10000)
        self.assertEqual(plan.balance_after(5), 0)
        self.assertTrue(all(item.interest == 0 for item in plan.installments))

        level = self.value("零利率等额本金", Plan, 14000, 7, 0, METHOD_PRINCIPAL)
        self.assertEqual([item.principal for item in level.installments], [2000] * 7)
        self.assertEqual(level.total_principal(), 14000)
        self.assertEqual(level.balance_after(7), 0)

        single = self.value("单期计划", Plan, 10000, 1, 0, METHOD_PRINCIPAL)
        self.assertEqual(len(single.installments), 1)
        self.assertEqual(single.installments[0].principal, 10000)
        self.assertEqual(single.installments[0].closing_balance, 0)
        self.assertEqual(single.total_principal(), 10000)

        waited = self.value("单期等额本金含息", Plan, 10000, 1, 5000, METHOD_PRINCIPAL)
        self.assertEqual(len(waited.installments), 1)
        self.assertEqual(waited.installments[0].interest, 50)
        self.assertEqual(waited.installments[0].payment, 10050)
        self.assertEqual(waited.installments[0].closing_balance, 0)
        self.assertEqual(waited.total_principal(), 10000)

        single_annuity = self.value("单期等额本息", Plan, 10000, 1, 0, METHOD_ANNUITY)
        self.assertEqual(single_annuity.installments[0].payment, 10000)
        self.assertEqual(single_annuity.balance_after(1), 0)

        tiny = self.value("最小本金", Plan, 500, 5, 0, METHOD_PRINCIPAL)
        self.assertEqual(tiny.total_principal(), 500)
        self.assertEqual(tiny.balance_after(5), 0)

        for item in plan.installments + level.installments:
            for amount in (item.opening_balance, item.interest, item.principal, item.payment, item.closing_balance):
                self.assertIsInstance(amount, int)
        self.assertTrue(all(item.closing_balance >= 0 for item in plan.installments))


if __name__ == "__main__":
    unittest.main()
