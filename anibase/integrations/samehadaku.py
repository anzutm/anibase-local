# anibase/integrations/samehadaku.py
"""Integrasi scraper dan direct link Samehadaku (v2.samehadaku.how).

Menyediakan fungsi pencarian anime, parsing daftar episode dari halaman anime/hub,
parsing tautan unduhan (MKV, MP4, x265 dengan berbagai resolusi), dan resolusi tautan
langsung (direct download) untuk host yang didukung seperti Pixeldrain.
"""

import os
import re
import html
import time
import json
import hashlib
import urllib.parse
from typing import Optional, List, Dict, Any

import requests
from anibase.constants import CACHE_DIR
from anibase.logging import app_log

SAMEHADAKU_BASE_URL = "https://v2.samehadaku.how"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 12
CACHE_TTL_SECONDS = 3600  # 1 jam


def _get_cache_dir() -> str:
    cache_path = os.path.join(CACHE_DIR, "samehadaku")
    try:
        os.makedirs(cache_path, exist_ok=True)
    except OSError:
        pass
    return cache_path


def _get_cached_data(cache_key: str) -> Optional[Any]:
    cache_file = os.path.join(_get_cache_dir(), f"{cache_key}.json")
    if not os.path.isfile(cache_file):
        return None
    try:
        mtime = os.path.getmtime(cache_file)
        if time.time() - mtime > CACHE_TTL_SECONDS:
            return None
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as error:
        app_log(f"Failed to read Samehadaku cache: {error}", "DEBUG")
        return None


def _set_cached_data(cache_key: str, data: Any) -> None:
    cache_file = os.path.join(_get_cache_dir(), f"{cache_key}.json")
    try:
        tmp_file = f"{cache_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_file, cache_file)
    except Exception as error:
        app_log(f"Failed to write Samehadaku cache: {error}", "DEBUG")


def _get_headers() -> Dict[str, str]:
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id,en-US;q=0.9,en;q=0.8",
    }


def _parse_samehadaku_search_articles(html_text: str, filter_episodes: bool = True) -> List[Dict[str, Any]]:
    """Helper untuk mem-parsing artikel hasil pencarian Samehadaku."""
    results = []
    seen_urls = set()
    articles = re.findall(
        r'<article[^>]*class=["\'][^"\']*animepost[^"\']*["\'][^>]*>([\s\S]*?)</article>',
        html_text,
    )
    for art in articles:
        link_m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']*)["\']', art)
        if not link_m:
            link_m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\']', art)
            if not link_m:
                continue
            href = link_m.group(1).strip()
            title = ""
        else:
            href = link_m.group(1).strip()
            title = link_m.group(2).strip()

        if not title:
            title_m = re.search(
                r'<(?:h2|div)[^>]*class=["\'][^"\']*(?:title|entry-title)[^"\']*["\'][^>]*>([\s\S]*?)</(?:h2|div)>',
                art,
            )
            if title_m:
                title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

        title = html.unescape(title).strip()
        img_m = re.search(r'<img\s+[^>]*?\bsrc=["\']([^"\']+)["\']', art)
        img_src = img_m.group(1).strip() if img_m else ""

        type_m = re.search(r'<div[^>]*class=["\'][^"\']*type[^"\']*["\'][^>]*>([\s\S]*?)</div>', art)
        item_type = re.sub(r'<[^>]+>', '', type_m.group(1)).strip() if type_m else ""

        score_m = re.search(r'<div[^>]*class=["\'][^"\']*score[^"\']*["\'][^>]*>([\s\S]*?)</div>', art)
        score = re.sub(r'<[^>]+>', '', score_m.group(1)).replace("★", "").strip() if score_m else ""

        is_anime = "/anime/" in href
        is_batch = "/batch/" in href
        is_episode = (
            bool(re.search(r'-episode-\d+', href, re.I))
            or bool(re.search(r'\b(?:Eps|Episode)\s*\d+\b', title, re.I))
            or item_type.lower().startswith("eps")
        )

        # Jika filter_episodes aktif, abaikan postingan episode tunggal (hanya ambil serial anime / batch)
        if filter_episodes and is_episode and not is_anime:
            continue

        if href in seen_urls:
            continue
        seen_urls.add(href)

        results.append({
            "title": title,
            "url": href,
            "thumbnail": img_src,
            "type": item_type or ("Anime" if is_anime else ("Batch" if is_batch else "Anime")),
            "score": score,
            "is_anime": is_anime or not is_episode,
            "is_batch": is_batch,
            "is_episode": False if filter_episodes else is_episode,
        })

    return results


def search_samehadaku(query: str, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Cari anime di Samehadaku berdasarkan judul (hanya serial anime & batch, mengecualikan episode)."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    cache_key = "search_anime_" + hashlib.md5(clean_query.lower().encode("utf-8")).hexdigest()
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    results = []

    # 1. Coba pencarian direktori resmi anime Samehadaku (/daftar-anime-2/?title=...)
    dir_url = f"{SAMEHADAKU_BASE_URL}/daftar-anime-2/?title={urllib.parse.quote_plus(clean_query)}"
    try:
        response = requests.get(dir_url, headers=_get_headers(), timeout=timeout)
        if response.status_code == 200:
            results = _parse_samehadaku_search_articles(response.text, filter_episodes=True)
    except Exception as error:
        app_log(f"Samehadaku directory search error: {error}", "DEBUG")

    # 2. Jika direktori tidak menghasilkan data, gunakan endpoint umum /?s=... dengan filter episode
    if not results:
        general_url = f"{SAMEHADAKU_BASE_URL}/?s={urllib.parse.quote_plus(clean_query)}"
        try:
            response = requests.get(general_url, headers=_get_headers(), timeout=timeout)
            if response.status_code == 200:
                results = _parse_samehadaku_search_articles(response.text, filter_episodes=True)
        except Exception as error:
            app_log(f"Samehadaku general search network error: {error}", "ERROR")

    _set_cached_data(cache_key, results)
    return results


def get_samehadaku_anime_details(page_url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Mengambil detail anime, daftar episode, dan tautan batch dari halaman anime Samehadaku."""
    clean_url = str(page_url or "").strip()
    if not clean_url:
        return {"ok": False, "error": "URL kosong"}

    cache_key = "anime_v2_" + hashlib.md5(clean_url.encode("utf-8")).hexdigest()
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    try:
        response = requests.get(clean_url, headers=_get_headers(), timeout=timeout)
        if response.status_code != 200:
            return {"ok": False, "error": f"HTTP status {response.status_code}"}
        html_text = response.text
    except Exception as error:
        return {"ok": False, "error": str(error)}

    # Jika URL yang diberikan adalah episode atau batch, cari apakah ada link ke hub /anime/...
    if "/anime/" not in clean_url:
        hub_match = re.search(
            r'<a\s+[^>]*href=["\'](https://v2\.samehadaku\.how/anime/[^"\']+)["\'][^>]*>(?:All Episode|<span[^>]*itemprop=["\']name["\']>[^<]+</span>|Tonton Episode Lainnya)',
            html_text,
            re.I
        )
        if hub_match:
            hub_url = hub_match.group(1).strip()
            # Fetch the actual anime hub page for comprehensive episode list
            return get_samehadaku_anime_details(hub_url, timeout=timeout)

    # Parse title
    title = ""
    title_m = re.search(r'<h1[^>]*>([\s\S]*?)</h1>', html_text)
    if title_m:
        raw_h1 = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
        raw_h1 = re.sub(r'\s*(?:Sub\s*Indo|Subtitle\s*Indonesia|Samehadaku).*$', '', raw_h1, flags=re.I).strip()
        title = raw_h1
    if not title:
        og_title = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html_text)
        if og_title:
            clean_og = html.unescape(og_title.group(1))
            clean_og = re.sub(r'\s*(?:[-–—]\s*)?Samehadaku.*$', '', clean_og, flags=re.I).strip()
            title = clean_og
    # Hilangkan tanda hubung / strip di akhir judul seperti "Kashita Maryoku -"
    title = re.sub(r'[\s\-–—]+$', '', title).strip()

    # Parse poster
    poster = ""
    og_img = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']', html_text)
    if og_img:
        poster = og_img.group(1).strip()
    if not poster:
        thumb_m = re.search(r'<div[^>]*class=["\'][^"\']*(?:thumb|content-thumb)[^"\']*["\'][^>]*>\s*<img[^>]*?\bsrc=["\']([^"\']+)["\']', html_text)
        if thumb_m:
            poster = thumb_m.group(1).strip()

    # Parse episode thumbnails (dari featured banner di halaman hub & artikel pencarian Samehadaku)
    ep_thumbs: Dict[str, str] = {}
    banner_m = re.search(
        r'<a[^>]*href=["\'](https://v2\.samehadaku\.how/[^"\']*episode-[^"\']*)["\'][^>]*style=["\'][^"\']*url\([\'"]?([^\'")]+)[\'"]?\)',
        html_text,
        re.I
    )
    if banner_m:
        ep_thumbs[banner_m.group(1).strip()] = banner_m.group(2).strip()

    # Ekstraksi thumbnail episode individual dari pencarian artikel episode
    search_keyword = ""
    slug_m = re.search(r'/anime/([^/]+)/?', clean_url)
    if slug_m:
        search_keyword = slug_m.group(1).replace("-", " ")
    elif title:
        search_keyword = title

    if search_keyword:
        s_cache_key = "ep_thumbs_" + hashlib.md5(search_keyword.lower().encode("utf-8")).hexdigest()
        cached_thumbs = _get_cached_data(s_cache_key)
        if cached_thumbs is not None:
            ep_thumbs.update(cached_thumbs)
        else:
            try:
                s_url = f"{SAMEHADAKU_BASE_URL}/?s={urllib.parse.quote_plus(search_keyword)}"
                s_resp = requests.get(s_url, headers=_get_headers(), timeout=timeout)
                if s_resp.status_code == 200:
                    fetched_thumbs: Dict[str, str] = {}
                    articles = re.findall(
                        r'<article[^>]*class=["\'][^"\']*animepost[^"\']*["\'][^>]*>([\s\S]*?)</article>',
                        s_resp.text,
                        re.I
                    )
                    for art in articles:
                        art_link = re.search(r'<a\s+[^>]*href=["\'](https://v2\.samehadaku\.how/[^"\']+)["\']', art)
                        art_img = re.search(r'<img[^>]*?\bsrc=["\']([^"\']+)["\']', art)
                        if art_link and art_img:
                            u = art_link.group(1).strip()
                            img_src = art_img.group(1).strip()
                            if "/uploads/" in img_src and not img_src.endswith("/uploads/"):
                                fetched_thumbs[u] = img_src
                    _set_cached_data(s_cache_key, fetched_thumbs)
                    ep_thumbs.update(fetched_thumbs)
            except Exception as error:
                app_log(f"Failed to fetch episode thumbnails for {search_keyword}: {error}", "DEBUG")

    # Parse synopsis
    synopsis = ""
    desc_m = re.search(r'<div[^>]*class=["\'][^"\']*(?:entry-content|desc|entry-content-single)[^"\']*["\'][^>]*>([\s\S]*?)</div>', html_text)
    if desc_m:
        synopsis = html.unescape(re.sub(r'<[^>]+>', '', desc_m.group(1))).strip()

    # Parse episodes
    episodes: List[Dict[str, Any]] = []
    seen_urls: Dict[str, Dict[str, Any]] = {}

    # Ekstraksi tautan episode dari halaman (hindari /batch/)
    ep_pattern = re.compile(
        r'<a\s+[^>]*href=["\'](https://v2\.samehadaku\.how/(?!batch/)[^"\']*(?:episode-(\d+(?:\.\d+)?)[^"\']*))["\'][^>]*>([\s\S]*?)</a>',
        re.I
    )

    for match in ep_pattern.finditer(html_text):
        ep_url = match.group(1).strip()
        ep_num = match.group(2)
        raw_inner = match.group(3)
        clean_inner = html.unescape(re.sub(r'<[^>]+>', '', raw_inner)).strip()

        # Lewati teks navigasi kosong
        if not clean_inner and not ep_num:
            continue

        display_title = clean_inner
        if not display_title or display_title == ep_num:
            display_title = f"{title} Episode {ep_num}" if title else f"Episode {ep_num}"

        if ep_url in seen_urls:
            # Jika URL sudah tercatat tapi judul sekarang lebih deskriptif, perbarui judulnya
            if len(display_title) > len(seen_urls[ep_url]["title"]):
                seen_urls[ep_url]["title"] = display_title
            continue

        # Cari tanggal jika ada di sekitar URL
        date = ""
        url_idx = html_text.find(ep_url)
        if url_idx != -1:
            snippet = html_text[url_idx:url_idx + 400]
            date_m = re.search(r'<div[^>]*class=["\'][^"\']*text-(?:gray-400|xs)[^"\']*[^>]*>([\s\S]*?)</div>', snippet)
            if date_m:
                date = html.unescape(re.sub(r'<[^>]+>', '', date_m.group(1))).strip()

        # Dapatkan thumbnail episode (dari screenshot episode atau poster anime)
        ep_thumb = ep_thumbs.get(ep_url) or (poster if poster else "")

        entry = {
            "episode": ep_num,
            "title": display_title,
            "url": ep_url,
            "thumbnail": ep_thumb,
            "date": date,
            "is_batch": False,
        }
        seen_urls[ep_url] = entry
        episodes.append(entry)

    # Sort episodes by episode number numeric ascending
    def _ep_sort_key(item):
        try:
            return float(item.get("episode") or 0)
        except ValueError:
            return 0.0

    episodes.sort(key=_ep_sort_key)

    # Parse batch links
    batch_links: List[Dict[str, Any]] = []
    batch_pattern = re.compile(
        r'<a\s+[^>]*href=["\'](https://v2\.samehadaku\.how/batch/[^"\']+)["\'][^>]*>([\s\S]*?)</a>',
        re.I
    )
    seen_batch = set()
    for b_match in batch_pattern.finditer(html_text):
        b_url = b_match.group(1).strip()
        if b_url in seen_batch:
            continue
        seen_batch.add(b_url)
        b_title = html.unescape(re.sub(r'<[^>]+>', '', b_match.group(2))).strip()
        batch_links.append({
            "title": b_title or "Download Batch",
            "url": b_url,
            "is_batch": True,
        })

    result = {
        "ok": True,
        "title": title,
        "url": clean_url,
        "poster": poster,
        "synopsis": synopsis,
        "episodes": episodes,
        "batch_links": batch_links,
    }
    _set_cached_data(cache_key, result)
    return result


def get_samehadaku_download_links(page_url: str, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Mengekstrak daftar unduhan per format dan resolusi dari halaman episode atau batch Samehadaku."""
    clean_url = str(page_url or "").strip()
    if not clean_url:
        return []

    cache_key = "dl_" + hashlib.md5(clean_url.encode("utf-8")).hexdigest()
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    try:
        response = requests.get(clean_url, headers=_get_headers(), timeout=timeout)
        if response.status_code != 200:
            app_log(f"Samehadaku download page fetch failed: {response.status_code}", "WARNING")
            return []
        html_text = response.text
    except Exception as error:
        app_log(f"Samehadaku download page network error: {error}", "ERROR")
        return []

    # Temukan kontainer download-eps
    dl_area = re.search(
        r'<div[^>]*class=["\'][^"\']*download-eps[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>',
        html_text,
    )
    if not dl_area:
        # Fallback container
        dl_area = re.search(
            r'<div[^>]*class=["\'][^"\']*download-eps[^"\']*["\'][^>]*>([\s\S]*?)</div>',
            html_text,
        )

    if not dl_area:
        return []

    content = dl_area.group(1)

    # Split berdasarkan bagian format, e.g. <p><b>MKV</b></p>
    format_parts = re.split(r'<p><b>(.*?)</b></p>', content)
    results: List[Dict[str, Any]] = []

    # Jika ada pembagian format <p><b>...</b></p>
    if len(format_parts) >= 3:
        idx = 1
        while idx < len(format_parts):
            fmt = html.unescape(format_parts[idx]).strip()
            sec_content = format_parts[idx + 1]
            idx += 2

            # Parse setiap <li><strong>Quality</strong>...</li>
            li_items = re.findall(
                r'<li[^>]*>\s*<strong>(.*?)</strong>\s*<div[^>]*class=["\']download-eps-links["\'][^>]*>([\s\S]*?)</div>\s*</li>',
                sec_content,
            )
            for quality_raw, links_html in li_items:
                quality = re.sub(r'<[^>]+>', '', quality_raw).strip()
                links = _parse_link_elements(links_html)
                if links:
                    results.append({
                        "format": fmt,
                        "quality": quality,
                        "links": links,
                    })
    else:
        # Format tunggal / langsung <li>
        li_items = re.findall(
            r'<li[^>]*>\s*<strong>(.*?)</strong>\s*<div[^>]*class=["\']download-eps-links["\'][^>]*>([\s\S]*?)</div>\s*</li>',
            content,
        )
        for quality_raw, links_html in li_items:
            quality = re.sub(r'<[^>]+>', '', quality_raw).strip()
            links = _parse_link_elements(links_html)
            if links:
                results.append({
                    "format": "MKV",
                    "quality": quality,
                    "links": links,
                })

    _set_cached_data(cache_key, results)
    return results


def _parse_link_elements(links_html: str) -> List[Dict[str, Any]]:
    """Helper untuk mengekstrak <a> tags dan menentukan direct download Pixeldrain."""
    links: List[Dict[str, Any]] = []
    for a_match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', links_html):
        link_url = a_match.group(1).strip()
        host_name = html.unescape(re.sub(r'<[^>]+>', '', a_match.group(2))).strip()
        if not link_url or not host_name:
            continue

        is_pixeldrain = "pixeldrain.com" in link_url.lower()
        pixeldrain_id = None
        direct_download_url = None

        if is_pixeldrain:
            pd_match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', link_url)
            if pd_match:
                pixeldrain_id = pd_match.group(1)
                direct_download_url = f"https://pixeldrain.com/api/file/{pixeldrain_id}?download"

        links.append({
            "host": host_name,
            "url": link_url,
            "is_direct": bool(direct_download_url),
            "is_pixeldrain": is_pixeldrain,
            "pixeldrain_id": pixeldrain_id,
            "direct_download_url": direct_download_url,
        })
    return links


def get_pixeldrain_file_info(file_id: str, timeout: int = 8) -> Optional[Dict[str, Any]]:
    """Ambil informasi metadata file dari Pixeldrain API (nama berkas, ukuran, mime-type)."""
    clean_id = str(file_id or "").strip()
    if not clean_id:
        return None
    url = f"https://pixeldrain.com/api/file/{clean_id}/info"
    try:
        r = requests.get(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "size": data.get("size", 0),
                    "mime_type": data.get("mime_type"),
                    "can_download": data.get("can_download", True),
                }
    except Exception as error:
        app_log(f"Pixeldrain info check error: {error}", "DEBUG")
    return None


def resolve_mediafire_direct_url(page_url: str, timeout: int = 10) -> Optional[str]:
    """Ekstraksi URL unduhan langsung dari tautan halaman Mediafire."""
    clean_url = str(page_url or "").strip()
    if not clean_url or "mediafire.com" not in clean_url.lower():
        return None

    try:
        r = requests.get(clean_url, headers=_get_headers(), timeout=timeout)
        if r.status_code == 200:
            m = re.findall(r'href=[\'"](https?://download\d*\.mediafire\.com/[^\'"]+)[\'"]', r.text)
            if m:
                return m[0].strip()
    except Exception as error:
        app_log(f"Failed to resolve Mediafire link {clean_url}: {error}", "DEBUG")
    return None


def get_samehadaku_latest_episodes(page: int = 1, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Mengambil daftar episode anime terbaru dari halaman beranda Samehadaku."""
    safe_page = max(1, min(10, int(page or 1)))
    cache_key = f"latest_episodes_p{safe_page}"
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    url = SAMEHADAKU_BASE_URL if safe_page == 1 else f"{SAMEHADAKU_BASE_URL}/page/{safe_page}/"
    try:
        response = requests.get(url, headers=_get_headers(), timeout=timeout)
        if response.status_code != 200:
            app_log(f"Samehadaku latest episodes failed with status {response.status_code}", "WARNING")
            return []
        html_text = response.text
    except Exception as error:
        app_log(f"Samehadaku latest episodes network error: {error}", "ERROR")
        return []

    results: List[Dict[str, Any]] = []

    # 1. Cari elemen daftar episode terbaru dalam kontainer post-show
    items = re.findall(
        r'<li[^>]*itemtype=["\']http://schema\.org/CreativeWork["\'][^>]*>([\s\S]*?)</li>',
        html_text,
    )

    for item in items:
        # Thumbnail & link episode
        thumb_link_m = re.search(
            r'<div class=["\']thumb["\'][^>]*>\s*<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']*)["\']',
            item,
        )
        if not thumb_link_m:
            thumb_link_m = re.search(r'<div class=["\']thumb["\'][^>]*>\s*<a\s+[^>]*href=["\']([^"\']+)["\']', item)
            episode_url = thumb_link_m.group(1).strip() if thumb_link_m else ""
            raw_title = ""
        else:
            episode_url = thumb_link_m.group(1).strip()
            raw_title = thumb_link_m.group(2).strip()

        img_m = re.search(r'<img\s+[^>]*?\bsrc=["\']([^"\']+)["\']', item)
        thumb_img = img_m.group(1).strip() if img_m else ""

        # Judul anime dan tautan hub anime
        anime_link_m = re.search(
            r'<h2[^>]*class=["\']entry-title["\'][^>]*>\s*<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            item,
        )
        if anime_link_m:
            anime_url = anime_link_m.group(1).strip()
            title = re.sub(r'<[^>]+>', '', anime_link_m.group(2)).strip()
        else:
            anime_url = ""
            title = raw_title

        title = html.unescape(title)

        # Nomor / label episode
        ep_m = re.search(r'<b>Episode</b>\s*([\s\S]*?)</span>', item)
        ep_num = re.sub(r'<[^>]+>', '', ep_m.group(1)).strip() if ep_m else ""

        # Tanggal rilis
        rel_m = re.search(r'<b>Released on:</b>\s*([\s\S]*?)</span>', item)
        released_on = re.sub(r'<[^>]+>', '', rel_m.group(1)).strip() if rel_m else ""

        # Penulis / Uploader
        author_m = re.search(r'itemprop=["\']name["\']>([^<]+)</span>', item)
        author = author_m.group(1).strip() if author_m else ""

        results.append({
            "title": title,
            "episode": f"Episode {ep_num}" if ep_num else "",
            "episode_number": ep_num,
            "episode_url": episode_url,
            "anime_url": anime_url,
            "thumbnail": thumb_img,
            "released_on": released_on,
            "author": author,
        })

    # 2. Fallback: jika post-show tidak ditemukan atau format berbeda
    if not results:
        articles = re.findall(
            r'<article[^>]*class=["\'][^"\']*animepost[^"\']*["\'][^>]*>([\s\S]*?)</article>',
            html_text,
        )
        for art in articles:
            link_m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']*)["\']', art)
            if not link_m:
                continue
            href = link_m.group(1).strip()
            title = html.unescape(link_m.group(2).strip())
            img_m = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', art)
            img_src = img_m.group(1).strip() if img_m else ""
            ep_m = re.search(r'episode-(\d+(?:\.\d+)?)', href, re.I)
            ep_num = ep_m.group(1) if ep_m else ""

            results.append({
                "title": title,
                "episode": f"Episode {ep_num}" if ep_num else "",
                "episode_number": ep_num,
                "episode_url": href,
                "anime_url": "",
                "thumbnail": img_src,
                "released_on": "",
                "author": "",
            })

    _set_cached_data(cache_key, results)
    return results
