import aiohttp
import asyncio
import re
import os
import tempfile
import json
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
}

BASE_URL = "https://hitmos.me"
SEARCH_URL = "https://hitmos.me/search?q={query}"


def _parse_tracks(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    tracks = []

    # --- Основной метод: data-musmeta (формат hitmoz/hitmos) ---
    items = soup.select('li.tracks__item[data-musmeta], [data-musmeta]')
    for item in items[:10]:
        raw = item.get('data-musmeta', '')
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            continue
        url = meta.get('url', '')
        title = meta.get('title', '')
        artist = meta.get('artist', 'Unknown')
        if url and title:
            if not url.startswith('http'):
                url = base_url + url
            tracks.append({'title': title, 'artist': artist, 'download_url': url})

    if tracks:
        return tracks

    # --- Запасной: кнопка скачивания a.track__download-btn ---
    for item in soup.select('li.tracks__item'):
        dl = item.select_one('a.track__download-btn[href]')
        title_el = item.select_one('.track__title')
        artist_el = item.select_one('.track__desc')
        if not dl:
            continue
        url = dl.get('href', '')
        if not url.startswith('http'):
            url = base_url + url
        title = title_el.get_text(strip=True) if title_el else 'Track'
        artist = artist_el.get_text(strip=True) if artist_el else 'Unknown'
        tracks.append({'title': title, 'artist': artist, 'download_url': url})

    if tracks:
        return tracks

    # --- Последний вариант: прямые MP3-ссылки в HTML ---
    mp3_urls = re.findall(r'(https?://[^\s"\'<>]+\.mp3[^\s"\'<>]*)', html)
    for url in mp3_urls[:10]:
        filename = url.split('/')[-1].split('?')[0].replace('.mp3', '').replace('_', ' ')
        tracks.append({'title': filename or 'Track', 'artist': 'Unknown', 'download_url': url})

    return tracks


def _extract_from_scripts(html: str) -> list[dict]:
    """Извлекает треки из JSON внутри <script> тегов."""
    soup = BeautifulSoup(html, 'lxml')
    tracks = []

    for script in soup.find_all('script'):
        text = script.string or ''
        # Ищем JSON-массивы с полями url/title/artist
        matches = re.findall(r'\{[^{}]*"url"\s*:\s*"([^"]+)"[^{}]*\}', text)
        if matches:
            for match in matches[:10]:
                tracks.append({'title': 'Track', 'artist': 'Unknown', 'download_url': match})
            if tracks:
                return tracks

        # Паттерн для объектов с аудио данными
        json_blocks = re.findall(r'(\[.*?\])', text, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    if any(k in data[0] for k in ('url', 'mp3', 'audio', 'src', 'link')):
                        for item in data[:10]:
                            url = (item.get('url') or item.get('mp3') or
                                   item.get('audio') or item.get('src') or '')
                            title = item.get('title') or item.get('name') or item.get('song') or 'Track'
                            artist = item.get('artist') or item.get('performer') or item.get('singer') or 'Unknown'
                            if url:
                                tracks.append({'title': title, 'artist': artist, 'download_url': url})
                        if tracks:
                            return tracks
            except (json.JSONDecodeError, ValueError):
                continue

    return tracks


async def search_music(query: str) -> list[dict]:
    url = SEARCH_URL.format(query=query.replace(' ', '+'))

    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(headers=HEADERS, connector=connector, timeout=timeout) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                final_url = str(resp.url)
                base = '/'.join(final_url.split('/')[:3])

                if resp.status == 403:
                    # Сохраняем HTML для отладки
                    print(f"[SCRAPER] 403 Forbidden от {final_url}")
                    return []

                if resp.status != 200:
                    print(f"[SCRAPER] HTTP {resp.status} от {final_url}")
                    return []

                html = await resp.text()

                tracks = _parse_tracks(html, base)
                print(f"[SCRAPER] Найдено треков: {len(tracks)}")
                return tracks

        except aiohttp.ClientError as e:
            print(f"[SCRAPER] Ошибка подключения: {e}")
            return []
        except Exception as e:
            print(f"[SCRAPER] Неожиданная ошибка: {e}")
            return []


async def download_track(url: str) -> str | None:
    download_headers = {
        **HEADERS,
        'Referer': BASE_URL,
        'Accept': 'audio/mpeg,audio/*;q=0.9,*/*;q=0.8',
    }

    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(headers=download_headers, connector=connector, timeout=timeout) as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"[SCRAPER] Ошибка загрузки: HTTP {resp.status}")
                    return None

                content_type = resp.headers.get('Content-Type', '')
                if 'audio' not in content_type and 'octet-stream' not in content_type and 'mpeg' not in content_type:
                    print(f"[SCRAPER] Неожиданный Content-Type: {content_type}")

                content = await resp.read()
                if len(content) < 10_000:
                    print(f"[SCRAPER] Файл слишком маленький ({len(content)} байт), вероятно ошибка")
                    return None

                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
                tmp.write(content)
                tmp.close()
                print(f"[SCRAPER] Скачано {len(content)} байт -> {tmp.name}")
                return tmp.name

        except Exception as e:
            print(f"[SCRAPER] Ошибка при скачивании: {e}")
            return None
