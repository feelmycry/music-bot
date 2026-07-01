import asyncio
import os
import tempfile
import yt_dlp

MAX_DURATION = 600  # 10 минут


def _search_sync(query: str, max_results: int = 8) -> list[dict]:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
    }
    url = f"scsearch{max_results}:{query}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        tracks = []
        for entry in (info.get('entries') or []):
            duration = entry.get('duration') or 0
            if duration and duration > MAX_DURATION:
                continue
            title = entry.get('title', 'Unknown')
            uploader = entry.get('uploader') or entry.get('channel') or 'Unknown'
            track_url = entry.get('url') or entry.get('webpage_url', '')
            if not track_url:
                continue
            tracks.append({
                'title': title,
                'artist': uploader,
                'duration': duration,
                'download_url': track_url,
            })
        return tracks


def _download_sync(url: str) -> str | None:
    tmp_dir = tempfile.mkdtemp()
    output_tmpl = os.path.join(tmp_dir, '%(title)s.%(ext)s')

    # SoundCloud отдаёт MP3 напрямую — ffmpeg не нужен
    ydl_opts = {
        'format': 'bestaudio[ext=mp3]/bestaudio/best',
        'outtmpl': output_tmpl,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 60,
        'retries': 3,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                size = os.path.getsize(filename)
                print(f"[SCRAPER] Скачано {size // 1024} KB -> {filename}", flush=True)
                return filename
            # Ищем любой аудиофайл в tmp_dir
            audio_exts = ('.mp3', '.m4a', '.ogg', '.opus', '.flac', '.wav', '.aac')
            for f in os.listdir(tmp_dir):
                if any(f.endswith(ext) for ext in audio_exts):
                    found = os.path.join(tmp_dir, f)
                    print(f"[SCRAPER] Найден файл {os.path.getsize(found) // 1024} KB -> {found}", flush=True)
                    return found
            print(f"[SCRAPER] Файл не найден, содержимое tmp: {os.listdir(tmp_dir)}", flush=True)
            return None
    except Exception as e:
        print(f"[SCRAPER] Ошибка загрузки ({type(e).__name__}): {e}", flush=True)
        return None


async def search_music(query: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _search_sync, query)
        print(f"[SCRAPER] Найдено треков: {len(results)}", flush=True)
        return results
    except Exception as e:
        print(f"[SCRAPER] Ошибка поиска ({type(e).__name__}): {e}", flush=True)
        return []


async def download_track(url: str) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_sync, url)
