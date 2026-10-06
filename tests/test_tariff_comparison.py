"""Сравнение тарифов: автостроки из карточки и строки +/-, которые правит админ."""

from __future__ import annotations

from types import SimpleNamespace

from app.localization.texts import get_texts
from app.services.tariff_comparison import (
    ComparisonCell,
    ComparisonConfig,
    ComparisonRow,
    build_public_view,
    parse_config,
    render_telegram,
    sanitize_config,
)


def _tariff(**kwargs):
    base = dict(
        id=1,
        name='Базовый',
        is_highlighted=False,
        traffic_limit_gb=100,
        device_limit=1,
        allowed_squads=['a'],
        is_daily=False,
        daily_price_kopeks=0,
        period_prices={'30': 10000},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_missing_setting_still_compares_real_tariff_fields():
    view = build_public_view(
        [
            _tariff(id=1, name='Базовый', traffic_limit_gb=100, device_limit=1),
            _tariff(id=2, name='Про', traffic_limit_gb=0, device_limit=5, allowed_squads=['a', 'b']),
        ],
        ComparisonConfig(),
        get_texts('ru'),
    )

    assert view.enabled is True
    traffic = next(row for row in view.rows if row.id == 'traffic')
    assert traffic.cells['1'].text == '100 ГБ'
    assert traffic.cells['2'].text == '∞'
    devices = next(row for row in view.rows if row.id == 'devices')
    assert devices.cells['2'].text == '5'


def test_custom_yes_no_row_uses_the_requested_language():
    config = ComparisonConfig(
        rows=[
            ComparisonRow(
                id='whitelist',
                labels={'ru': 'Белые списки', 'en': 'Whitelist'},
                cells={
                    '1': ComparisonCell(kind='no'),
                    '2': ComparisonCell(kind='yes'),
                },
            )
        ]
    )
    view = build_public_view(
        [_tariff(id=1), _tariff(id=2, name='Про')],
        config,
        get_texts('en'),
    )
    custom = next(row for row in view.rows if row.custom)
    assert custom.label == 'Whitelist'

    text = render_telegram(view)
    assert 'Whitelist' in text
    assert '❌' in text
    assert '✅' in text
    assert '<b>Про</b>' not in text  # имя тарифа не должно стать заголовком строки


def test_disabled_or_single_tariff_hides_the_table():
    tariffs = [_tariff(id=1), _tariff(id=2, name='Про')]
    hidden = build_public_view(tariffs, ComparisonConfig(enabled=False), get_texts('ru'))
    assert hidden.enabled is False

    single = build_public_view([_tariff()], ComparisonConfig(), get_texts('ru'))
    assert single.enabled is False


def test_sanitize_drops_unknown_tariffs_and_blank_labels():
    raw = ComparisonConfig(
        rows=[
            ComparisonRow(
                id='ok',
                labels={'ru': '  Поддержка  '},
                cells={'1': ComparisonCell(kind='yes'), '99': ComparisonCell(kind='yes')},
            ),
            ComparisonRow(id='skip', labels={'ru': '   '}, cells={}),
            ComparisonRow(id='ok', labels={'en': 'Support'}, cells={'1': ComparisonCell(kind='text', text='24/7')}),
        ]
    )
    clean = sanitize_config(raw, known_tariff_ids={1})

    assert len(clean.rows) == 2
    assert clean.rows[0].labels['ru'] == 'Поддержка'
    assert '99' not in clean.rows[0].cells
    assert clean.rows[0].id != clean.rows[1].id
    assert clean.rows[1].cells['1'].text == '24/7'


def test_broken_json_falls_back_to_defaults():
    config = parse_config('{not json')
    assert config.enabled is True
    assert config.rows == []
