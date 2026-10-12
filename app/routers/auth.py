from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import create_access_token, hash_password, verify_password
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import LoginIn, ProfileUpdate, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="このメールアドレスは既に登録されています")

    user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    role = "admin" if user_count == 0 else "worker"

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.email)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )
    token = create_access_token(user.id, user.email)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)

    new_password = data.get("new_password")
    if new_password:
        current = data.get("current_password") or ""
        if not current or not verify_password(current, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="現在のパスワードが正しくありません",
            )
        user.password_hash = hash_password(new_password)

    if "display_name" in data:
        name = (data["display_name"] or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="表示名は必須です",
            )
        user.display_name = name

    if "email" in data and data["email"] is not None:
        email = str(data["email"]).lower().strip()
        if email != user.email:
            taken = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if taken is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="このメールアドレスは既に登録されています",
                )
            user.email = email

    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
