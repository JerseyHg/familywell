import uuid
from datetime import datetime
from qcloud_cos import CosConfig, CosS3Client
from app.config import get_settings

settings = get_settings()

_cos_config = CosConfig(
    Region=settings.COS_REGION,
    SecretId=settings.COS_SECRET_ID,
    SecretKey=settings.COS_SECRET_KEY,
)
_client = CosS3Client(_cos_config)


def generate_file_key(user_id: int, file_name: str) -> str:
    """Generate a unique COS object key."""
    now = datetime.utcnow()
    ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "jpg"
    unique = uuid.uuid4().hex[:12]
    return f"uploads/{user_id}/{now.year}/{now.month:02d}/{unique}.{ext}"


def get_presigned_upload_url(file_key: str, content_type: str = "image/jpeg") -> str:
    """Get a presigned URL for direct upload from client to COS."""
    url = _client.get_presigned_url(
        Method="PUT",
        Bucket=settings.COS_BUCKET,
        Key=file_key,
        Expired=600,  # 10 minutes
        Headers={"Content-Type": content_type},
    )
    return url


def download_file(file_key: str, local_path: str) -> str:
    """Download a file from COS to local path."""
    _client.download_file(
        Bucket=settings.COS_BUCKET,
        Key=file_key,
        DestFilePath=local_path,
    )
    return local_path


def get_file_url(file_key: str) -> str:
    """Get a presigned download URL for a COS object."""
    return _client.get_presigned_url(
        Method="GET",
        Bucket=settings.COS_BUCKET,
        Key=file_key,
        Expired=3600,
    )

def generate_presigned_url(file_key: str, expires: int = 1800) -> str:
    """生成 COS 预签名下载 URL（让火山 ASR 能下载音频）"""
    return _client.get_presigned_url(
        Method='GET',
        Bucket=settings.COS_BUCKET,
        Key=file_key,
        Expired=expires,
    )
