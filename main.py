import os
import asyncio
import logging
import shutil
from dotenv import load_dotenv
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import FSInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from qobuz_client import QobuzClient
from downloader import QobuzDownloader
import metadata_utils

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
EMAIL = os.getenv("QOBUZ_EMAIL")
PASSWORD = os.getenv("QOBUZ_PASSWORD")
TOKEN_QOBUZ = os.getenv("QOBUZ_TOKEN")
APP_ID = os.getenv("QOBUZ_APP_ID")
APP_SECRET = os.getenv("QOBUZ_APP_SECRET")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "./downloads")
QUALITY = int(os.getenv("DEFAULT_QUALITY", 6))
API_URL = os.getenv("TELEGRAM_API_URL")

TEXTS = {
    "ru": {
        "start": "👋 Привет! Я бот для скачивания музыки из Qobuz.\n\n🔍 Просто отправь мне название песни, альбома или ссылку на Qobuz.\n⚙️ Настройки: /settings\n\nНапример: `Roni Size - Share The Fall`",
        "settings_title": "⚙️ Настройки:",
        "quality_menu": "🔈 Качество звука",
        "lang_menu": "🌐 Язык / Language",
        "lang_select": "Выберите язык / Select language:",
        "quality_select": "Выберите качество загрузки:",
        "searching": "🔍 Ищу в Qobuz...",
        "no_results": "😢 Ничего не найдено.",
        "search_results": "🔍 Результаты для \"{query}\" (Стр. {page}):",
        "loading_track": "🚀 Загружаю трек...",
        "loading_album": "🚀 Загрузка альбома...",
        "album_sent": "✅ Альбом успешно отправлен!",
        "error": "❌ Произошла ошибка: {e}",
        "back": "⬅️",
        "forward": "➡️",
        "downloading": "⏳ Начинаю загрузку {type}...",
        "done": "✅ Загрузка завершена!",
        "lang_updated": "✅ Язык изменен!",
        "quality_updated": "✅ Качество обновлено!",
        "artist_profile": "👤 Артист: {name}\n📀 Альбомов: {count}",
        "discography": "📀 Дискография",
        "albums_of": "Альбомы {name} (Стр. {page}):",
        "category_albums": "📀 Альбомы",
        "category_singles": "🎵 Синглы/EP",
        "category_compilations": "📚 Другое",
        "artist_info": "👤 **{name}**\n\nВсего релизов: {count}",
        "album_info": "💽 **{title}**\n👤 {artist}\n📅 {year}\n\nСписок треков:",
        "download_full_album": "📥 Скачать весь альбом",
        "tracks_list": "🎵 Треки ({page}):"
    },
    "en": {
        "start": "👋 Hi! I'm a Qobuz downloader bot.\n\n🔍 Just send me a track name, album name, or a Qobuz link.\n⚙️ Settings: /settings\n\nExample: `Imagine Dragons Believer`",
        "settings_title": "⚙️ Settings:",
        "quality_menu": "🔈 Audio Quality",
        "lang_menu": "🌐 Language / Язык",
        "lang_select": "Select language / Выберите язык:",
        "quality_select": "Select download quality:",
        "searching": "🔍 Searching Qobuz...",
        "no_results": "😢 No results found.",
        "search_results": "🔍 Results for \"{query}\" (Page {page}):",
        "loading_track": "🚀 Downloading track...",
        "loading_album": "🚀 Downloading album...",
        "album_sent": "✅ Album sent successfully!",
        "error": "❌ Error occurred: {e}",
        "back": "⬅️",
        "forward": "➡️",
        "downloading": "⏳ Downloading {type}...",
        "done": "✅ Download complete!",
        "lang_updated": "✅ Language updated!",
        "quality_updated": "✅ Quality updated!",
        "artist_profile": "👤 Artist: {name}\n📀 Albums: {count}",
        "discography": "📀 Discography",
        "albums_of": "Albums of {name} (Page {page}):",
        "category_albums": "📀 Albums",
        "category_singles": "🎵 Singles/EP",
        "category_compilations": "📚 Other",
        "artist_info": "👤 **{name}**\n\nTotal releases: {count}",
        "album_info": "💽 **{title}**\n👤 {artist}\n📅 {year}\n\nTracks list:",
        "download_full_album": "📥 Download Full Album",
        "tracks_list": "🎵 Tracks ({page}):"
    }
}

class UserSettings:
    def __init__(self, file_path="user_settings.json"):
        self.file_path = file_path
        self.settings = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, "r") as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.settings, f)

    def get_quality(self, user_id):
        return self.settings.get(str(user_id), {}).get("quality", QUALITY)

    def set_quality(self, user_id, quality):
        if str(user_id) not in self.settings:
            self.settings[str(user_id)] = {}
        self.settings[str(user_id)]["quality"] = quality
        self._save()

    def get_lang(self, user_id):
        return self.settings.get(str(user_id), {}).get("lang", "ru")

    def set_lang(self, user_id, lang):
        if str(user_id) not in self.settings:
            self.settings[str(user_id)] = {}
        self.settings[str(user_id)]["lang"] = lang
        self._save()

user_pref = UserSettings()

# Setup custom API server if configured
session = None
if API_URL:
    logger.info(f"Using custom Bot API server: {API_URL}")
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(API_URL, is_local=True)
    )

bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()
q_client = QobuzClient(EMAIL, PASSWORD, token=TOKEN_QOBUZ, app_id=APP_ID, app_secret=APP_SECRET)
downloader = QobuzDownloader(q_client, DOWNLOAD_PATH, QUALITY)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    lang = user_pref.get_lang(message.from_user.id)
    await message.answer(TEXTS[lang]["start"])

@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    lang = user_pref.get_lang(message.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["quality_menu"], callback_data="menu:quality"))
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["lang_menu"], callback_data="menu:lang"))
    await message.answer(TEXTS[lang]["settings_title"], reply_markup=builder.as_markup())

@dp.callback_query(F.data == "menu:quality")
async def cb_menu_quality(callback: types.CallbackQuery):
    lang = user_pref.get_lang(callback.from_user.id)
    qualities = {
        5: "MP3 320 kbps",
        6: "FLAC CD (16-bit/44.1kHz)",
        7: "FLAC Hi-Res (24-bit/up to 96kHz)",
        27: "FLAC Hi-Res (24-bit/above 96kHz)"
    }
    current = user_pref.get_quality(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    for q_id, q_label in qualities.items():
        prefix = "✅ " if q_id == current else ""
        builder.row(types.InlineKeyboardButton(text=f"{prefix}{q_label}", callback_data=f"set_quality:{q_id}"))
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data="menu:main"))
    
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(TEXTS[lang]["quality_select"], reply_markup=builder.as_markup())
    else:
        await callback.message.edit_text(TEXTS[lang]["quality_select"], reply_markup=builder.as_markup())

@dp.callback_query(F.data == "menu:lang")
async def cb_menu_lang(callback: types.CallbackQuery):
    lang = user_pref.get_lang(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🇷🇺 Русский" + (" ✅" if lang == "ru" else ""), callback_data="set_lang:ru"))
    builder.row(types.InlineKeyboardButton(text="🇺🇸 English" + (" ✅" if lang == "en" else ""), callback_data="set_lang:en"))
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data="menu:main"))
    
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(TEXTS[lang]["lang_select"], reply_markup=builder.as_markup())
    else:
        await callback.message.edit_text(TEXTS[lang]["lang_select"], reply_markup=builder.as_markup())

@dp.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: types.CallbackQuery):
    lang = user_pref.get_lang(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["quality_menu"], callback_data="menu:quality"))
    builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["lang_menu"], callback_data="menu:lang"))
    
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(TEXTS[lang]["settings_title"], reply_markup=builder.as_markup())
    else:
        await callback.message.edit_text(TEXTS[lang]["settings_title"], reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("set_quality:"))
async def cb_set_quality(callback: types.CallbackQuery):
    quality = int(callback.data.split(":")[1])
    user_pref.set_quality(callback.from_user.id, quality)
    lang = user_pref.get_lang(callback.from_user.id)
    await callback.answer(TEXTS[lang]["quality_updated"])
    await cb_menu_quality(callback)

@dp.callback_query(F.data.startswith("set_lang:"))
async def cb_set_lang(callback: types.CallbackQuery):
    new_lang = callback.data.split(":")[1]
    user_pref.set_lang(callback.from_user.id, new_lang)
    await callback.answer(TEXTS[new_lang]["lang_updated"])
    await cb_menu_lang(callback)

@dp.message(F.text.regexp(r'https?://(?:play|open)\.qobuz\.com/(?P<type>album|track)/(?P<id>[^/?#]+)'))
async def handle_qobuz_url(message: types.Message):
    pattern = r'https?://(?:play|open)\.qobuz\.com/(?P<type>album|track)/(?P<id>[^/?#]+)'
    match = re.search(pattern, message.text)
    if not match:
        return
    item_type = match.group('type')
    item_id = match.group('id')
    user_q = user_pref.get_quality(message.from_user.id)
    lang = user_pref.get_lang(message.from_user.id)
    downloader.quality = user_q
    
    status_msg = await message.answer(TEXTS[lang]["downloading"].format(type=item_type))
    
    folder_to_clean = None
    try:
        if item_type == 'track':
            file_path, caption, p_info = await downloader.download_track(item_id)
            folder_to_clean = p_info.get("folder_path")
            await message.answer_audio(
                FSInputFile(file_path), 
                caption=caption,
                title=p_info['title'],
                performer=p_info['performer'],
                duration=p_info['duration'],
                thumbnail=FSInputFile(p_info['thumbnail']) if p_info.get('thumbnail') else None
            )
        else:
            album_data = await q_client.get_album(item_id)
            tracks = album_data["tracks"]["items"]
            for i, track in enumerate(tracks):
                file_path, caption, p_info = await downloader.download_track(track["id"], album_data)
                if not folder_to_clean: folder_to_clean = p_info.get("folder_path")
                await message.answer_audio(
                    FSInputFile(file_path), 
                    caption=caption,
                    title=p_info['title'],
                    performer=p_info['performer'],
                    duration=p_info['duration'],
                    thumbnail=FSInputFile(p_info['thumbnail']) if p_info.get('thumbnail') else None
                )
        
        await status_msg.edit_text(TEXTS[lang]["done"])
    except Exception as e:
        logger.error(f"Error downloading: {e}")
        await status_msg.edit_text(TEXTS[lang]["error"].format(e=str(e)))
    finally:
        if folder_to_clean and os.path.exists(folder_to_clean):
            logger.info(f"Cleaning up: {folder_to_clean}")
            shutil.rmtree(folder_to_clean, ignore_errors=True)

@dp.message(F.text)
async def handle_search(message: types.Message):
    query = message.text
    if len(query) < 3 or query.startswith('/'):
        return
    await perform_search(message, query, 0)

async def perform_search(message_or_query: types.Message | types.CallbackQuery, query: str, offset: int):
    # Determine where to send/edit the message
    is_callback = isinstance(message_or_query, types.CallbackQuery)
    target = message_or_query.message if is_callback else message_or_query
    uid = message_or_query.from_user.id
    lang = user_pref.get_lang(uid)
    
    if is_callback:
        await message_or_query.answer()

    status_msg = None
    if not is_callback:
        status_msg = await target.answer(TEXTS[lang]["searching"])
    
    try:
        limit = 5
        artist_results = await q_client.search(query, type="artist", limit=3, offset=offset)
        album_results = await q_client.search(query, type="album", limit=limit, offset=offset)
        track_results = await q_client.search(query, type="track", limit=limit, offset=offset)
        
        artists = artist_results.get("artists", {}).get("items", [])
        albums = album_results.get("albums", {}).get("items", [])
        tracks = track_results.get("tracks", {}).get("items", [])
        
        total_artists = artist_results.get("artists", {}).get("total", 0)
        total_albums = album_results.get("albums", {}).get("total", 0)
        total_tracks = track_results.get("tracks", {}).get("total", 0)
        
        if not artists and not albums and not tracks:
            msg_text = TEXTS[lang]["no_results"]
            if status_msg: await status_msg.edit_text(msg_text)
            else: await target.edit_text(msg_text)
            return

        builder = InlineKeyboardBuilder()
        
        if artists:
            for artist in artists:
                label = f"👤 {artist['name']}"[:64]
                builder.row(types.InlineKeyboardButton(text=label, callback_data=f"ar:{artist['id']}:{offset}:{query}"[:64]))

        if albums:
            for album in albums:
                label = f"💽 {album['artist']['name']} - {album['title']}"[:64]
                builder.row(types.InlineKeyboardButton(text=label, callback_data=f"al:{album['id']}:0:{offset}:{query}"[:64]))
        
        if tracks:
            for track in tracks:
                track_title = metadata_utils.get_title(track)
                label = f"🎵 {track['performer']['name']} - {track_title}"[:64]
                builder.row(types.InlineKeyboardButton(text=label, callback_data=f"dl_track:{track['id']}"))
        
        # Pagination
        nav_buttons = []
        if offset >= limit:
            nav_buttons.append(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=f"sp:{offset-limit}:{query}"[:64]))
        
        if total_albums > offset + limit or total_tracks > offset + limit or total_artists > offset + 3:
            nav_buttons.append(types.InlineKeyboardButton(text=TEXTS[lang]["forward"], callback_data=f"sp:{offset+limit}:{query}"[:64]))
        
        if nav_buttons:
            builder.row(*nav_buttons)
        
        page_num = (offset // limit) + 1
        msg_text = TEXTS[lang]["search_results"].format(query=query, page=page_num)
        
        if status_msg:
            await status_msg.edit_text(msg_text, reply_markup=builder.as_markup())
        else:
            if is_callback and message_or_query.message.photo:
                await message_or_query.message.delete()
                await message_or_query.message.answer(msg_text, reply_markup=builder.as_markup())
            else:
                await target.edit_text(msg_text, reply_markup=builder.as_markup())
            
    except Exception as e:
        logger.error(f"Search error: {e}")
        error_text = TEXTS[lang]["error"].format(e="Search failed")
        if status_msg: 
            await status_msg.edit_text(error_text)
        else: 
            if is_callback and message_or_query.message.photo:
                await message_or_query.message.delete()
                await message_or_query.message.answer(error_text)
            else:
                await target.edit_text(error_text)

@dp.callback_query(F.data.startswith("ar:"))
async def cb_artist(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    artist_id = parts[1]
    search_offset = int(parts[2]) if len(parts) > 2 else 0
    search_query = parts[3] if len(parts) > 3 else "main"
    lang = user_pref.get_lang(callback.from_user.id)
    
    try:
        artist_data = await q_client.get_artist(artist_id)
        name = artist_data["name"]
        albums_count = artist_data.get("albums_count", 0)
        
        text = TEXTS[lang]["artist_info"].format(name=name, count=albums_count)
        
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["category_albums"], callback_data=f"aa:{artist_id}:album:0:{search_offset}:{search_query}"[:64]))
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["category_singles"], callback_data=f"aa:{artist_id}:epSingle:0:{search_offset}:{search_query}"[:64]))
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["category_compilations"], callback_data=f"aa:{artist_id}:other:0:{search_offset}:{search_query}"[:64]))
        
        if search_query == "main":
            builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data="menu:main"))
        else:
            builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=f"sp:{search_offset}:{search_query}"[:64]))
        
        photo_url = None
        if artist_data.get("image"):
            photo_url = artist_data["image"].get("large") or artist_data["image"].get("medium")

        if photo_url:
            try:
                # We need to delete the text message and send photo or edit if possible
                # But stupid aiogram cannot edit text to photo. So we delete and send new.
                await callback.message.delete()
                await callback.message.answer_photo(photo_url, caption=text, reply_markup=builder.as_markup())
            except Exception as pe:
                logger.warning(f"Could not send artist photo: {pe}")
                await callback.message.edit_text(text, reply_markup=builder.as_markup())
        else:
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
            
    except Exception as e:
        logger.error(f"Artist error: {e}")
        await callback.answer(TEXTS[lang]["error"].format(e="Failed to get artist"))

@dp.callback_query(F.data.startswith("aa:"))
async def cb_artist_albums(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    artist_id = parts[1]
    rel_type = parts[2]
    offset = int(parts[3])
    search_offset = int(parts[4]) if len(parts) > 4 else 0
    search_query = parts[5] if len(parts) > 5 else "main"
    limit = 10
    lang = user_pref.get_lang(callback.from_user.id)
    
    try:
        # Get artist name
        artist_data = await q_client.get_artist(artist_id)
        name = artist_data["name"]
        
        # Get albums with offset
        albums_data = await q_client.get_artist_releases(artist_id, release_type=rel_type, limit=limit, offset=offset)
        albums = albums_data.get("items", [])
        has_more = albums_data.get("has_more", False)
        
        if not albums and offset == 0:
            await callback.answer(TEXTS[lang]["no_results"])
            return

        builder = InlineKeyboardBuilder()
        for album in albums:
            # Try multiple fields for release year
            date_str = (
                album.get('release_date_original') or 
                album.get('release_date_stream') or 
                album.get('dates', {}).get('original') or 
                album.get('dates', {}).get('stream')
            )
            year = date_str[:4] if date_str and len(date_str) >= 4 else ""
            
            label = f"💽 {album['title']}"
            if year:
                label += f" ({year})"
            label = label[:64]
            
            builder.row(types.InlineKeyboardButton(text=label, callback_data=f"al:{album['id']}:0:aa:{artist_id}:{rel_type}:{offset}:{search_offset}:{search_query}"[:64]))
            
        nav = []
        if offset >= limit:
            nav.append(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=f"aa:{artist_id}:{rel_type}:{offset-limit}:{search_offset}:{search_query}"[:64]))
        if has_more:
            nav.append(types.InlineKeyboardButton(text=TEXTS[lang]["forward"], callback_data=f"aa:{artist_id}:{rel_type}:{offset+limit}:{search_offset}:{search_query}"[:64]))
            
        if nav:
            builder.row(*nav)
        
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=f"ar:{artist_id}:{search_offset}:{search_query}"[:64]))
        
        page = (offset // limit) + 1
        
        # If the current message is a photo, we edit caption and markup
        if callback.message.photo:
            await callback.message.edit_caption(caption=TEXTS[lang]["albums_of"].format(name=name, page=page), reply_markup=builder.as_markup())
        else:
            await callback.message.edit_text(TEXTS[lang]["albums_of"].format(name=name, page=page), reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Artist albums error: {e}")
        await callback.answer(TEXTS[lang]["error"].format(e="Failed to get albums"))

@dp.callback_query(F.data.startswith("sp:"))
async def cb_search_page(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    offset = int(parts[1])
    query = ":".join(parts[2:])
    await perform_search(callback, query, offset)

@dp.callback_query(F.data.startswith("dl_track:"))
async def callback_track(callback: types.CallbackQuery):
    track_id = callback.data.split(":")[1]
    uid = callback.from_user.id
    user_q = user_pref.get_quality(uid)
    lang = user_pref.get_lang(uid)
    downloader.quality = user_q
    await callback.answer(TEXTS[lang]["downloading"].format(type="track"))
    
    # If photo, delete it to show status as text
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(TEXTS[lang]["loading_track"])
    else:
        await callback.message.answer(TEXTS[lang]["loading_track"])
    
    folder_to_clean = None
    try:
        file_path, caption, p_info = await downloader.download_track(track_id)
        folder_to_clean = p_info.get("folder_path")
        await callback.message.answer_audio(
            FSInputFile(file_path), 
            caption=caption,
            title=p_info['title'],
            performer=p_info['performer'],
            duration=p_info['duration'],
            thumbnail=FSInputFile(p_info['thumbnail']) if p_info.get('thumbnail') else None
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        # Use message.answer instead of edit_text if we deleted the original message
        await callback.message.answer(TEXTS[lang]["error"].format(e=str(e)))
    finally:
        if folder_to_clean and os.path.exists(folder_to_clean):
            shutil.rmtree(folder_to_clean, ignore_errors=True)

@dp.callback_query(F.data.startswith("al:"))
async def cb_album_details(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    album_id = parts[1]
    track_offset = int(parts[2])
    # Context can be search (sp:offset:query) or discography (aa:artist_id:type:offset:search_offset:search_query)
    context_data = ":".join(parts[3:])
    
    lang = user_pref.get_lang(callback.from_user.id)
    limit = 8
    
    try:
        album_data = await q_client.get_album(album_id)
        title = album_data["title"]
        artist = album_data["artist"]["name"]
        year = (album_data.get("release_date_original") or album_data.get("release_date_stream") or "")[:4]
        
        tracks = album_data.get("tracks", {}).get("items", [])
        total_tracks = len(tracks)
        
        # Pagination
        start = track_offset
        end = min(track_offset + limit, total_tracks)
        tracks_slice = tracks[start:end]
        
        text = TEXTS[lang]["album_info"].format(title=title, artist=artist, year=year)
        
        builder = InlineKeyboardBuilder()
        # Single track buttons
        for tr in tracks_slice:
            tr_label = f"{tr['track_number']}. {tr['title']}"[:64]
            builder.row(types.InlineKeyboardButton(text=tr_label, callback_data=f"dl_track:{tr['id']}"))
            
        # Navigation for tracks
        nav = []
        if track_offset >= limit:
            nav.append(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=f"al:{album_id}:{track_offset-limit}:{context_data}"[:64]))
        if total_tracks > track_offset + limit:
            nav.append(types.InlineKeyboardButton(text=TEXTS[lang]["forward"], callback_data=f"al:{album_id}:{track_offset+limit}:{context_data}"[:64]))
        if nav:
            builder.row(*nav)
            
        # Download all button
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["download_full_album"], callback_data=f"dl_full:{album_id}"))
        
        # Back button uses context_data
        builder.row(types.InlineKeyboardButton(text=TEXTS[lang]["back"], callback_data=context_data[:64]))
        
        photo_url = album_data.get("image", {}).get("large")
        
        if photo_url:
            if callback.message.photo:
                await callback.message.edit_caption(caption=text, reply_markup=builder.as_markup())
            else:
                await callback.message.delete()
                await callback.message.answer_photo(photo_url, caption=text, reply_markup=builder.as_markup())
        else:
            if callback.message.photo:
                await callback.message.delete()
                await callback.message.answer(text, reply_markup=builder.as_markup())
            else:
                await callback.message.edit_text(text, reply_markup=builder.as_markup())
                
    except Exception as e:
        logger.error(f"Album details error: {e}")
        await callback.answer(TEXTS[lang]["error"].format(e="Failed to get album details"))

@dp.callback_query(F.data.startswith("dl_full:"))
async def cb_dl_full_album(callback: types.CallbackQuery):
    album_id = callback.data.split(":")[1]
    await handle_download_album(callback, album_id)

async def handle_download_album(callback: types.CallbackQuery, album_id: str):
    uid = callback.from_user.id
    user_q = user_pref.get_quality(uid)
    lang = user_pref.get_lang(uid)
    downloader.quality = user_q
    await callback.answer(TEXTS[lang]["downloading"].format(type="album"))
    
    # Update message to show progress
    if callback.message.photo:
        await callback.message.delete()
        status_msg = await callback.message.answer(TEXTS[lang]["loading_album"])
    else:
        status_msg = await callback.message.edit_text(TEXTS[lang]["loading_album"])
    
    folder_to_clean = None
    try:
        album_data = await q_client.get_album(album_id)
        tracks = album_data["tracks"]["items"]
        for track in tracks:
            file_path, caption, p_info = await downloader.download_track(track["id"], album_data)
            if not folder_to_clean: folder_to_clean = p_info.get("folder_path")
            await callback.message.answer_audio(
                FSInputFile(file_path), 
                caption=caption,
                title=p_info['title'],
                performer=p_info['performer'],
                duration=p_info['duration'],
                thumbnail=FSInputFile(p_info['thumbnail']) if p_info.get('thumbnail') else None
            )
        if status_msg:
            await status_msg.edit_text(TEXTS[lang]["album_sent"])
        else:
            await callback.message.answer(TEXTS[lang]["album_sent"])
    except Exception as e:
        logger.error(f"Download error: {e}")
        if status_msg:
            await status_msg.edit_text(TEXTS[lang]["error"].format(e=str(e)))
        else:
            await callback.message.answer(TEXTS[lang]["error"].format(e=str(e)))
    finally:
        if folder_to_clean and os.path.exists(folder_to_clean):
            shutil.rmtree(folder_to_clean, ignore_errors=True)

@dp.callback_query(F.data.startswith("dl_album:"))
async def callbacks_num(callback: types.CallbackQuery):
    album_id = callback.data.split(":")[1]
    await handle_download_album(callback, album_id)

async def main():
    logger.info("Starting bot...")
    await q_client.initialize()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
