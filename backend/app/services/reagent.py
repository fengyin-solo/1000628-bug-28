"""试剂耗材业务规则：筛选、结存重算、排序、分页共用同一份条件。

物料状态与临期标记统一按有效期派生，保证列表、明细与有效期列口径一致；
冻结、耗尽属于流转状态，解冻时按有效期重新判定，避免临期状态丢失。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "reagent"
REQUIRED_FIELDS = ["物料编号", "物料名称", "规格纯度"]

STATUS_NORMAL = "正常可用"
STATUS_NEAR = "临近有效期"
STATUS_FROZEN = "已冻结"
STATUS_EXHAUSTED = "已耗尽"
STATUS_ORDER = [STATUS_NORMAL, STATUS_NEAR, STATUS_FROZEN, STATUS_EXHAUSTED]
# 冻结、耗尽是物料流转状态；正常/临期由有效期实时派生。
LIFECYCLE_STATUSES = {STATUS_FROZEN, STATUS_EXHAUSTED}

NEAR_EXPIRY_DAYS = 30

FILTER_FIELDS = ["物料编号", "物料名称", "规格纯度", "批号"]
SORTS = {"expiry_asc", "expiry_desc", "id_asc"}
DEFAULT_SORT = "expiry_asc"

ACTION_FREEZE = "冻结物料"
ACTION_UNFREEZE = "解冻物料"
ACTION_EXHAUST = "登记耗尽"
ACTION_RULES = {ACTION_FREEZE, ACTION_UNFREEZE, ACTION_EXHAUST}


def _parse_expiry(value: Any) -> date | None:
    """有效期只认 ISO 日期；占位文本或空值一律视为无可比日期，排序时沉底。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_near_expiry(row: dict[str, Any], *, today: date | None = None) -> bool:
    """已过有效期或未来 30 天内到期都算临期，保证临期物料排在最前并被标记。"""
    expiry = _parse_expiry(row.get("有效期至"))
    if expiry is None:
        return False
    today = today or date.today()
    return expiry <= today + timedelta(days=NEAR_EXPIRY_DAYS)


def _effective_status(row: dict[str, Any]) -> str:
    """对外状态：冻结/耗尽以流转状态为准，其余按有效期实时判定。"""
    stored = str(row.get("status") or "").strip()
    if stored in LIFECYCLE_STATUSES:
        return stored
    return STATUS_NEAR if _is_near_expiry(row) else STATUS_NORMAL


def _material_key(row: dict[str, Any]) -> str:
    """结存按物料汇总；物料编号缺失时退回物料名称，避免被归到空串一组。"""
    return str(row.get("物料编号") or row.get("物料名称") or "").strip()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class ReagentService:
    def list_entries(
        self,
        *,
        material_no: str | None = None,
        material_name: str | None = None,
        spec: str | None = None,
        batch_no: str | None = None,
        keyword: str | None = None,
        status: str | None = None,
        exclude_frozen: bool = False,
        sort: str = DEFAULT_SORT,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        # 第一步：用同一份条件完成全部筛选，排序、分页、结存、统计都基于这份结果。
        rows = [
            row for row in store.rows(MODULE)
            if self._matches(
                row,
                material_no=material_no,
                material_name=material_name,
                spec=spec,
                batch_no=batch_no,
                keyword=keyword,
                status=status,
                exclude_frozen=exclude_frozen,
            )
        ]

        # 第二步：结存数量按当前筛选结果重算（按物料编号汇总各批号结存），
        # 而不是沿用全量口径预先存好的数字。
        balances: dict[str, int] = {}
        for row in rows:
            key = _material_key(row)
            balances[key] = balances.get(key, 0) + _as_int(row.get("结存数量"))

        presented = [self._present(row, balance=balances.get(_material_key(row), 0)) for row in rows]

        stats = {
            "可用物料": sum(1 for row in presented if row["物料状态"] == STATUS_NORMAL),
            "临期物料": sum(1 for row in presented if row["临期标记"]),
            "已冻结物料": sum(1 for row in presented if row["物料状态"] == STATUS_FROZEN),
        }

        # 第三步：排序与翻页复用上面同一份结果，翻页不可能再混入被过滤掉的物料。
        presented.sort(key=lambda row: self._sort_key(row, sort))

        total = len(presented)
        start = max(page - 1, 0) * size
        return presented[start:start + size], total, stats

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        # 明细页的结存按该物料全部批号汇总；状态与临期标记同样走统一派生口径。
        balance = sum(
            _as_int(other.get("结存数量"))
            for other in store.rows(MODULE)
            if _material_key(other) == _material_key(row)
        )
        return self._present(row, balance=balance)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("批号", "结存数量", "有效期至", "保管人员"):
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_NORMAL
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        balance = sum(
            _as_int(other.get("结存数量"))
            for other in rows
            if _material_key(other) == _material_key(entry)
        )
        return self._present(entry, balance=balance), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"试剂物料 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于试剂耗材可执行范围"

        if action == ACTION_FREEZE:
            entry["status"] = STATUS_FROZEN
            entry["pending"] = False
            entry["abnormal"] = False
        elif action == ACTION_EXHAUST:
            entry["status"] = STATUS_EXHAUSTED
            entry["结存数量"] = 0
            entry["pending"] = False
            entry["abnormal"] = False
        else:  # 解冻物料：恢复后按有效期重新判定正常/临期，临期标记不再丢失。
            entry["status"] = STATUS_NEAR if _is_near_expiry(entry) else STATUS_NORMAL
            entry["pending"] = True
            entry["abnormal"] = _is_near_expiry(entry)
        return self._present(entry), f"试剂物料已{action}"

    def _matches(
        self,
        row: dict[str, Any],
        *,
        material_no: str | None,
        material_name: str | None,
        spec: str | None,
        batch_no: str | None,
        keyword: str | None,
        status: str | None,
        exclude_frozen: bool,
    ) -> bool:
        exact = {
            "物料编号": material_no,
            "物料名称": material_name,
            "规格纯度": spec,
            "批号": batch_no,
        }
        for field, needle in exact.items():
            if needle and needle not in str(row.get(field, "")):
                return False
        if keyword and not any(keyword in str(row.get(field, "")) for field in FILTER_FIELDS):
            return False
        current_status = _effective_status(row)
        if status and current_status != status:
            return False
        if exclude_frozen and current_status == STATUS_FROZEN:
            return False
        return True

    def _present(self, row: dict[str, Any], *, balance: int | None = None) -> dict[str, Any]:
        """统一出口：补齐临期标记、物料状态，按需替换为当前口径下的结存数量。"""
        view = dict(row)
        near = _is_near_expiry(row)
        view["临期标记"] = near
        view["物料状态"] = _effective_status(row)
        view["status"] = view["物料状态"]
        if balance is not None:
            view["结存数量"] = balance
        return view

    def _sort_key(self, row: dict[str, Any], sort: str) -> tuple[Any, ...]:
        expiry = _parse_expiry(row.get("有效期至"))
        row_id = _as_int(row.get("id"))
        far_future = date.max
        if sort == "expiry_desc":
            # 倒序时远的到期日排前；无有效期的记录缺少可比日期，统一沉底而不是顶到最前。
            return (0, -expiry.toordinal(), row_id) if expiry else (1, 0, row_id)
        if sort == "id_asc":
            return (0, 0, row_id)
        # 默认按有效期近 -> 远，临期/过期物料自然排在最前，无效日期沉底。
        return (expiry is None, expiry or far_future, row_id)
