import asyncio
import os
import tempfile
import yt_dlp

MAX_DURATION = 600  # 10 минут — отсекаем подкасты и длинные видео


def _search_sync(query: str, max_results: int = 8) -> list[dict]:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
    }
    url = f"ytsearch{max_results}:{query}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        tracks = []
        for entry in (info.get('entries') or []):
            duration = entry.get('duration') or 0
            if duration > MAX_DURATION:
                continue
            title = entry.get('title', 'Unknown')
            uploader = entry.get('uploader') or entry.get('channel') or 'Unknown'
            video_id = entry.get('id', '')
            if not video_id:
                continue
            tracks.append({
                'title': title,
                'artist': uploader,
                'duration': duration,
                'download_url': f"https://www.youtube.com/watch?v={video_id}",
            })
        return tracks


def _download_sync(url: str) -> str | None:
    tmp_dir = tempfile.mkdtemp()
    output_tmpl = os.path.join(tmp_dir, '%(title)s.%(ext)s')

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_tmpl,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # После FFmpeg расширение меняется на .mp3
            mp3_path = os.path.splitext(filename)[0] + '.mp3'
            if os.path.exists(mp3_path):
                size = os.path.getsize(mp3_path)
                print(f"[SCRAPER] Скачано {size // 1024} KB -> {mp3_path}")
                return mp3_path
            print(f"[SCRAPER] Файл не найден: {mp3_path}")
            return None
    except Exception as e:
        print(f"[SCRAPER] Ошибка загрузки: {e}")
        return None


async def search_music(query: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _search_sync, query)
        print(f"[SCRAPER] Найдено треков: {len(results)}")
        return results
    except Exception as e:
        print(f"[SCRAPER] Ошибка поиска: {e}")
        return []


async def download_track(url: str) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_sync, url)
