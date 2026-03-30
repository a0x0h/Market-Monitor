import asyncio
import logging
from io import BytesIO

from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

logger = logging.getLogger(__name__)

# Telegram caption limit is 1024 chars, message limit is 4096
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 10] + "\n…"


class TelegramSender:
    def __init__(self, bot: Bot, channel_id: str) -> None:
        self.bot = bot
        self.channel_id = channel_id

    async def _retry(self, coro_fn, retries: int = 3):
        """Wrap a coroutine with retry + flood-wait handling."""
        for attempt in range(retries):
            try:
                return await coro_fn()
            except TelegramRetryAfter as e:
                wait = e.retry_after + 1
                logger.warning(f"Flood wait: {wait}s")
                await asyncio.sleep(wait)
            except TelegramBadRequest as e:
                logger.error(f"Bad request: {e}")
                return None
            except Exception as e:
                logger.warning(f"Send attempt {attempt + 1} failed: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return None

    async def send_text(self, text: str, pin: bool = False) -> None:
        trimmed = _trim(text, MESSAGE_LIMIT)

        async def _do():
            return await self.bot.send_message(
                chat_id=self.channel_id,
                text=trimmed,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        result = await self._retry(_do)
        if pin and result:
            try:
                await self.bot.pin_chat_message(chat_id=self.channel_id, message_id=result.message_id)
            except Exception as e:
                logger.warning(f"Failed to pin text message: {e}")

    async def send_photo(self, image_path_or_bytes, caption: str, pin: bool = False) -> None:
        from aiogram.types import BufferedInputFile, FSInputFile
        trimmed_caption = _trim(caption, CAPTION_LIMIT)
        
        if isinstance(image_path_or_bytes, str):
            file = FSInputFile(image_path_or_bytes)
        else:
            file = BufferedInputFile(image_path_or_bytes, filename="tweet.jpg")

        async def _do():
            return await self.bot.send_photo(
                chat_id=self.channel_id,
                photo=file,
                caption=trimmed_caption,
                parse_mode="HTML",
            )

        result = await self._retry(_do)

        if pin and result:
            try:
                await self.bot.pin_chat_message(chat_id=self.channel_id, message_id=result.message_id)
            except Exception as e:
                logger.warning(f"Failed to pin photo message: {e}")

        # If photo send fails (e.g. invalid image), fall back to text
        if result is None:
            await self.send_text(trimmed_caption, pin=pin)

    async def send_video(self, video_path_or_bytes, caption: str, pin: bool = False) -> None:
        """Send a video file. Falls back to send_text on failure."""
        from aiogram.types import BufferedInputFile, FSInputFile
        trimmed = _trim(caption, CAPTION_LIMIT)
        
        if isinstance(video_path_or_bytes, str):
            file = FSInputFile(video_path_or_bytes, filename="video.mp4")
        else:
            file = BufferedInputFile(video_path_or_bytes, filename="video.mp4")

        async def _do():
            return await self.bot.send_video(
                chat_id=self.channel_id,
                video=file,
                caption=trimmed,
                parse_mode="HTML",
                supports_streaming=True,
            )

        result = await self._retry(_do)
        
        if pin and result:
            try:
                await self.bot.pin_chat_message(chat_id=self.channel_id, message_id=result.message_id)
            except Exception as e:
                logger.warning(f"Failed to pin video message: {e}")
                
        if result is None:
            await self.send_text(trimmed, pin=pin)

    async def send_photo_and_video(self, photo_bytes, video_path_or_bytes, caption: str, pin: bool = False) -> None:
        from aiogram.types import InputMediaPhoto, InputMediaVideo, BufferedInputFile, FSInputFile

        trimmed_caption = _trim(caption, CAPTION_LIMIT)
        
        photo_file = BufferedInputFile(photo_bytes, filename="tweet.jpg")
        if isinstance(video_path_or_bytes, str):
            video_file = FSInputFile(video_path_or_bytes, filename="video.mp4")
        else:
            video_file = BufferedInputFile(video_path_or_bytes, filename="video.mp4")

        media = [
            InputMediaPhoto(media=photo_file, caption=trimmed_caption, parse_mode="HTML"),
            InputMediaVideo(media=video_file, supports_streaming=True)
        ]

        async def _do():
            return await self.bot.send_media_group(
                chat_id=self.channel_id,
                media=media,
            )

        result_messages = await self._retry(_do)
        
        if pin and result_messages:
            try:
                # pin the first message in the group
                await self.bot.pin_chat_message(chat_id=self.channel_id, message_id=result_messages[0].message_id)
            except Exception as e:
                logger.warning(f"Failed to pin album message: {e}")
                
        if not result_messages:
            # fallback if media group fails
            await self.send_photo(photo_bytes, caption, pin=pin)

    async def send_media_group(self, images: list[bytes], caption: str) -> None:
        """Send up to 10 images as a media group (album)."""
        from aiogram.types import InputMediaPhoto

        if not images:
            await self.send_text(caption)
            return

        trimmed_caption = _trim(caption, CAPTION_LIMIT)
        media = []
        for i, img_bytes in enumerate(images[:10]):
            file = BufferedInputFile(img_bytes, filename=f"img{i}.jpg")
            media.append(
                InputMediaPhoto(
                    media=file,
                    caption=trimmed_caption if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None,
                )
            )

        async def _do():
            await self.bot.send_media_group(
                chat_id=self.channel_id,
                media=media,
            )

        result = await self._retry(_do)
        if result is None:
            await self.send_text(trimmed_caption)
