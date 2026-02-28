import random
import string
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User, UserProfile
from app.models.family import Family, FamilyMember
from app.models.medication import Medication
from app.models.insurance import Insurance
from app.models.record import Record
from app.models.reminder import Reminder
from app.schemas.family import (
    FamilyCreate, FamilyJoin, FamilyResponse,
    FamilyMemberResponse, FamilyOverviewMember, FamilyOverviewResponse,
)
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/families", tags=["families"])


def _generate_invite_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "FW" + "".join(random.choices(chars, k=4))


@router.post("", response_model=FamilyResponse)
async def create_family(
    req: FamilyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check if already in a family as admin
    existing = await db.execute(
        select(FamilyMember).where(
            FamilyMember.user_id == user.id,
            FamilyMember.role == "admin",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="你已经是一个家庭的管理者")

    code = _generate_invite_code()
    family = Family(
        name=req.name or f"{user.nickname}的家庭",
        invite_code=code,
        created_by=user.id,
    )
    db.add(family)
    await db.flush()

    member = FamilyMember(
        family_id=family.id,
        user_id=user.id,
        role="admin",
    )
    db.add(member)
    await db.flush()

    return FamilyResponse.model_validate(family)


@router.get("/mine", response_model=FamilyResponse | None)
async def get_my_family(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Family)
        .join(FamilyMember, FamilyMember.family_id == Family.id)
        .where(FamilyMember.user_id == user.id)
    )
    family = result.scalar_one_or_none()
    if not family:
        return None
    return FamilyResponse.model_validate(family)


@router.post("/join")
async def join_family(
    req: FamilyJoin,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Find family by invite code
    result = await db.execute(
        select(Family).where(Family.invite_code == req.invite_code.upper())
    )
    family = result.scalar_one_or_none()
    if not family:
        raise HTTPException(status_code=404, detail="邀请码无效")

    # Check if already a member
    existing = await db.execute(
        select(FamilyMember).where(
            FamilyMember.family_id == family.id,
            FamilyMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="你已经是该家庭成员")

    member = FamilyMember(
        family_id=family.id,
        user_id=user.id,
        role="member",
    )
    db.add(member)
    await db.flush()

    return {"message": "加入成功", "family_name": family.name}


@router.get("/{family_id}/members", response_model=list[FamilyMemberResponse])
async def list_members(
    family_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify user is a member
    check = await db.execute(
        select(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == user.id,
        )
    )
    if not check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="无权访问")

    result = await db.execute(
        select(FamilyMember, User)
        .join(User, User.id == FamilyMember.user_id)
        .where(FamilyMember.family_id == family_id)
    )
    rows = result.all()

    return [
        FamilyMemberResponse(
            user_id=fm.user_id,
            nickname=u.nickname,
            role=fm.role,
            joined_at=fm.joined_at,
        )
        for fm, u in rows
    ]


@router.get("/{family_id}/overview", response_model=FamilyOverviewResponse)
async def get_family_overview(
    family_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: get comprehensive family health overview."""
    # Verify admin
    check = await db.execute(
        select(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == user.id,
            FamilyMember.role == "admin",
        )
    )
    if not check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="仅管理者可查看")

    # Get all members
    members_result = await db.execute(
        select(FamilyMember, User, UserProfile)
        .join(User, User.id == FamilyMember.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(FamilyMember.family_id == family_id)
    )
    rows = members_result.all()

    total_meds = 0
    total_insurance = 0
    total_alerts = 0
    member_list = []

    for fm, u, profile in rows:
        # Count medications
        med_count = (await db.execute(
            select(func.count(Medication.id)).where(
                Medication.user_id == u.id, Medication.is_active == True
            )
        )).scalar()

        # Count insurance
        ins_count = (await db.execute(
            select(func.count(Insurance.id)).where(
                Insurance.user_id == u.id, Insurance.is_active == True
            )
        )).scalar()

        # Count urgent alerts
        alert_count = (await db.execute(
            select(func.count(Reminder.id)).where(
                Reminder.user_id == u.id,
                Reminder.is_resolved == False,
                Reminder.priority == "urgent",
            )
        )).scalar()

        # Last checkup
        last_checkup_result = await db.execute(
            select(Record.record_date)
            .where(Record.user_id == u.id, Record.category == "checkup")
            .order_by(Record.record_date.desc())
            .limit(1)
        )
        last_checkup = last_checkup_result.scalar()

        # Calculate age
        age = None
        if profile and profile.birthday:
            today = date.today()
            age = today.year - profile.birthday.year - (
                (today.month, today.day) < (profile.birthday.month, profile.birthday.day)
            )

        tags = profile.medical_history if profile and profile.medical_history else []
        if not tags:
            tags = ["健康"]

        total_meds += med_count
        total_insurance += ins_count
        total_alerts += alert_count

        member_list.append(FamilyOverviewMember(
            user_id=u.id,
            nickname=u.nickname,
            age=age,
            tags=tags,
            meds_count=med_count,
            insurance_count=ins_count,
            last_checkup=last_checkup.isoformat() if last_checkup else None,
            urgent_alerts=alert_count,
            ai_summary=None,  # Could generate with AI
            key_indicator=None,  # Could query latest key indicator
        ))

    return FamilyOverviewResponse(
        summary={
            "total_members": len(rows),
            "total_alerts": total_alerts,
            "total_meds": total_meds,
            "total_insurance": total_insurance,
        },
        members=member_list,
    )


@router.delete("/{family_id}/members/{member_user_id}")
async def remove_member(
    family_id: int,
    member_user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: remove a member from family."""
    check = await db.execute(
        select(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == user.id,
            FamilyMember.role == "admin",
        )
    )
    if not check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="仅管理者可操作")

    if member_user_id == user.id:
        raise HTTPException(status_code=400, detail="不能移除自己")

    result = await db.execute(
        select(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == member_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="成员不存在")

    await db.delete(member)
    return {"message": "已移除"}
