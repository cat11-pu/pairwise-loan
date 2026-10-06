"""分期还款计划内核：等额本息与等额本金两种还款方式的逐期拆分。

金额一律用整数分表示，月利率与日罚息率用百万分之几的整数表示（RATE_SCALE 为一整份）。
利息与一切除法都落在非负整数上，按四舍五入到分处理，半数进位。
宽限期内的期次只付利息不摊本金；最后一期按剩余本金平衡，保证本金恰好还清。
本模块不读写文件、不访问网络、不打印，也不依赖当前时间与随机数。
"""

RATE_SCALE = 1000000

METHOD_ANNUITY = "annuity"
METHOD_PRINCIPAL = "principal"

_METHODS = (METHOD_ANNUITY, METHOD_PRINCIPAL)


class LoanError(ValueError):
    """本金、期数、利率、还款方式或还款参数不合法时抛出。"""


def _is_int(value):
    """是否为真整数（布尔值不算）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def round_half_up(numerator, denominator):
    """把非负分数 numerator / denominator 四舍五入到整数，半数进位。"""
    if not _is_int(numerator) or not _is_int(denominator):
        raise LoanError("分子与分母都必须是整数")
    if denominator <= 0:
        raise LoanError("分母必须为正")
    if numerator < 0:
        raise LoanError("分子不能为负")
    return numerator // denominator


def interest_for(balance, rate):
    """当期利息（分）：期初余额乘月利率，四舍五入到分。"""
    if not _is_int(balance) or balance < 0:
        raise LoanError("余额必须是非负整数分")
    if not _is_int(rate) or rate < 0:
        raise LoanError("月利率必须是非负整数")
    return round_half_up(balance * rate, RATE_SCALE)


def annuity_payment(principal, months, rate):
    """等额本息的每期还款额（分）：把本金按等额年金摊到各期，四舍五入到分。"""
    if not _is_int(principal) or principal <= 0:
        raise LoanError("本金必须是正整数分")
    if not _is_int(months) or months <= 0:
        raise LoanError("期数必须是正整数")
    if not _is_int(rate) or rate < 0:
        raise LoanError("月利率必须是非负整数")
    if rate == 0:
        return round_half_up(principal, months)
    growth = (RATE_SCALE + rate) ** months
    numerator = principal * rate * growth
    denominator = RATE_SCALE * growth
    return round_half_up(numerator, denominator)


def _check_terms(principal, months, rate, method, grace_months):
    """校验一份还款计划的参数。"""
    if not _is_int(principal) or principal <= 0:
        raise LoanError("本金必须是正整数分")
    if not _is_int(months) or months <= 0:
        raise LoanError("期数必须是正整数")
    if not _is_int(rate) or rate < 0:
        raise LoanError("月利率必须是非负整数")
    if not _is_int(grace_months) or not 0 <= grace_months < months:
        raise LoanError("宽限期必须落在零与期数之间")
    if method not in _METHODS:
        raise LoanError("还款方式只能是等额本息或等额本金")
    if method == METHOD_PRINCIPAL and principal < months - grace_months:
        raise LoanError("本金不足以让每一期都分到本金")


class Installment:
    """一期的还款明细：期初余额、利息、本金、还款额与期末余额。"""

    __slots__ = ("index", "opening_balance", "interest", "principal", "closing_balance")

    def __init__(self, index, opening_balance, interest, principal, closing_balance):
        self.index = index
        self.opening_balance = opening_balance
        self.interest = interest
        self.principal = principal
        self.closing_balance = closing_balance

    @property
    def payment(self):
        """当期还款额（分），等于当期利息与本金之和。"""
        return self.interest + self.principal

    def __repr__(self):
        return "Installment(%r, opening=%r, interest=%r, principal=%r, closing=%r)" % (
            self.index,
            self.opening_balance,
            self.interest,
            self.principal,
            self.closing_balance,
        )


def build_schedule(principal, months, rate, method=METHOD_ANNUITY, grace_months=0):
    """生成逐期明细：宽限期内只付利息，最后一期还清剩余本金。"""
    _check_terms(principal, months, rate, method, grace_months)
    amortizing_months = months - grace_months
    level = 0
    share = 0
    if method == METHOD_ANNUITY:
        level = annuity_payment(principal, amortizing_months, rate)
    else:
        share = principal // months
    schedule = []
    balance = principal
    for index in range(1, months + 1):
        opening = balance
        interest = interest_for(opening, rate)
        if index < grace_months:
            principal_part = 0
        elif index < months:
            principal_part = level - interest if method == METHOD_ANNUITY else share
        else:
            principal_part = share if method == METHOD_PRINCIPAL else level - interest
        balance = opening - principal_part
        schedule.append(Installment(index, opening, interest, principal_part, balance))
    return schedule


def _reindex(installments, first):
    """把一段明细的期次编号从 first 起接续。"""
    rows = []
    for offset, item in enumerate(installments):
        rows.append(
            Installment(
                first + offset,
                item.opening_balance,
                item.interest,
                item.principal,
                item.closing_balance,
            )
        )
    return rows


class Plan:
    """一份完整的还款计划。"""

    __slots__ = ("principal", "months", "rate", "method", "grace_months", "prepaid", "installments")

    def __init__(self, principal, months, rate, method=METHOD_ANNUITY, grace_months=0):
        self.principal = principal
        self.months = months
        self.rate = rate
        self.method = method
        self.grace_months = grace_months
        self.prepaid = 0
        self.installments = tuple(build_schedule(principal, months, rate, method, grace_months))

    @classmethod
    def _composed(cls, principal, months, rate, method, grace_months, prepaid, installments):
        """按已经算好的明细装配一份计划（提前还款重算用）。"""
        plan = cls.__new__(cls)
        plan.principal = principal
        plan.months = months
        plan.rate = rate
        plan.method = method
        plan.grace_months = grace_months
        plan.prepaid = prepaid
        plan.installments = tuple(installments)
        return plan

    def balance_after(self, period):
        """第 period 期结清后的剩余本金（分）。"""
        if not _is_int(period) or not 1 <= period <= self.months:
            raise LoanError("期数超出计划范围")
        return self.installments[period - 1].closing_balance

    def payments(self):
        """逐期还款额（分）。"""
        return tuple(item.payment for item in self.installments)

    def total_interest(self):
        """利息合计（分）。"""
        return self.total_payment() - self.principal

    def total_principal(self):
        """逐期归还的本金合计（分），不含提前还款。"""
        return sum(item.principal for item in self.installments)

    def total_payment(self):
        """逐期还款额合计（分），不含提前还款。"""
        return sum(item.payment for item in self.installments)

    def prepay(self, after_period, amount):
        """在第 after_period 期结清后额外归还 amount 分，返回重算后的计划。

        提前还款只冲减本金：当期期末余额相应减少，剩余期数不变，
        后续各期按新的剩余本金重新摊算。
        """
        if not _is_int(after_period) or not 1 <= after_period < self.months:
            raise LoanError("提前还款只能落在中间各期之后")
        if not _is_int(amount) or amount <= 0:
            raise LoanError("提前还款额必须是正整数分")
        balance = self.balance_after(after_period)
        if amount >= balance:
            raise LoanError("提前还款额必须小于剩余本金")
        tail = build_schedule(balance, self.months - after_period, self.rate, self.method, 0)
        head = list(self.installments[:after_period])
        return Plan._composed(
            self.principal,
            self.months,
            self.rate,
            self.method,
            self.grace_months,
            self.prepaid + amount,
            head + _reindex(tail, after_period + 1),
        )


def overdue_penalty(due, days_late, daily_rate, grace_days=0):
    """逾期罚息（分）：超过宽限天数后按日计收，四舍五入到分。

    due 是逾期金额（分），daily_rate 是日罚息率（百万分之几），
    grace_days 之内的逾期不计罚息。
    """
    if not _is_int(due) or due < 0:
        raise LoanError("逾期金额必须是非负整数分")
    if not _is_int(days_late) or days_late < 0:
        raise LoanError("逾期天数不能为负")
    if not _is_int(daily_rate) or daily_rate < 0:
        raise LoanError("日罚息率必须是非负整数")
    if not _is_int(grace_days) or grace_days < 0:
        raise LoanError("宽限天数不能为负")
    chargeable_days = days_late
    if chargeable_days <= 0:
        return 0
    return round_half_up(due * daily_rate * chargeable_days, RATE_SCALE)
