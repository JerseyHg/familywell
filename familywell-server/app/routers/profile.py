from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserProfile
from app.schemas.profile import ProfileUpdate, ProfileResponse, VoiceParseRequest, VoiceParseResponse
from app.services import ai_service
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="档案未创建")
    return profile


@router.put("", response_model=ProfileResponse)
async def update_profile(
    req: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    # Mark onboarding complete if basic fields are filled
    if profile.real_name and profile.gender:
        profile.onboarding_completed = True

    await db.flush()
    return profile


@router.post("/voice-parse", response_model=VoiceParseResponse)
async def voice_parse(req: VoiceParseRequest):
    """Parse voice-to-text result using AI."""
    parsed = await ai_service.parse_voice_text(req.step, req.text)
    return VoiceParseResponse(parsed=parsed)
