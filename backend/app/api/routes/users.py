from fastapi import APIRouter, File, Request, Response, UploadFile, status

from app.api.deps import DB, CurrentUser
from app.api.schemas import ProfileUpdate, UserOut
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


def _out(db, user) -> UserOut:
    return UserOut.model_validate(user).model_copy(
        update={"has_photo": user_service.has_photo(db, user.id)}
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser, db: DB) -> UserOut:
    return _out(db, user)


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, user: CurrentUser, db: DB) -> UserOut:
    user = user_service.update_profile(
        db, user, full_name=body.full_name, default_currency=body.default_currency
    )
    return _out(db, user)


@router.put("/me/photo", response_model=UserOut)
async def upload_photo(user: CurrentUser, db: DB, file: UploadFile = File(...)) -> UserOut:
    limit = get_settings().max_photo_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed("Photo exceeds the 2 MB limit", code="FILE_TOO_LARGE")
    user_service.set_photo(db, user, data)
    return _out(db, user)


@router.get("/me/photo", responses={200: {"content": {"image/*": {}}}})
def get_photo(user: CurrentUser, db: DB, request: Request) -> Response:
    photo = user_service.get_photo(db, user.id)
    headers = {"ETag": f'"{photo.etag}"', "Cache-Control": "private, max-age=300"}
    if request.headers.get("if-none-match") == f'"{photo.etag}"':
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=photo.data, media_type=photo.content_type, headers=headers)


@router.delete("/me/photo", status_code=status.HTTP_204_NO_CONTENT)
def delete_photo(user: CurrentUser, db: DB) -> Response:
    user_service.delete_photo(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
