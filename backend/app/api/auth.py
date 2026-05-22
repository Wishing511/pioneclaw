from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_access_token,
    decode_refresh_token,
    decode_reset_token,
    get_db,
    get_password_hash,
    settings,
    validate_password_strength,
    verify_password,
)
from app.core.config import settings as config
from app.core.rate_limit import RateLimit
from app.models import Organization, User, UserRole
from app.schemas import (
    ChangePasswordRequest,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    ProfileUpdateRequest,
    RefreshTokenRequest,
    UserCreate,
    UserLogin,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["认证"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    """获取当前登录用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")

    # 检查账户锁定（strip tzinfo 以兼容 SQLite 存储的无时区 datetime）
    if user.locked_until and user.locked_until.replace(tzinfo=None) > datetime.now(
        timezone.utc
    ).replace(tzinfo=None):
        raise HTTPException(
            status_code=423,
            detail=f"账户已锁定，请于 {user.locked_until.strftime('%H:%M')} 后重试",
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """获取当前活跃用户"""
    return current_user


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(times=3, seconds=60)),
):
    """用户注册（自动创建组织，限频 3次/分钟）"""
    # 检查用户名是否存在
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 检查邮箱是否存在
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已注册")

    # 密码复杂度验证
    is_valid, err_msg = validate_password_strength(user_data.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg)

    # 创建默认组织（以用户名命名）
    org_code = user_data.username
    result = await db.execute(select(Organization).where(Organization.code == org_code))
    if result.scalar_one_or_none():
        org_code = f"{user_data.username}_{id(user_data)}"

    org = Organization(
        name=f"{user_data.display_name or user_data.username}的组织",
        code=org_code,
        description="默认组织",
        type="company",
        level=1,
    )
    db.add(org)
    await db.flush()
    org.path = org.id

    # 创建用户
    user = User(
        username=user_data.username,
        email=user_data.email,
        display_name=user_data.display_name,
        hashed_password=get_password_hash(user_data.password),
        role=UserRole.ORG_ADMIN,  # 注册用户默认为组织管理员
        is_org_admin=True,
        organization_id=org.id,
    )
    db.add(user)
    await db.flush()

    # 更新组织管理者
    org.manager_id = user.id

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login")
async def login(
    request: UserLogin,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(times=10, seconds=60)),
):
    """用户登录（含账户锁定检测，限频 10次/分钟）"""
    username = request.username
    password = request.password

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # 检查账户锁定（strip tzinfo 以兼容 SQLite 存储的无时区 datetime）
    if user.locked_until and user.locked_until.replace(tzinfo=None) > datetime.now(
        timezone.utc
    ).replace(tzinfo=None):
        raise HTTPException(
            status_code=423,
            detail=f"账户已锁定，请于 {user.locked_until.strftime('%H:%M')} 后重试",
        )

    if not verify_password(password, user.hashed_password):
        # 增加失败计数
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1

        # 超过最大尝试次数则锁定
        if user.failed_login_attempts >= config.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=config.LOCKOUT_DURATION_MINUTES
            )
            await db.commit()
            raise HTTPException(
                status_code=423,
                detail=f"登录失败次数过多，账户已锁定 {config.LOCKOUT_DURATION_MINUTES} 分钟",
            )

        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")

    # 登录成功，重置失败计数
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = request.ip

    # 生成双令牌
    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})

    await db.commit()

    # 构建响应：access_token 在 body，refresh_token 在 HttpOnly cookie
    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "access_token": access_token,
            "token_type": "bearer",
        },
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,  # 开发环境 HTTP，生产应为 True
        samesite="strict",
        max_age=config.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )
    return response


@router.post("/refresh-token")
async def refresh_token(
    req: Request,
    body: RefreshTokenRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(times=20, seconds=60)),
):
    """刷新访问令牌（限频 20次/分钟）"""
    # 优先从 body 取，fallback 到 HttpOnly cookie
    token = (body and body.refresh_token) or req.cookies.get("refresh_token")

    payload = decode_refresh_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    # 生成新令牌
    access_token = create_access_token(data={"sub": str(user.id)})
    new_refresh_token = create_refresh_token(data={"sub": str(user.id)})

    response = JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "bearer",
        },
    )
    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=config.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )
    return response


@router.post("/logout", response_model=MessageResponse)
async def logout(current_user: User = Depends(get_current_active_user)):
    """用户登出"""
    response = JSONResponse(content={"message": "登出成功"})
    response.delete_cookie(key="refresh_token", path="/api/auth")
    return response


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户信息"""
    from app.core.permissions import get_user_permission_codes

    user_data = UserResponse.model_validate(current_user)
    user_data.permissions = await get_user_permission_codes(current_user, db)
    return user_data


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新用户资料"""
    if profile_data.display_name is not None:
        current_user.display_name = profile_data.display_name
    if profile_data.avatar is not None:
        current_user.avatar = profile_data.avatar
    if profile_data.phone is not None:
        current_user.phone = profile_data.phone
    if profile_data.department is not None:
        current_user.department = profile_data.department
    if profile_data.position is not None:
        current_user.position = profile_data.position

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    password_data: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """修改密码"""
    old_password = password_data.old_password
    new_password = password_data.new_password

    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="当前密码错误")

    is_valid, err_msg = validate_password_strength(new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg)

    current_user.hashed_password = get_password_hash(new_password)
    await db.commit()

    return MessageResponse(message="密码修改成功")


@router.post("/password-reset/request", response_model=MessageResponse)
async def request_password_reset(
    request: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(times=3, seconds=60)),
):
    """请求重置密码（限频 3次/分钟）"""
    email = request.email

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        # 不泄露用户是否存在
        return MessageResponse(message="如果该邮箱已注册，重置链接已发送")

    # 生成重置 token
    reset_token = create_reset_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=config.PASSWORD_RESET_EXPIRE_MINUTES),
    )

    # 发送重置邮件
    from app.core.email import build_password_reset_email, send_email

    reset_link = f"{config.FRONTEND_URL}/reset-password?token={reset_token}"
    subject, html_body, text_body = build_password_reset_email(
        reset_link=reset_link,
        username=user.display_name or user.username,
    )
    sent = await send_email(
        to=user.email, subject=subject, html_body=html_body, text_body=text_body
    )

    if not sent:
        # 开发模式：SMTP 未配置时打印到日志
        import logging

        _logger = logging.getLogger(__name__)
        _logger.info(f"[DEV] 密码重置链接: {reset_link}")

    return MessageResponse(message="如果该邮箱已注册，重置链接已发送")


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(times=5, seconds=60)),
):
    """确认重置密码（限频 5次/分钟）"""
    token = request.token
    new_password = request.new_password

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于6位")

    payload = decode_reset_token(token)
    if payload is None:
        raise HTTPException(status_code=400, detail="无效或已过期的重置令牌")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.hashed_password = get_password_hash(new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.commit()

    return MessageResponse(message="密码重置成功")


@router.get("/validate-token")
async def validate_token(current_user: User = Depends(get_current_active_user)):
    """验证令牌是否有效"""
    return {"valid": True, "user_id": current_user.id}
