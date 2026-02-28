"""
Projects Router — 项目（健康记录文件夹）管理
──────────────────────────────────────────────
POST   /api/projects              创建项目
GET    /api/projects              项目列表
GET    /api/projects/:id          项目详情
PUT    /api/projects/:id          更新项目
DELETE /api/projects/:id          删除项目
POST   /api/projects/:id/records  批量归入记录
DELETE /api/projects/:id/records  批量移出记录
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.record import Record
from app.models.project import Project
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    ProjectListResponse, RecordAssign,
)
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ─── Helpers ───

async def _get_project_or_404(
    db: AsyncSession, project_id: int, user_id: int
) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


async def _count_records(db: AsyncSession, project_id: int) -> int:
    result = await db.execute(
        select(func.count(Record.id)).where(Record.project_id == project_id)
    )
    return result.scalar() or 0


def _to_response(project: Project, record_count: int = 0) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        icon=project.icon,
        start_date=project.start_date,
        end_date=project.end_date,
        status=project.status,
        template=project.template,
        record_count=record_count,
        created_at=project.created_at,
    )


# ─── CRUD ───

@router.post("", response_model=ProjectResponse)
async def create_project(
    req: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建项目。"""
    project = Project(
        user_id=user.id,
        name=req.name,
        description=req.description,
        icon=req.icon,
        start_date=req.start_date,
        end_date=req.end_date,
        template=req.template,
        status="active",
    )
    db.add(project)
    await db.flush()

    # 如果设置了时间范围，自动归入该时间段内的记录
    if req.start_date:
        auto_query = (
            update(Record)
            .where(
                Record.user_id == user.id,
                Record.project_id.is_(None),
                Record.record_date >= req.start_date,
            )
        )
        if req.end_date:
            auto_query = auto_query.where(Record.record_date <= req.end_date)
        auto_query = auto_query.values(project_id=project.id)
        await db.execute(auto_query)

    count = await _count_records(db, project.id)
    return _to_response(project, count)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    status: str | None = Query(None, description="active|archived"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取项目列表（含每个项目的记录数）。"""
    query = select(Project).where(Project.user_id == user.id)
    count_query = select(func.count(Project.id)).where(Project.user_id == user.id)

    if status:
        query = query.where(Project.status == status)
        count_query = count_query.where(Project.status == status)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Project.created_at.desc())
    result = await db.execute(query)
    projects = result.scalars().all()

    # 批量获取每个项目的记录数
    items = []
    for p in projects:
        count = await _count_records(db, p.id)
        items.append(_to_response(p, count))

    return ProjectListResponse(total=total, items=items)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取项目详情。"""
    project = await _get_project_or_404(db, project_id, user.id)
    count = await _count_records(db, project.id)
    return _to_response(project, count)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    req: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新项目信息。"""
    project = await _get_project_or_404(db, project_id, user.id)

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    await db.flush()

    count = await _count_records(db, project.id)
    return _to_response(project, count)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除项目（记录不删除，只是解除关联）。"""
    project = await _get_project_or_404(db, project_id, user.id)

    # 解除所有记录的 project_id
    await db.execute(
        update(Record)
        .where(Record.project_id == project_id)
        .values(project_id=None)
    )

    await db.delete(project)
    return {"ok": True}


# ─── 记录归入 / 移出 ───

@router.post("/{project_id}/records")
async def assign_records(
    project_id: int,
    req: RecordAssign,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量将记录归入项目。"""
    await _get_project_or_404(db, project_id, user.id)

    result = await db.execute(
        update(Record)
        .where(
            Record.id.in_(req.record_ids),
            Record.user_id == user.id,
        )
        .values(project_id=project_id)
    )

    return {"ok": True, "updated": result.rowcount}


@router.delete("/{project_id}/records")
async def remove_records(
    project_id: int,
    req: RecordAssign,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量将记录从项目中移出。"""
    await _get_project_or_404(db, project_id, user.id)

    result = await db.execute(
        update(Record)
        .where(
            Record.id.in_(req.record_ids),
            Record.user_id == user.id,
            Record.project_id == project_id,
        )
        .values(project_id=None)
    )

    return {"ok": True, "updated": result.rowcount}
