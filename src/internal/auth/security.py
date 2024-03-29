from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Annotated, Final

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from prisma.models import User as UserRecord
from prisma.types import JwtBlacklistCreateInput

from internal.auth.schemas import TokenData, UserResponse
from internal.db import User, JWTBlacklist
from internal.settings import ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM

PWD: Final[CryptContext] = CryptContext(schemes=["bcrypt"], deprecated="auto")

OAUTH2_SCHEME: Final[OAuth2PasswordBearer] = OAuth2PasswordBearer(
    tokenUrl="/auth/token"
)

CredentialException: Final[HTTPException] = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credentials are not valid",
    headers={"WWW-Authenticate": "Bearer"},
)

TokenExpiredException: Final[HTTPException] = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token has expired",
    headers={"WWW-Authenticate": "Bearer"},
)


async def generate_password_hash(password: str) -> str:
    """
    Generates a password hash using the PWD library.

    Args:
        password (str): The password to hash.
    Returns:
        str: The hashed password.
    """
    return PWD.hash(secret=password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify if a plain password matches a hashed password.

    Args:
        plain_password (str): The plain password to verify.
        hashed_password (str): The hashed password to compare against.
    Returns:
        bool: True if the plain password matches the hashed password, False otherwise.
    """
    return PWD.verify(plain_password, hashed_password)


async def authenticate_user(email: str, password: str) -> UserRecord | bool:
    """
    Authenticates a user by checking if the email and password match a user record in the database.

    Args:
        email (str): The email of the user to authenticate.
        password (str): The password of the user to authenticate.
    Returns:
        Union[UserRecord, bool]: The authenticated user record if the email and password match, False otherwise.
    """
    user: UserRecord | None = await User.find_first(where={"email": email})
    if not user or not await verify_password(password, user.password):
        return False
    return user


async def create_access_token(data: dict[str, str]) -> str:
    """
    Creates a new access token for the given data.

    Args:
        data (dict[str, str]): The data to include in the access token.
    Returns:
        str: The newly created access token.
    """
    to_encode: dict = data.copy()
    expire: datetime = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def login_for_access_token(email: str, password: str) -> dict[str, str]:
    """
    Authenticates a user with the given email and password, and returns an access token if successful.

    Args:
        email (str): The email of the user to authenticate.
        password (str): The password of the user to authenticate.
    Returns:
        dict[str, str]: A dictionary containing the access token and token type.
    Raises:
        CredentialsException: If the user cannot be authenticated with the given email and password.
    """
    user: UserRecord | bool = await authenticate_user(email=email, password=password)
    if not user:
        raise CredentialException
    access_token = await create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


async def get_current_user(token: Annotated[str, Depends(OAUTH2_SCHEME)]) -> UserRecord:
    """
    Given a JWT token, decodes it and returns the corresponding user object.

    Args:
        token (str): JWT token to decode.
    Returns:
        User: User object corresponding to the decoded token.
    Raises:
        CredentialsException: If the token is invalid or the user does not exist.
    """
    try:
        payload: dict = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        if email is None:
            raise CredentialException
        token_data = TokenData(email=email)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredException
    except jwt.InvalidTokenError:
        raise CredentialException
    user: UserRecord | None = await User.find_first(
        where={"email": token_data.email},
        include={"jwt_blacklist": True}
    )
    if not user:
        raise CredentialException
    elif any(filter(lambda x: x.token == token, user.jwt_blacklist)):
        raise TokenExpiredException
    return user


def user_logged() -> type[UserResponse]:
    """
    Returns the type of the current user that is logged in.

    :return: The type of the current user that is logged in.
    """
    return Annotated[UserResponse, Depends(get_current_user)]


async def add_access_token_blacklist(token: str, user: UserResponse) -> None:
    """
    Adds a JWT token to the blacklist.

    Args:
        token (str): The JWT token to add to the blacklist.
        user (UserRecord): The user that owns the token.
    """
    data: JwtBlacklistCreateInput = {"token": token, "user": {"connect": {"id": user.id}}}
    await JWTBlacklist.create(data)
