import os
import asyncio
import logging
import httpx
from pathvalidate import sanitize_filename, sanitize_filepath
import aiofiles
from qobuz_client import QobuzClient
import metadata_utils

logger = logging.getLogger(__name__)

class QobuzDownloader:
    def __init__(self, client: QobuzClient, download_base_path="./downloads", quality=6):
        self.client = client
        self.base_path = download_base_path
        self.quality = quality
        os.makedirs(self.base_path, exist_ok=True)

    async def download_track(self, track_id, album_data=None, folder_path=None, pbar_callback=None):
        """Download a single track."""
        if not album_data:
            track_meta = await self.client.get_track(track_id)
            album_data = track_meta["album"]
            track_data = track_meta
        else:
            # When downloading as part of an album, we might already have album data
            # but we need specific track url and meta
            track_data = await self.client.get_track(track_id)

        # Get download URL
        file_info = await self.client.get_file_url(track_id, self.quality)
        url = file_info.get("url")
        if not url:
            raise Exception("No download URL available. It might be a demo or restricted.")

        # Prepare paths
        artist_name = album_data["artist"]["name"]
        album_title = album_data["title"]
        year = album_data.get("release_date_original", "").split("-")[0]
        
        if not folder_path:
            folder_name = sanitize_filename(f"{artist_name} - {album_title} ({year})")
            folder_path = os.path.join(self.base_path, folder_name)
        
        os.makedirs(folder_path, exist_ok=True)

        is_mp3 = int(self.quality) == 5
        extension = ".mp3" if is_mp3 else ".flac"
        
        track_number = f"{track_data['track_number']:02}"
        track_title = metadata_utils.get_title(track_data)
        filename = sanitize_filename(f"{track_number}. {track_title}{extension}")
        final_path = os.path.join(folder_path, filename)

        if os.path.exists(final_path):
            logger.info(f"File already exists: {filename}")
            return final_path

        # Temp
        tmp_path = final_path + ".tmp"
        
        # Download cover if not exists
        cover_path = await self._download_cover(album_data, folder_path)

        # Start download
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                
                async with aiofiles.open(tmp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024*64):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if pbar_callback:
                            await pbar_callback(downloaded, total_size, filename)

        # Prepare thumbnail
        thumb_path = os.path.join(folder_path, "thumb.jpg")
        if os.path.exists(thumb_path):
            try: os.remove(thumb_path)
            except: pass

        try:
            thumb_path = metadata_utils.create_thumbnail(cover_path)
        except Exception as e:
            logger.warning(f"Failed to create thumbnail: {e}")
            thumb_path = None

        # Tagging (this is blocking, so run in executor if needed, but for small-ish files it's fine)
        await asyncio.to_thread(
            metadata_utils.tag_mp3 if is_mp3 else metadata_utils.tag_flac,
            tmp_path, final_path, track_data, album_data, cover_path
        )
        
        caption = metadata_utils.get_audio_info(final_path)
        
        player_info = {
            "title": track_title,
            "performer": artist_name,
            "duration": track_data.get("duration"),
            "cover": cover_path,
            "thumbnail": thumb_path,
            "folder_path": folder_path
        }
        
        return final_path, caption, player_info

    async def _download_cover(self, album_data, folder_path):
        cover_path = os.path.join(folder_path, "cover.jpg")
        if os.path.exists(cover_path):
            return cover_path
            
        base_url = album_data["image"]["large"] # Usually 600x600
        # List of URLs to try in order of preference
        urls_to_try = [
            base_url.replace("_600.jpg", "_org.jpg"),  # Original
            base_url,                                  # 600px
            album_data["image"]["small"]               # fallback
        ]
        
        async with httpx.AsyncClient() as client:
            for url in urls_to_try:
                if not url: continue
                try:
                    resp = await client.get(url, timeout=10.0)
                    if resp.status_code == 200:
                        async with aiofiles.open(cover_path, "wb") as f:
                            await f.write(resp.content)
                        logger.info(f"Downloaded cover art: {url}")
                        return cover_path
                except Exception as e:
                    logger.debug(f"Failed to download cover from {url}: {e}")
        
        logger.warning("Failed to download any cover art")
        return None

    async def download_album(self, album_id, pbar_callback=None):
        """Download an entire album."""
        album_data = await self.client.get_album(album_id)
        tracks = album_data["tracks"]["items"]
        
        artist_name = album_data["artist"]["name"]
        album_title = album_data["title"]
        year = album_data.get("release_date_original", "").split("-")[0]
        folder_name = sanitize_filename(f"{artist_name} - {album_title} ({year})")
        folder_path = os.path.join(self.base_path, folder_name)
        
        downloaded_data = []
        for i, track in enumerate(tracks):
            try:
                # We can update the progress bar to show overall progress too
                result = await self.download_track(track["id"], album_data, folder_path, pbar_callback)
                downloaded_data.append(result)
            except Exception as e:
                logger.error(f"Failed to download track {track['title']}: {e}")
        
        return downloaded_data
