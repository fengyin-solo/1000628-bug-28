"""试剂耗材业务规则：状态流转、字段校验与筛选口径都收在这里。

查询统一走「过滤 → 按当前结果汇总结存 → 排序 → 分页」一条流水线，
保证列表、明细、导出看到的结存数量与物料状态口径一致。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "reagent"
REQUIRED_FIELDS = ["物料编号", "物料名称", "规格纯度"]
STATUS_ORDER = ["正常可用", "临近有效期", "已冻结", "已耗尽"]
ACTION_RULES = {"冻结物料": "已冻结", "解冻物料": "正常可用", "登记耗尽": "已耗尽"}
NEGATIVE_ACTIONS = []

# 距有效期不足该天数即视为临近有效期
NEAR_EXPIRY_DAYS = 30

# 结存数量按「物料编号 + 规格纯度」聚合，同一物料同一规格在当前筛选口径下重算
BALANCE_KEYS = ["物料编号", "规格纯度"]

# 列表支持的检索字段：查询参数名 -> 数据字段名
FILTER_FIELDS = {"keyword": "物料编号", "spec": "规格纯度", "batch": "批号"}

# 列表支持的排序字段
SORT_FIELDS = ["有效期至", "结存数量", "物料编号", "批号"]
DEFAULT_SORT = "有效期至"

LOCKED_STATUSES = ("已冻结", "已耗尽")


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _amount(value: Any) -> float | int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else number


def _is_near_expiry(row: dict[str, Any], today: date) -> bool:
    expiry = _parse_date(row.get("有效期至"))
    return expiry is not None and expiry <= today + timedelta(days=NEAR_EXPIRY_DAYS)


def _usable_status(row: dict[str, Any], today: date) -> str:
    """未冻结、未耗尽时按有效期推导可用状态，临期物料不会被写成正常可用。"""
    return "临近有效期" if _is_near_expiry(row, today) else "正常可用"


def _effective_status(row: dict[str, Any], today: date) -> str:
    status = str(row.get("status") or "")
    if status in LOCKED_STATUSES:
        return status
    return _usable_status(row, today)


def _balance_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in BALANCE_KEYS)


class ReagentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        spec: str | None = None,
        batch: str | None = None,
        status: str | None = None,
        hide_frozen: bool = False,
        sort: str | None = None,
        order: str = "asc",
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        today = date.today()
        rows = store.rows(MODULE)
        filters = {"keyword": keyword, "spec": spec, "batch": batch}
        for param, field in FILTER_FIELDS.items():
            value = filters[param]
            if value:
                rows = [row for row in rows if value in str(row.get(field, ""))]
        if status:
            rows = [row for row in rows if _effective_status(row, today) == status]
        if hide_frozen:
            rows = [row for row in rows if _effective_status(row, today) != "已冻结"]

        # 先按当前过滤结果汇总结存，再排序、分页：翻页与排序用的是同一份条件
        balances: dict[tuple[str, ...], float | int] = {}
        for row in rows:
            key = _balance_key(row)
            balances[key] = balances.get(key, 0) + _amount(row.get("结存数量"))

        rows = self._sort_rows(rows, balances, sort or DEFAULT_SORT, order)

        total = len(rows)
        start = max(page - 1, 0) * size
        items = [self._serialize(row, balances, today) for row in rows[start:start + size]]
        return items, total, self._summary(today)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        today = date.today()
        # 明细按全量口径汇总结存，状态与临期标记的推导与列表保持一致
        balances: dict[tuple[str, ...], float | int] = {}
        for row in store.rows(MODULE):
            key = _balance_key(row)
            balances[key] = balances.get(key, 0) + _amount(row.get("结存数量"))
        return self._serialize(entry, balances, today)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("批号", "结存数量", "有效期至", "保管人员"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = _usable_status(entry, date.today())
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"试剂物料 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于试剂耗材可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "解冻物料":
            # 解冻后按有效期重新推导：临近有效期的物料恢复临期状态，而不是一律回到正常可用
            entry["status"] = _usable_status(entry, date.today())
        else:
            entry["status"] = target
        if action == "登记耗尽":
            entry["结存数量"] = 0
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"试剂物料已{action}"

    def _serialize(
        self,
        row: dict[str, Any],
        balances: dict[tuple[str, ...], float | int],
        today: date,
    ) -> dict[str, Any]:
        item = dict(row)
        item["结存数量"] = balances.get(_balance_key(row), _amount(row.get("结存数量")))
        item["物料状态"] = _effective_status(row, today)
        # 已耗尽的物料不再提示临期；冻结中的物料保留临期标记，解冻后状态随之恢复
        item["临期"] = _is_near_expiry(row, today) and item["物料状态"] != "已耗尽"
        return item

    def _sort_rows(
        self,
        rows: list[dict[str, Any]],
        balances: dict[tuple[str, ...], float | int],
        sort: str,
        order: str,
    ) -> list[dict[str, Any]]:
        descending = order == "desc"
        if sort == "有效期至":
            def key(row: dict[str, Any]) -> tuple[bool, int]:
                expiry = _parse_date(row.get("有效期至"))
                ordinal = expiry.toordinal() if expiry else 0
                # 缺失有效期的记录始终排在最后，与升降序无关
                return (expiry is None, -ordinal if descending else ordinal)
        elif sort == "结存数量":
            def key(row: dict[str, Any]) -> tuple[bool, float | int]:
                amount = balances.get(_balance_key(row), 0)
                return (False, -amount if descending else amount)
        else:
            def key(row: dict[str, Any]) -> tuple[bool, str]:
                return (False, str(row.get(sort) or ""))

            return sorted(rows, key=key, reverse=descending)
        return sorted(rows, key=key)

    def _summary(self, today: date) -> dict[str, int]:
        summary = {status: 0 for status in STATUS_ORDER}
        for row in store.rows(MODULE):
            summary[_effective_status(row, today)] += 1
        return summary
