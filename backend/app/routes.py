from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "version": "0.1.0"}
