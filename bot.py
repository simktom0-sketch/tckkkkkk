# -*- coding: utf-8 -*-

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import os
import re
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MODERATION_CHAT_ID = os.getenv("MODERATION_CHAT_ID", "")
MODERATION_THREAD_ID = os.getenv("MODERATION_THREAD_ID", "").strip()
NEWS_URL = os.getenv("NEWS_URL", "").strip()
BRAND_NAME = os.getenv("BRAND_NAME", "точка")
BRAND_URL = os.getenv("BRAND_URL", "https://t.me/TochkaPoeta")
ADMIN_IDS = {
    int(admin_id)
    for admin_id in os.getenv("ADMIN_IDS", "6100749072,1011945865").split(",")
    if admin_id.strip().isdigit()
}
DATA_DIR = os.getenv("DATA_DIR", ".").strip() or "."
SETTINGS_FILE = os.path.join(DATA_DIR, "bot_settings.json")

DECORATION_LINE = "﹌" * 17
TELEGRAM_URL_RE = re.compile(r"^(https?://)?(t\.me|telegram\.me)/[A-Za-z0-9_]{5,32}/?$")

router = Router()
BOT_VERSION = "2026-09-13-admin-tournaments-news"


class PoemForm(StatesGroup):
    poem = State()
    author = State()
    channel = State()
    consent = State()
    preview = State()


class TournamentForm(StatesGroup):
    consent = State()
    author = State()
    channel = State()
    works = State()
    preview = State()


@dataclass(frozen=True)
class FormStep:
    field: str
    prompt: str
    validate: Callable[[str], bool]
    normalize: Callable[[str], str]
    error: str
    empty_error: str
    next_state: str
    next_prompt: str | None = None
    next_markup: Callable[[], InlineKeyboardMarkup] | None = None
    retry_markup: Callable[[], InlineKeyboardMarkup] | None = None
    on_success: Callable[[Message, FSMContext], Awaitable[None]] | None = None


def mk(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def load_settings() -> dict[str, bool]:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            settings = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"tournament_enabled": True}

    return {"tournament_enabled": bool(settings.get("tournament_enabled", True))}


def save_settings(settings: dict[str, bool]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def is_tournament_enabled() -> bool:
    return load_settings()["tournament_enabled"]


def set_tournament_enabled(enabled: bool) -> None:
    save_settings({"tournament_enabled": enabled})


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def menu_markup() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Предложить стих", callback_data="suggest_poem")],
    ]
    if is_tournament_enabled():
        rows.append([InlineKeyboardButton(text="Подать заявку на Турнир", callback_data="apply_tournament")])
    rows.append([InlineKeyboardButton(text="Новости", url=NEWS_URL or "https://www.tochkapoetry.ru/news")])
    return mk(*rows)


def admin_markup() -> InlineKeyboardMarkup:
    if is_tournament_enabled():
        action_text = "Выключить турниры"
        action_callback = "admin_tournament_off"
        status = "Турниры включены"
    else:
        action_text = "Включить турниры"
        action_callback = "admin_tournament_on"
        status = "Турниры выключены"

    return mk(
        [InlineKeyboardButton(text=status, callback_data="admin_status")],
        [InlineKeyboardButton(text=action_text, callback_data=action_callback)],
        [InlineKeyboardButton(text="В главное меню", callback_data="main_menu")],
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Турниры")]],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел админки",
    )


def tournament_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Включить турнир"),
                KeyboardButton(text="Выключить турнир"),
            ],
            [KeyboardButton(text="Админка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Управление турнирами",
    )


def channel_prompt() -> InlineKeyboardMarkup:
    return mk(
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_channel")],
        [InlineKeyboardButton(text="Отменить", callback_data="cancel_form")],
    )


def consent_choice() -> InlineKeyboardMarkup:
    return mk(
        [
            InlineKeyboardButton(text="Согласен", callback_data="agree"),
            InlineKeyboardButton(text="Не согласен", callback_data="disagree"),
        ]
    )


def preview_actions() -> InlineKeyboardMarkup:
    return mk(
        [InlineKeyboardButton(text="Отправить на публикацию", callback_data="send_to_moderation")],
        [InlineKeyboardButton(text="Отменить", callback_data="cancel_form")],
    )


def tournament_preview_actions() -> InlineKeyboardMarkup:
    return mk(
        [InlineKeyboardButton(text="Отправить заявку", callback_data="send_tournament_application")],
        [InlineKeyboardButton(text="Отменить", callback_data="cancel_form")],
    )


def home_button() -> InlineKeyboardMarkup:
    return mk(
        [InlineKeyboardButton(text="В главное меню", callback_data="main_menu")]
    )


def anchor(label: str, href: str) -> str:
    return f'<a href="{escape(href, quote=True)}">{escape(label)}</a>'


def clean_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def normalize_text_input(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_multiline_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def normalize_channel_link(value: str) -> str:
    text = normalize_text_input(value)
    if not text:
        return ""
    if text.startswith("@"):
        text = f"https://t.me/{text[1:]}"
    elif text.startswith("telegram.me/"):
        text = f"https://{text}"
    elif text.startswith("t.me/"):
        text = f"https://{text}"
    return text.rstrip("/")


def is_valid_channel_link(value: str) -> bool:
    return bool(TELEGRAM_URL_RE.fullmatch(value))


def telegram_name_link(user: Any) -> str:
    name = clean_text(user.full_name, "Пользователь")
    if user.username:
        return anchor(name, f"https://t.me/{user.username}")
    return anchor(name, f"tg://user?id={user.id}")


def credit_name(author: str, channel: str | None) -> str:
    name = clean_text(author, "Без автора")
    channel = clean_text(channel)
    if channel:
        return anchor(name, channel)
    return escape(name)


def render_publish_copy(data: dict[str, Any]) -> str:
    poem = escape(clean_text(data.get("poem")))
    author = credit_name(str(data.get("author", "")), data.get("channel"))
    brand = anchor(BRAND_NAME, BRAND_URL)
    return "\n\n".join((DECORATION_LINE, poem, f"© {author} | {brand}"))


def render_moderation_note(data: dict[str, Any], user: Any) -> str:
    telegram = f"@{escape(user.username)}" if user.username else "не указан"
    channel = clean_text(data.get("channel"), "не указан")
    rows = [
        "📝 <b>Новая заявка на публикацию</b>",
        "",
        f"<b>Отправитель:</b> {telegram_name_link(user)}",
        f"<b>Telegram:</b> {telegram}",
        f"<b>ID:</b> <code>{user.id}</code>",
        f"<b>Канал автора:</b> {escape(channel)}",
    ]
    return "\n".join(rows)


def render_tournament_preview(data: dict[str, Any], user: Any) -> str:
    author = credit_name(str(data.get("author", "")), data.get("channel"))
    works = escape(clean_text(data.get("works")))
    rows = [
        f"<b>Ник в тг:</b> {telegram_name_link(user)}",
        f"<b>Имя или псевдоним:</b> {author}",
        "",
        f"<b>Тексты:</b>\n{works}",
    ]
    return "\n".join(rows)


def render_tournament_moderation(data: dict[str, Any], user: Any) -> str:
    return "🏆 <b>Новая заявка на Турнир</b>\n\n" + render_tournament_preview(data, user)


def moderation_destination() -> dict[str, Any]:
    destination: dict[str, Any] = {"chat_id": MODERATION_CHAT_ID}
    if MODERATION_THREAD_ID:
        destination["message_thread_id"] = int(MODERATION_THREAD_ID)
    return destination


async def show_main_menu(message: Message) -> None:
    await message.answer("Главное меню", reply_markup=menu_markup())


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return

    await state.clear()
    await show_main_menu(message)


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Главное меню", reply_markup=menu_markup())
    await callback.answer()


async def show_admin_panel(message: Message) -> None:
    status = "включены" if is_tournament_enabled() else "выключены"
    await message.answer(
        f"Админка\n\nТурниры сейчас: <b>{status}</b>\n\nВыберите раздел:",
        reply_markup=admin_keyboard(),
    )


@router.message(F.text.in_({"/admin", "Админка"}))
async def admin_command(message: Message) -> None:
    if message.chat.type != "private":
        return
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админке.")
        return

    await show_admin_panel(message)


@router.message(F.text == "/version")
async def version_command(message: Message) -> None:
    if message.chat.type != "private":
        return

    await message.answer(f"Версия бота: <code>{BOT_VERSION}</code>")


@router.message(F.text == "Турниры")
async def tournament_admin_section(message: Message) -> None:
    if message.chat.type != "private":
        return
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админке.")
        return

    status = "включены" if is_tournament_enabled() else "выключены"
    await message.answer(
        f"Управление турнирами\n\nТурниры сейчас: <b>{status}</b>",
        reply_markup=tournament_admin_keyboard(),
    )


@router.message(F.text.in_({"/tournament_on", "Включить турнир"}))
async def tournament_on_command(message: Message) -> None:
    if message.chat.type != "private":
        return
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админке.")
        return

    set_tournament_enabled(True)
    await message.answer("Турнир включен. Кнопка заявки появится в главном меню.", reply_markup=tournament_admin_keyboard())


@router.message(F.text.in_({"/tournament_off", "Выключить турнир"}))
async def tournament_off_command(message: Message) -> None:
    if message.chat.type != "private":
        return
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админке.")
        return

    set_tournament_enabled(False)
    await message.answer("Турнир выключен. Кнопка заявки скрыта из главного меню.", reply_markup=tournament_admin_keyboard())


@router.callback_query(F.data.in_({"admin_tournament_on", "admin_tournament_off", "admin_status"}))
async def admin_callbacks(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    if callback.data == "admin_tournament_on":
        set_tournament_enabled(True)
        await callback.answer("Турниры включены.")
    elif callback.data == "admin_tournament_off":
        set_tournament_enabled(False)
        await callback.answer("Турниры выключены.")
    else:
        await callback.answer()

    status = "включены" if is_tournament_enabled() else "выключены"
    await callback.message.edit_text(
        f"Админка\n\nТурниры сейчас: <b>{status}</b>",
        reply_markup=admin_markup(),
    )


@lru_cache(maxsize=1)
def build_form_steps() -> dict[str, FormStep]:
    return {
        PoemForm.poem.state: FormStep(
            field="poem",
            prompt="Отправьте текст одного произведения.",
            validate=lambda value: len(value) >= 10,
            normalize=normalize_multiline_text,
            error="Текст слишком короткий. Отправьте, пожалуйста, полное произведение.",
            empty_error="Отправьте текст стихотворения обычным сообщением.",
            next_state=PoemForm.author.state,
            next_prompt="Укажите имя или псевдоним, под которым стих будет опубликован.",
        ),
        PoemForm.author.state: FormStep(
            field="author",
            prompt="Укажите имя или псевдоним, под которым стих будет опубликован.",
            validate=lambda value: len(value) >= 2,
            normalize=normalize_text_input,
            error="Имя или псевдоним слишком короткие. Попробуйте еще раз.",
            empty_error="Укажите имя или псевдоним сообщением, а не пустой строкой.",
            next_state=PoemForm.channel.state,
            next_prompt="Отправьте ссылку на личный Telegram-канал, если он есть.",
            next_markup=channel_prompt,
        ),
        PoemForm.channel.state: FormStep(
            field="channel",
            prompt="Отправьте ссылку на личный Telegram-канал, если он есть.",
            validate=is_valid_channel_link,
            normalize=normalize_channel_link,
            error="Пришлите ссылку в формате https://t.me/channel, @channel или нажмите «Пропустить».",
            empty_error="Пришлите ссылку на канал или нажмите «Пропустить».",
            next_state=PoemForm.consent.state,
            retry_markup=channel_prompt,
            on_success=ask_consent,
        ),
        TournamentForm.author.state: FormStep(
            field="author",
            prompt="Укажите имя или псевдоним, под которым вы собираетесь принять участие.",
            validate=lambda value: len(value) >= 2,
            normalize=normalize_text_input,
            error="Имя или псевдоним слишком короткие. Попробуйте еще раз.",
            empty_error="Укажите имя или псевдоним сообщением, а не пустой строкой.",
            next_state=TournamentForm.channel.state,
            next_prompt="Отправьте ссылку на личный Telegram-канал, если он есть.",
            next_markup=channel_prompt,
        ),
        TournamentForm.channel.state: FormStep(
            field="channel",
            prompt="Отправьте ссылку на личный Telegram-канал, если он есть.",
            validate=is_valid_channel_link,
            normalize=normalize_channel_link,
            error="Пришлите ссылку в формате https://t.me/channel, @channel или нажмите «Пропустить».",
            empty_error="Пришлите ссылку на канал или нажмите «Пропустить».",
            next_state=TournamentForm.works.state,
            next_prompt=(
                "Отправьте одно или несколько ваших лучших произведений. "
                "Важно, чтобы их объем был не более одного сообщения."
            ),
            retry_markup=channel_prompt,
        ),
        TournamentForm.works.state: FormStep(
            field="works",
            prompt=(
                "Отправьте одно или несколько ваших лучших произведений. "
                "Важно, чтобы их объем был не более одного сообщения."
            ),
            validate=lambda value: len(value) >= 10,
            normalize=normalize_multiline_text,
            error="Текст слишком короткий. Отправьте, пожалуйста, одно или несколько произведений одним сообщением.",
            empty_error="Отправьте произведения обычным сообщением.",
            next_state=TournamentForm.preview.state,
            on_success=show_tournament_preview,
        ),
    }


def reply_markup_from(factory: Callable[[], InlineKeyboardMarkup] | None) -> InlineKeyboardMarkup | None:
    return factory() if callable(factory) else None


async def advance_form_step(message: Message, state: FSMContext) -> bool:
    step = build_form_steps().get(await state.get_state() or "")
    if not step:
        return False

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer(step.empty_error, reply_markup=reply_markup_from(step.retry_markup))
        return True

    value = step.normalize(raw_text)
    if not step.validate(value):
        await message.answer(step.error, reply_markup=reply_markup_from(step.retry_markup))
        return True

    await state.update_data(**{step.field: value})
    await state.set_state(step.next_state)

    if step.on_success:
        await step.on_success(message, state)
    elif step.next_prompt:
        await message.answer(step.next_prompt, reply_markup=reply_markup_from(step.next_markup))
    return True


@router.callback_query(F.data == "suggest_poem")
async def suggest_poem(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PoemForm.poem)
    await callback.message.edit_text(build_form_steps()[PoemForm.poem.state].prompt)
    await callback.answer()


@router.callback_query(F.data == "apply_tournament")
async def apply_tournament(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_tournament_enabled():
        await callback.answer("Прием заявок на турнир сейчас закрыт.", show_alert=True)
        await callback.message.edit_text("Главное меню", reply_markup=menu_markup())
        return

    await state.clear()
    await state.set_state(TournamentForm.consent)
    await callback.message.edit_text(
        "Подтвердите согласие на обработку персональных данных:\n\n"
        "Вы отправляете заявку добровольно, даете согласие на публикацию в наших "
        "ресурсах ваших произведений и подтверждаете согласие на обработку и "
        "использование предоставленных данных исключительно для публикации "
        "произведений и оглашения результатов мероприятия.",
        reply_markup=consent_choice(),
    )
    await callback.answer()


@router.callback_query(PoemForm.channel, F.data == "skip_channel")
async def skip_channel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(channel=None)
    await callback.answer()
    await ask_consent(callback.message, state)


@router.callback_query(TournamentForm.channel, F.data == "skip_channel")
async def skip_tournament_channel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(channel=None)
    await state.set_state(TournamentForm.works)
    await callback.message.answer(
        "Отправьте одно или несколько ваших лучших произведений. "
        "Важно, чтобы их объем был не более одного сообщения."
    )
    await callback.answer()


async def ask_consent(message: Message, state: FSMContext) -> None:
    await state.set_state(PoemForm.consent)
    await message.answer(
        "Подтвердите согласие на публикацию:\n\n"
        "Вы отправляете произведение добровольно, даете согласие на его публикацию "
        "в наших ресурсах и подтверждаете согласие на обработку и использование "
        "предоставленных данных исключительно для публикации произведения.",
        reply_markup=consent_choice(),
    )


@router.callback_query(PoemForm.consent, F.data == "disagree")
async def disagree(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Форма отменена.", reply_markup=home_button())
    await callback.answer()


@router.callback_query(TournamentForm.consent, F.data == "disagree")
async def tournament_disagree(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявка на турнир отменена.", reply_markup=home_button())
    await callback.answer()


@router.callback_query(PoemForm.consent, F.data == "agree")
async def agree(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(PoemForm.preview)
    await callback.message.answer(
        "<b>Проверьте превью перед отправкой:</b>\n\n" + render_publish_copy(data),
        reply_markup=preview_actions(),
    )
    await callback.answer()


@router.callback_query(TournamentForm.consent, F.data == "agree")
async def tournament_agree(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TournamentForm.author)
    await callback.message.answer(
        "Укажите имя или псевдоним, под которым вы собираетесь принять участие."
    )
    await callback.answer()


async def show_tournament_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await message.answer(
        "<b>Проверьте вашу заявку перед отправкой:</b>\n\n"
        + render_tournament_preview(data, message.from_user),
        reply_markup=tournament_preview_actions(),
    )


@router.callback_query(F.data == "cancel_form")
async def cancel_form(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Форма отменена.", reply_markup=home_button())
    await callback.answer()


@router.callback_query(TournamentForm.preview, F.data == "send_tournament_application")
async def send_tournament_application(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not MODERATION_CHAT_ID:
        await callback.answer("Чат модерации не настроен.", show_alert=True)
        return

    data = await state.get_data()
    await bot.send_message(
        **moderation_destination(),
        text=render_tournament_moderation(data, callback.from_user),
        disable_web_page_preview=True,
    )
    await state.clear()
    await callback.message.edit_text(
        "Спасибо! Заявка на турнир отправлена на проверку.",
        reply_markup=home_button(),
    )
    await callback.answer()


@router.callback_query(PoemForm.preview, F.data == "send_to_moderation")
async def send_to_moderation(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not MODERATION_CHAT_ID:
        await callback.answer("Чат модерации не настроен.", show_alert=True)
        return

    data = await state.get_data()
    await bot.send_message(
        **moderation_destination(),
        text=render_moderation_note(data, callback.from_user),
        disable_web_page_preview=True,
    )
    await bot.send_message(
        **moderation_destination(),
        text=render_publish_copy(data),
        disable_web_page_preview=True,
    )
    await state.clear()
    await callback.message.edit_text(
        "Спасибо! Произведение отправлено на модерацию.",
        reply_markup=home_button(),
    )
    await callback.answer()


@router.message()
async def route_private_message(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return

    if message.text and message.text.startswith("/"):
        return

    if await advance_form_step(message, state):
        return

    await message.answer("Выберите действие в главном меню.", reply_markup=menu_markup())

async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting bot version %s", BOT_VERSION)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

