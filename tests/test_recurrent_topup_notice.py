"""Уведомление об автопополнении: баланс после зачисления и кнопка пополнить."""

from __future__ import annotations

from app.localization.texts import get_texts
from app.services.recurrent_payment_service import (
    _build_extend_keyboard,
    projected_balance_kopeks,
)


def test_projected_balance_adds_the_topup_only_after_the_charge_is_paid():
    # Вебхук зачисляет деньги позже сообщения. Пока YooKassa уже ответила paid,
    # в тексте должен быть баланс после этого пополнения, а не старый.
    assert projected_balance_kopeks(15000, 5000, paid=True) == 20000
    assert projected_balance_kopeks(15000, 5000, paid=False) == 15000


def _targets(keyboard) -> list[str]:
    found: list[str] = []
    for row in keyboard.inline_keyboard:
        for button in row:
            if button.callback_data:
                found.append(button.callback_data)
            web_app = getattr(button, 'web_app', None)
            url = getattr(web_app, 'url', None) if web_app is not None else None
            if url:
                found.append(url)
            if button.url:
                found.append(button.url)
    return found


def test_recurrent_notice_keyboard_offers_extend_and_topup():
    keyboard = _build_extend_keyboard(get_texts('ru'), subscription_id=7)
    targets = _targets(keyboard)

    assert len(keyboard.inline_keyboard) == 2
    assert any('balance' in target for target in targets)
