import os
import re
import time
import logging
from mutagen.flac import FLAC, Picture
import mutagen.id3 as id3
from mutagen.id3 import ID3NoHeaderError

logger = logging.getLogger(__name__)

COPYRIGHT, PHON_COPYRIGHT = "\u2117", "\u00a9"
FLAC_MAX_BLOCKSIZE = 16777215

ID3_LEGEND = {
    "album": id3.TALB,
    "albumartist": id3.TPE2,
    "artist": id3.TPE1,
    "comment": id3.COMM,
    "composer": id3.TCOM,
    "copyright": id3.TCOP,
    "date": id3.TDAT,
    "genre": id3.TCON,
    "isrc": id3.TSRC,
    "label": id3.TPUB,
    "performer": id3.TOPE,
    "title": id3.TIT2,
    "year": id3.TYER,
}

def get_title(track_dict):
    title = track_dict["title"]
    version = track_dict.get("version")
    if version:
        title = f"{title} ({version})"
    if track_dict.get("work"):
        title = f"{track_dict['work']}: {title}"
    return title

def format_copyright(s: str) -> str:
    if s:
        s = s.replace("(P)", PHON_COPYRIGHT)
        s = s.replace("(C)", COPYRIGHT)
    return s

def format_genres(genres: list) -> str:
    if not genres: return ""
    genres = re.findall(r"([^\\u2192\\/]+)", "/".join(genres))
    no_repeats = []
    [no_repeats.append(g) for g in genres if g not in no_repeats]
    return ", ".join(no_repeats)

def embed_flac_img(cover_path, audio: FLAC):
    if not os.path.isfile(cover_path): return
    try:
        if os.path.getsize(cover_path) > FLAC_MAX_BLOCKSIZE:
            logger.warning("Cover size too large for FLAC embedding")
            return
        
        # Clear existing pictures
        audio.clear_pictures()
        
        image = Picture()
        image.type = 3
        image.mime = "image/jpeg"
        image.desc = "cover"
        with open(cover_path, "rb") as img:
            image.data = img.read()
        audio.add_picture(image)
    except Exception as e:
        logger.error(f"Error embedding FLAC image: {e}")

def embed_id3_img(cover_path, audio: id3.ID3):
    if not os.path.isfile(cover_path): return
    try:
        # Clear existing APIC frames
        audio.delall("APIC")
        
        with open(cover_path, "rb") as cover:
            audio.add(id3.APIC(3, "image/jpeg", 3, "", cover.read()))
    except Exception as e:
        logger.error(f"Error embedding ID3 image: {e}")

def tag_flac(filename, final_name, track_data, album_data, cover_path=None):
    audio = FLAC(filename)
    audio["TITLE"] = get_title(track_data)
    audio["TRACKNUMBER"] = str(track_data["track_number"])
    audio["DISCNUMBER"] = str(track_data.get("media_number", 1))
    
    if "composer" in track_data:
        audio["COMPOSER"] = track_data["composer"]["name"]
    
    artist = track_data.get("performer", {}).get("name") or track_data.get("album", {}).get("artist", {}).get("name")
    audio["ARTIST"] = artist or "Unknown Artist"
    audio["ALBUMARTIST"] = album_data.get("artist", {}).get("name", "Unknown Artist")
    audio["LABEL"] = album_data.get("label", {}).get("name", "n/a")
    audio["GENRE"] = format_genres(album_data.get("genres_list", []))
    audio["ALBUM"] = album_data.get("title", "Unknown Album")
    audio["DATE"] = album_data.get("release_date_original", "")
    audio["COPYRIGHT"] = format_copyright(track_data.get("copyright") or album_data.get("copyright") or "n/a")
    audio["TRACKTOTAL"] = str(album_data.get("tracks_count", 0))

    if cover_path:
        embed_flac_img(cover_path, audio)
    
    audio.save()
    if os.path.exists(final_name): os.remove(final_name)
    os.rename(filename, final_name)

def tag_mp3(filename, final_name, track_data, album_data, cover_path=None):
    try:
        audio = id3.ID3(filename)
    except ID3NoHeaderError:
        audio = id3.ID3()
        audio.save(filename)

    tags = dict()
    tags["title"] = get_title(track_data)
    tags["album"] = album_data.get("title", "Unknown Album")
    tags["artist"] = track_data.get("performer", {}).get("name") or album_data.get("artist", {}).get("name", "Unknown Artist")
    tags["albumartist"] = album_data.get("artist", {}).get("name", "Unknown Artist")
    tags["date"] = album_data.get("release_date_original", "")
    tags["year"] = tags["date"][:4] if tags["date"] else ""
    tags["genre"] = format_genres(album_data.get("genres_list", []))
    tags["copyright"] = format_copyright(track_data.get("copyright") or album_data.get("copyright") or "n/a")
    tags["label"] = album_data.get("label", {}).get("name", "n/a")

    audio["TRCK"] = id3.TRCK(encoding=3, text=f'{track_data["track_number"]}/{album_data.get("tracks_count", 0)}')
    audio["TPOS"] = id3.TPOS(encoding=3, text=str(track_data.get("media_number", 1)))

    for k, v in tags.items():
        if v:
            id3tag = ID3_LEGEND[k]
            audio[id3tag.__name__] = id3tag(encoding=3, text=v)

    if cover_path:
        embed_id3_img(cover_path, audio)

    audio.save(filename, v2_version=3)
    if os.path.exists(final_name): os.remove(final_name)
    os.rename(filename, final_name)

def create_thumbnail(image_path):
    """Creates a 320x320 JPEG thumbnail for Telegram."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        from PIL import Image
        thumb_path = os.path.join(os.path.dirname(image_path), "thumb.jpg")
        with Image.open(image_path) as img:
            thumb = img.convert("RGB")
            thumb.thumbnail((320, 320))
            thumb.save(thumb_path, "JPEG", quality=90)
        size_kb = os.path.getsize(thumb_path) / 1024
        logger.info(f"Created thumbnail: {thumb_path} ({size_kb:.2f} KB)")
        return thumb_path
    except Exception as e:
        logger.error(f"Error creating thumbnail: {e}")
    return None

def get_audio_info(file_path):
    """Returns a formatted string with technical audio details."""
    try:
        if file_path.endswith('.flac'):
            audio = FLAC(file_path)
            # Use bits_per_sample and sample_rate to determine Hi-Res
            bt_depth = getattr(audio.info, 'bits_per_sample', 16)
            sr_hz = audio.info.sample_rate
            is_hires = bt_depth > 16 or sr_hz > 48000
            quality_type = "Hi-Res" if is_hires else "CD"
            
            # Use stream bitrate if possible, otherwise calculate more accurately
            if hasattr(audio.info, 'bitrate') and audio.info.bitrate > 0:
                bitrate = int(audio.info.bitrate / 1000)
            else:
                # Fallback: estimate bitrate excluding metadata overhead (rough but closer)
                # Stream info doesn't always have bitrate for flacs in mutagen
                bitrate = int(os.path.getsize(file_path) * 8 / audio.info.length / 1000)
            
            sr_khz = sr_hz / 1000
            sr_str = f"{sr_khz:g}"
            return f"FLAC {quality_type} {bt_depth}-Bit / {sr_str} kHz / {bitrate} kbps"
        elif file_path.endswith('.mp3'):
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            bitrate = int(audio.info.bitrate / 1000)
            return f"MP3 {bitrate} kbps"
    except Exception as e:
        logger.error(f"Error getting audio info: {e}")
    return ""
