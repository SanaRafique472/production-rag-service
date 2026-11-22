"""Signup, login (OAuth2 password form) and current user."""

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_container, get_db, rate_limit
from app.auth.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.errors import AuthenticationError, EmailTakenError
from app.schemas import SignupRequest, TokenOut, UserOut, error_responses
from app.services.container import Container

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth"))],
    responses=error_responses("EMAIL_ALREADY_REGISTERED", "VALIDATION_ERROR", "RATE_LIMITED"),
)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)) -> User:
    """Create an account (password stored as an Argon2 hash)."""
    email = body.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise EmailTakenError("An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise EmailTakenError("An account with this email already exists.") from exc
    return user


@router.post(
    "/login",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit("auth"))],
    responses=error_responses("NOT_AUTHENTICATED", "RATE_LIMITED"),
)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    c: Container = Depends(get_container),
) -> TokenOut:
    """Exchange email (as ``username``) and password for a JWT."""
    user = await db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not verify_password(form.password, user.password_hash):
        raise AuthenticationError("Incorrect email or password.")
    token, seconds = create_access_token(user.id, c.settings)
    return TokenOut(access_token=token, expires_in=seconds)


@router.get("/me", response_model=UserOut, responses=error_responses("NOT_AUTHENTICATED"))
async def me(user: User = Depends(current_user)) -> User:
    """The logged-in user."""
    return user
