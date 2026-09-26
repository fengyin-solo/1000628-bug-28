"""试剂耗材接口：维护试剂物料，覆盖冻结物料、解冻物料、登记耗尽等动作。

列表查询的筛选、排序、分页参数在同一处解析，导出接口复用同一套口径，
保证翻页、导出看到的结果与查询条件完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.reagent import (
    DEFAULT_SORT,
    SORTS,
    STATUS_ORDER,
    ReagentService,
)

router = APIRouter(prefix="/api/reagent", tags=["试剂耗材"])

service = ReagentService()

LIST_FIELDS = ["物料编号", "物料名称", "规格纯度", "批号", "结存数量", "有效期至", "保管人员", "物料状态"]
STATUSES = STATUS_ORDER


@dataclass
class ListParams:
    """一次列表请求的完整条件；筛选、排序、翻页都带着它，避免口径分叉。"""

    material_no: str | None = None
    material_name: str | None = None
    spec: str | None = None
    batch_no: str | None = None
    keyword: str | None = None
    status: str | None = None
    exclude_frozen: bool = False
    sort: str = DEFAULT_SORT
    page: int = 1
    size: int = 10


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def list_params(
    material_no: str | None = Query(default=None, description="按物料编号检索"),
    material_name: str | None = Query(default=None, description="按物料名称检索"),
    spec: str | None = Query(default=None, description="按规格纯度检索"),
    batch_no: str | None = Query(default=None, description="按批号检索"),
    # 兼容此前以物料编号做关键字的调用方式。
    keyword: str | None = Query(default=None, description="按物料编号等字段做关键字检索"),
    # 兼容页面直接用中文字段名提交的查询串。
    物料编号: str | None = Query(default=None, alias="物料编号"),
    物料名称: str | None = Query(default=None, alias="物料名称"),
    规格纯度: str | None = Query(default=None, alias="规格纯度"),
    批号: str | None = Query(default=None, alias="批号"),
    status: str | None = Query(default=None, description="正常可用、临近有效期、已冻结、已耗尽"),
    exclude_frozen: bool = Query(default=False, description="为 true 时筛掉已冻结物料"),
    sort: str = Query(default=DEFAULT_SORT, description="expiry_asc、expiry_desc、id_asc"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1),
) -> ListParams:
    status_value = _clean(status)
    if status_value is not None and status_value not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"物料状态「{status_value}」不在可选范围内")
    sort_value = _clean(sort) or DEFAULT_SORT
    if sort_value not in SORTS:
        raise HTTPException(status_code=400, detail="排序方式仅支持 expiry_asc、expiry_desc、id_asc")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    return ListParams(
        material_no=_clean(material_no) or _clean(物料编号),
        material_name=_clean(material_name) or _clean(物料名称),
        spec=_clean(spec) or _clean(规格纯度),
        batch_no=_clean(batch_no) or _clean(批号),
        keyword=_clean(keyword),
        status=status_value,
        exclude_frozen=exclude_frozen,
        sort=sort_value,
        page=page,
        size=size,
    )


@router.get("", response_model=PageResult[dict])
def list_entries(params: ListParams = Depends(list_params)) -> PageResult[dict]:
    """按同一份条件完成筛选、结存重算、排序与分页；没有数据时返回空页，不报错。"""
    items, total, stats = service.list_entries(
        material_no=params.material_no,
        material_name=params.material_name,
        spec=params.spec,
        batch_no=params.batch_no,
        keyword=params.keyword,
        status=params.status,
        exclude_frozen=params.exclude_frozen,
        sort=params.sort,
        page=params.page,
        size=params.size,
    )
    return PageResult(items=items, total=total, page=params.page, size=params.size, stats=stats)


@router.get("/export")
def export_entries(params: ListParams = Depends(list_params)) -> dict[str, Any]:
    """导出试剂耗材清单：复用列表同一套条件，一次取全量。"""
    items, total, _ = service.list_entries(
        material_no=params.material_no,
        material_name=params.material_name,
        spec=params.spec,
        batch_no=params.batch_no,
        keyword=params.keyword,
        status=params.status,
        exclude_frozen=params.exclude_frozen,
        sort=params.sort,
        page=1,
        size=10000,
    )
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
