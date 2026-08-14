import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.user import User
from backend.services.auth import (
    verify_password,
    hash_password,
    create_access_token,
    get_current_user,
    require_admin
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, description="La nuova password deve contenere almeno 8 caratteri")

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    is_admin: bool = False

class ResetUserPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)

class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True

@router.post("/login")
async def login(
    login_data: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    username = login_data.username.strip()
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    now = datetime.now(timezone.utc)

    if not user:
        logger.warning(f"Tentativo di login fallito per utente inesistente: {username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenziali non corrette."
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Questo account è stato disabilitato dall'amministratore."
        )

    # Verifica blocco per troppi tentativi falliti
    if user.locked_until:
        locked_time = user.locked_until
        if locked_time.tzinfo is None:
            locked_time = locked_time.replace(tzinfo=timezone.utc)
            
        if locked_time > now:
            remaining = int((locked_time - now).total_seconds() / 60) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Account temporaneamente bloccato per sicurezza. Riprova tra {remaining} minuti."
            )
        else:
            user.locked_until = None
            user.failed_attempts = 0

    if not verify_password(login_data.password, user.hashed_password):
        user.failed_attempts = (user.failed_attempts or 0) + 1
        max_attempts = 5
        
        if user.failed_attempts >= max_attempts:
            user.locked_until = now + timedelta(minutes=15)
            await db.commit()
            logger.warning(f"Account bloccato per troppi tentativi: {username}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Troppi tentativi falliti. Account bloccato per 15 minuti."
            )
            
        await db.commit()
        remaining_attempts = max_attempts - user.failed_attempts
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Password errata. {remaining_attempts} tentativi rimasti prima del blocco temporaneo."
        )

    # Login riuscito
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = now
    await db.commit()

    token = create_access_token(data={"sub": user.username})

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=7 * 24 * 3600,
        samesite="lax",
        secure=False
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "is_admin": user.is_admin
    }

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="access_token", path="/")
    return {"status": "success", "message": "Disconnesso con successo"}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La password attuale non è corretta."
        )

    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nuova password deve contenere almeno 8 caratteri."
        )

    current_user.hashed_password = hash_password(data.new_password)
    await db.commit()
    return {"status": "success", "message": "Password modificata con successo"}

# ==========================================
# GESTIONE UTENTI (RISERVATA AGLI AMMINISTRATORI)
# ==========================================

@router.get("/users", response_model=List[UserResponse])
async def list_users(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Restituisce la lista di tutti gli utenti (solo per admin)."""
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()

@router.post("/users", response_model=UserResponse)
async def create_user(
    data: CreateUserRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuovo utente (solo per admin)."""
    username = data.username.strip()
    
    # Check if username already exists
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"L'utente '{username}' esiste già."
        )

    new_user = User(
        username=username,
        hashed_password=hash_password(data.password),
        is_admin=data.is_admin,
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un utente (solo per admin, non è consentito eliminare se stessi)."""
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Non puoi eliminare il tuo stesso account amministratore."
        )

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")

    await db.delete(user)
    await db.commit()
    return {"status": "success", "message": f"Utente '{user.username}' eliminato"}

@router.put("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    data: ResetUserPasswordRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Reimposta la password di un utente (solo per admin)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")

    user.hashed_password = hash_password(data.new_password)
    user.failed_attempts = 0
    user.locked_until = None
    await db.commit()
    return {"status": "success", "message": f"Password reimpostata per l'utente '{user.username}'"}
