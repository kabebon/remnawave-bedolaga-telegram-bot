"""Публичное сравнение тарифов и его редактор в админке.

Маршруты живут в своём файле, чтобы обновление ``admin_tariffs.py`` от автора
не затирало редактор. Данные — JSON в system_settings, без миграции.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.tariff import get_all_tariffs
from app.database.models import User
from app.localization.texts import get_texts
from app.services.tariff_comparison import (
    AdminView,
    ComparisonCell,
    ComparisonConfig,
    ComparisonRow,
    PublicView,
    load_admin_view,
    load_public_view,
    normalize_lang,
    sanitize_config,
    save_config,
)

from ..dependencies import get_cabinet_db, require_permission


router = APIRouter(tags=['Tariff comparison'])


class ComparisonCellIn(BaseModel):
    kind: str = 'empty'
    text: str = ''


class ComparisonRowIn(BaseModel):
    id: str = ''
    labels: dict[str, str] = Field(default_factory=dict)
    cells: dict[str, ComparisonCellIn] = Field(default_factory=dict)


class ComparisonUpdateRequest(BaseModel):
    enabled: bool = True
    show_auto: bool = True
    rows: list[ComparisonRowIn] = Field(default_factory=list)


def _to_config(payload: ComparisonUpdateRequest) -> ComparisonConfig:
    return ComparisonConfig(
        enabled=payload.enabled,
        show_auto=payload.show_auto,
        rows=[
            ComparisonRow(
                id=row.id,
                labels=row.labels,
                cells={key: ComparisonCell(kind=cell.kind, text=cell.text) for key, cell in row.cells.items()},
            )
            for row in payload.rows
        ],
    )


@router.get('/tariff-comparison', response_model=PublicView)
async def get_tariff_comparison(
    lang: str = Query('ru'),
    db: AsyncSession = Depends(get_cabinet_db),
) -> PublicView:
    """Сравнение для экрана выбора. Без авторизации: это витрина, не личные данные."""
    tariffs = await get_all_tariffs(db, include_inactive=False)
    texts = get_texts(normalize_lang(lang))
    return await load_public_view(db, tariffs, texts)


@router.get('/admin/tariff-comparison', response_model=AdminView)
async def get_admin_tariff_comparison(
    admin: User = Depends(require_permission('tariffs:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> AdminView:
    del admin
    return await load_admin_view(db)


@router.put('/admin/tariff-comparison', response_model=AdminView)
async def update_admin_tariff_comparison(
    payload: ComparisonUpdateRequest,
    admin: User = Depends(require_permission('tariffs:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> AdminView:
    del admin
    tariffs = await get_all_tariffs(db, include_inactive=True)
    known_ids = {tariff.id for tariff in tariffs}
    config = sanitize_config(_to_config(payload), known_tariff_ids=known_ids)
    if payload.rows and not config.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Each comparison row needs a label',
        )
    await save_config(db, config)
    return await load_admin_view(db)
