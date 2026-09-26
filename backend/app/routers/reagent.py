"""试剂耗材接口：维护试剂物料，覆盖冻结物料、解冻物料、登记耗尽等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.reagent import SORT_FIELDS, ReagentService

router = APIRouter(prefix="/api/reagent", tags=["试剂耗材"])

service = ReagentService()

LIST_FIELDS = ["物料编号", "物料名称", "规格纯度", "批号", "结存数量", "有效期至", "保管人员", "物料状态"]
STATUSES = ["正常可用", "临近有效期", "已冻结", "已耗尽"]


class ReagentPageResult(PageResult[dict]):
    """试剂物料分页结果：附带各状态的全量统计，供列表页卡片使用。"""

    summary: dict[str, int] = Field(default_factory=dict)


def _parse_conditions(
    keyword: str | None,
    spec: str | None,
    batch: str | None,
    status: str | None,
    hide_frozen: bool,
    sort: str | None,
    order: str,
) -> dict[str, Any]:
    """把查询参数整理成服务层条件；排序字段或方向不认时直接说明原因。"""
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"物料状态「{status}」不在允许的状态序列里")
    if sort and sort not in SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"排序字段「{sort}」不支持，可选：{'、'.join(SORT_FIELDS)}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="排序方向只支持 asc 或 desc")
    return {
        "keyword": keyword,
        "spec": spec,
        "batch": batch,
        "status": status,
        "hide_frozen": hide_frozen,
        "sort": sort,
        "order": order,
    }


@router.get("", response_model=ReagentPageResult)
def list_entries(
    keyword: str | None = Query(default=None, description="按物料编号检索"),
    spec: str | None = Query(default=None, description="按规格纯度检索"),
    batch: str | None = Query(default=None, description="按批号检索"),
    status: str | None = Query(default=None, description="正常可用、临近有效期、已冻结、已耗尽"),
    hide_frozen: bool = Query(default=False, description="筛掉已冻结物料"),
    sort: str | None = Query(default=None, description="排序字段，默认按有效期至升序"),
    order: str = Query(default="asc", description="asc 升序、desc 降序"),
    page: int = 1,
    size: int = 20,
) -> ReagentPageResult:
    """按同一组条件过滤、排序、分页试剂物料列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    conditions = _parse_conditions(keyword, spec, batch, status, hide_frozen, sort, order)
    items, total, summary = service.list_entries(page=page, size=size, **conditions)
    return ReagentPageResult(items=items, total=total, page=page, size=size, summary=summary)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按物料编号检索"),
    spec: str | None = Query(default=None, description="按规格纯度检索"),
    batch: str | None = Query(default=None, description="按批号检索"),
    status: str | None = Query(default=None, description="正常可用、临近有效期、已冻结、已耗尽"),
    hide_frozen: bool = Query(default=False, description="筛掉已冻结物料"),
    sort: str | None = Query(default=None, description="排序字段，默认按有效期至升序"),
    order: str = Query(default="asc", description="asc 升序、desc 降序"),
) -> dict[str, Any]:
    """导出试剂耗材清单：返回当前过滤条件下的全量数据。"""
    conditions = _parse_conditions(keyword, spec, batch, status, hide_frozen, sort, order)
    items, total, _ = service.list_entries(page=1, size=10000, **conditions)
    return {"module": "reagent", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条试剂物料明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"试剂物料 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条试剂物料，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="试剂物料已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条试剂物料执行冻结物料、解冻物料、登记耗尽；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
