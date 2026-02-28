from datetime import date
from pydantic import BaseModel


class ProfileUpdate(BaseModel):
    real_name: str | None = None
    gender: str | None = None
    birthday: date | None = None
    blood_type: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    allergies: list[str] | None = None
    medical_history: list[str] | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class ProfileResponse(BaseModel):
    real_name: str | None
    gender: str | None
    birthday: date | None
    blood_type: str | None
    height_cm: float | None
    weight_kg: float | None
    allergies: list | None
    medical_history: list | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    onboarding_completed: bool

    class Config:
        from_attributes = True


class VoiceParseRequest(BaseModel):
    step: str
    text: str


class VoiceParseResponse(BaseModel):
    parsed: dict
