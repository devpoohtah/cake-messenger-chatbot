from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {"service": "Mrs Brave's Cake - AI Messenger Ordering", "status": "running"}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
