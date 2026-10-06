"""Сравнение тарифов: факты из карточек тарифа плюс строки +/- из админки.

Хранится одной JSON-записью в ``system_settings`` (ключ ``tariff_comparison``).
Отдельной таблицы и миграции нет: при обновлении от автора Alembic не
получает вторую голову, а этот модуль почти не пересекается с его файлами.

Автостроки (трафик, устройства, серверы, цена) считаются из тарифа при каждом
показе. Админ правит только свои строки: «да», «нет» или короткий текст.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.system_setting import get_setting_value, upsert_system_setting
from app.database.crud.tariff import get_all_tariffs
from app.localization.texts import Texts


logger = structlog.get_logger(__name__)

SETTING_KEY = 'tariff_comparison'
SETTING_DESCRIPTION = 'Custom tariff comparison rows (fork overlay, JSON)'

MAX_ROWS = 20
MAX_LABEL = 80
MAX_CELL_TEXT = 40
_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,32}$')
_LANGS = ('ru', 'en', 'ua', 'zh', 'fa')
_LANG_ALIASES = {'uk': 'ua'}


class ComparisonCell(BaseModel):
    kind: str = 'empty'
    text: str = ''


class ComparisonRow(BaseModel):
    id: str
    labels: dict[str, str] = Field(default_factory=dict)
    cells: dict[str, ComparisonCell] = Field(default_factory=dict)


class ComparisonConfig(BaseModel):
    enabled: bool = True
    show_auto: bool = True
    rows: list[ComparisonRow] = Field(default_factory=list)


class PublicColumn(BaseModel):
    id: int
    name: str
    is_highlighted: bool = False


class PublicRow(BaseModel):
    id: str
    label: str
    custom: bool
    cells: dict[str, ComparisonCell]


class PublicView(BaseModel):
    enabled: bool
    title: str = ''
    columns: list[PublicColumn] = Field(default_factory=list)
    rows: list[PublicRow] = Field(default_factory=list)


class AdminTariffColumn(BaseModel):
    id: int
    name: str
    is_active: bool
    is_highlighted: bool = False


class AdminView(BaseModel):
    enabled: bool
    show_auto: bool
    rows: list[ComparisonRow]
    tariffs: list[AdminTariffColumn]


def normalize_lang(language: str | None) -> str:
    code = (language or 'ru').split('-')[0].lower()
    code = _LANG_ALIASES.get(code, code)
    return code if code in _LANGS else 'ru'


def empty_config() -> ComparisonConfig:
    return ComparisonConfig()


def parse_config(raw: str | None) -> ComparisonConfig:
    if not raw:
        return empty_config()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('tariff_comparison JSON is invalid, using defaults')
        return empty_config()
    if not isinstance(payload, dict):
        return empty_config()
    try:
        return ComparisonConfig.model_validate(payload)
    except Exception:
        logger.warning('tariff_comparison JSON does not match the schema, using defaults')
        return empty_config()


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or '').replace('\x00', '').strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:limit]


def sanitize_config(payload: ComparisonConfig, *, known_tariff_ids: set[int]) -> ComparisonConfig:
    """Приводит присланную админкой конфигурацию к тому, что можно хранить."""
    rows: list[ComparisonRow] = []
    seen_ids: set[str] = set()
    for row in payload.rows[:MAX_ROWS]:
        labels = {
            lang: _clean_text(text, MAX_LABEL)
            for lang, text in row.labels.items()
            if lang in _LANGS and _clean_text(text, MAX_LABEL)
        }
        if not labels:
            continue
        row_id = row.id.strip()
        if not _ID_RE.match(row_id) or row_id in seen_ids:
            row_id = uuid.uuid4().hex[:12]
        seen_ids.add(row_id)
        cells: dict[str, ComparisonCell] = {}
        for key, cell in row.cells.items():
            if not str(key).isdigit():
                continue
            tariff_id = int(key)
            if tariff_id not in known_tariff_ids:
                continue
            kind = cell.kind if cell.kind in {'yes', 'no', 'text'} else 'empty'
            text = _clean_text(cell.text, MAX_CELL_TEXT) if kind == 'text' else ''
            if kind == 'text' and not text:
                kind = 'empty'
            cells[str(tariff_id)] = ComparisonCell(kind=kind, text=text)
        rows.append(ComparisonRow(id=row_id, labels=labels, cells=cells))
    return ComparisonConfig(enabled=payload.enabled, show_auto=payload.show_auto, rows=rows)


def pick_label(labels: dict[str, str], language: str) -> str:
    lang = normalize_lang(language)
    if labels.get(lang):
        return labels[lang]
    if labels.get('ru'):
        return labels['ru']
    for value in labels.values():
        if value:
            return value
    return ''


def _text_cell(text: str) -> ComparisonCell:
    return ComparisonCell(kind='text', text=text)


def _empty_cell() -> ComparisonCell:
    return ComparisonCell(kind='empty', text='')


def _squad_count(tariff: Any) -> int:
    squads = getattr(tariff, 'allowed_squads', None) or []
    return len(squads) if isinstance(squads, list) else 0


def _price_cell(tariff: Any, texts: Texts) -> ComparisonCell:
    free = texts.t('TARIFF_COMPARISON_FREE', 'Бесплатно')
    if getattr(tariff, 'is_daily', False):
        price = int(getattr(tariff, 'daily_price_kopeks', 0) or 0)
        if price <= 0:
            return _text_cell(free)
        formatted = settings.format_price(price)
        return _text_cell(texts.t('TARIFF_COMPARISON_PER_DAY', '{price}/день').format(price=formatted))

    prices = getattr(tariff, 'period_prices', None) or {}
    amounts: list[int] = []
    if isinstance(prices, dict):
        for raw in prices.values():
            try:
                amounts.append(int(raw))
            except (TypeError, ValueError):
                continue
    if not amounts:
        return _empty_cell()
    amount = min(amounts)
    if amount <= 0:
        return _text_cell(free)
    formatted = settings.format_price(amount)
    if len(amounts) > 1:
        return _text_cell(texts.t('TARIFF_COMPARISON_PRICE_FROM', 'от {price}').format(price=formatted))
    return _text_cell(formatted)


def _auto_rows(tariffs: list[Any], texts: Texts) -> list[PublicRow]:
    traffic_cells: dict[str, ComparisonCell] = {}
    device_cells: dict[str, ComparisonCell] = {}
    server_cells: dict[str, ComparisonCell] = {}
    price_cells: dict[str, ComparisonCell] = {}
    unlimited = texts.t('TARIFF_COMPARISON_UNLIMITED', '∞')
    for tariff in tariffs:
        key = str(tariff.id)
        traffic_gb = int(getattr(tariff, 'traffic_limit_gb', 0) or 0)
        if traffic_gb <= 0:
            traffic_cells[key] = _text_cell(unlimited)
        else:
            traffic_cells[key] = _text_cell(
                texts.t('TARIFF_COMPARISON_TRAFFIC_VALUE', '{traffic} ГБ').format(traffic=traffic_gb)
            )
        devices = int(getattr(tariff, 'device_limit', 0) or 0)
        device_cells[key] = _text_cell(unlimited if devices <= 0 else str(devices))
        server_cells[key] = _text_cell(str(_squad_count(tariff)))
        price_cells[key] = _price_cell(tariff, texts)

    return [
        PublicRow(
            id='traffic',
            label=texts.t('TARIFF_COMPARISON_ROW_TRAFFIC', 'Трафик'),
            custom=False,
            cells=traffic_cells,
        ),
        PublicRow(
            id='devices',
            label=texts.t('TARIFF_COMPARISON_ROW_DEVICES', 'Устройства'),
            custom=False,
            cells=device_cells,
        ),
        PublicRow(
            id='servers',
            label=texts.t('TARIFF_COMPARISON_ROW_SERVERS', 'Серверы'),
            custom=False,
            cells=server_cells,
        ),
        PublicRow(
            id='price',
            label=texts.t('TARIFF_COMPARISON_ROW_PRICE', 'Цена'),
            custom=False,
            cells=price_cells,
        ),
    ]


def _custom_rows(config: ComparisonConfig, tariffs: list[Any], language: str) -> list[PublicRow]:
    visible = {str(tariff.id) for tariff in tariffs}
    rows: list[PublicRow] = []
    for row in config.rows:
        label = pick_label(row.labels, language)
        if not label:
            continue
        cells = {key: cell for key, cell in row.cells.items() if key in visible}
        rows.append(PublicRow(id=row.id, label=label, custom=True, cells=cells))
    return rows


def build_public_view(tariffs: list[Any], config: ComparisonConfig, texts: Texts) -> PublicView:
    title = texts.t('TARIFF_COMPARISON_TITLE', '📊 Сравнение тарифов')
    if not config.enabled or len(tariffs) < 2:
        return PublicView(enabled=False, title=title)
    columns = [
        PublicColumn(
            id=int(tariff.id),
            name=str(getattr(tariff, 'name', '') or ''),
            is_highlighted=bool(getattr(tariff, 'is_highlighted', False)),
        )
        for tariff in tariffs
    ]
    rows: list[PublicRow] = []
    if config.show_auto:
        rows.extend(_auto_rows(tariffs, texts))
    rows.extend(_custom_rows(config, tariffs, texts.language))
    if not rows:
        return PublicView(enabled=False, title=title, columns=columns)
    return PublicView(enabled=True, title=title, columns=columns, rows=rows)


def _cell_html(cell: ComparisonCell | None) -> str:
    if cell is None or cell.kind in {'', 'empty'}:
        return '—'
    if cell.kind == 'yes':
        return '✅'
    if cell.kind == 'no':
        return '❌'
    return html.escape(cell.text or '—')


def render_telegram(view: PublicView) -> str:
    """Текст сравнения для сообщения бота. Таблицу Telegram не рисует, поэтому строки идут списком."""
    lines = [f'<b>{html.escape(view.title)}</b>', '']
    for row in view.rows:
        lines.append(f'<b>{html.escape(row.label)}</b>')
        for column in view.columns:
            lines.append(f'• {html.escape(column.name)} — {_cell_html(row.cells.get(str(column.id)))}')
        lines.append('')
    text = '\n'.join(lines).strip()
    if len(text) > 3900:
        return text[:3890].rstrip() + '…'
    return text


async def load_config(db: AsyncSession) -> ComparisonConfig:
    raw = await get_setting_value(db, SETTING_KEY)
    return parse_config(raw)


async def is_enabled(db: AsyncSession) -> bool:
    config = await load_config(db)
    return config.enabled


async def save_config(db: AsyncSession, config: ComparisonConfig) -> None:
    raw = json.dumps(config.model_dump(), ensure_ascii=False, separators=(',', ':'))
    await upsert_system_setting(db, SETTING_KEY, raw, description=SETTING_DESCRIPTION)
    await db.commit()


async def load_public_view(db: AsyncSession, tariffs: list[Any], texts: Texts) -> PublicView:
    config = await load_config(db)
    return build_public_view(tariffs, config, texts)


async def load_admin_view(db: AsyncSession) -> AdminView:
    config = await load_config(db)
    tariffs = await get_all_tariffs(db, include_inactive=True)
    columns = [
        AdminTariffColumn(
            id=tariff.id,
            name=tariff.name,
            is_active=bool(tariff.is_active),
            is_highlighted=bool(tariff.is_highlighted),
        )
        for tariff in tariffs
    ]
    columns.sort(key=lambda item: (not item.is_active, item.name.lower(), item.id))
    return AdminView(
        enabled=config.enabled,
        show_auto=config.show_auto,
        rows=config.rows,
        tariffs=columns,
    )
