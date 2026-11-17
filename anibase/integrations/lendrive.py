# anibase/integrations/lendrive.py
"""Integrasi scraper dan direct link Lendrive (lendrive.web.id).

Menyediakan fungsi pencarian anime, parsing daftar episode dari postingan anime/hub,
parsing tautan unduhan (spesialisasi x265/HEVC Dual Subtitle Indonesia & English
dengan berbagai resolusi: 1080p, 720p, 480p, 10-bit), dan resolusi tautan langsung
(direct download) untuk file hosting seperti Pixeldrain dan Mediafire.
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
from anibase.settings import load_settings
from anibase.integrations.samehadaku import (
    resolve_mediafire_direct_url,
    get_pixeldrain_file_info,
)

LENDRIVE_DEFAULT_BASE_URL = "https://lendrive.web.id"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15
CACHE_TTL_SECONDS = 3600  # 1 jam


def get_lendrive_base_url() -> str:
    """Mengambil base URL Lendrive dari settings jika ada, atau menggunakan default."""
    try:
        st = load_settings()
        custom = st.get("lendrive_base_url", "").strip()
        if custom and (custom.startswith("http://") or custom.startswith("https://")):
            return custom.rstrip("/")
    except Exception:
        pass
    return LENDRIVE_DEFAULT_BASE_URL


def _get_cache_dir() -> str:
    cache_path = os.path.join(CACHE_DIR, "lendrive")
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
        app_log(f"Failed to read Lendrive cache: {error}", "DEBUG")
        return None


_LAST_LENDRIVE_CHALLENGE = False


def is_lendrive_challenge_page(html_text: str) -> bool:
    """Mengecek apakah respon HTML adalah gerbang verifikasi bot/WAF challenge."""
    if not html_text:
        return False
    low = html_text.lower()
    return (
        "please wait while your request is being verified" in low
        or "<title>one moment, please...</title>" in low
        or "wsidchk" in low
    )


def get_lendrive_challenge_status() -> bool:
    """Mengembalikan status apakah request terakhir terhambat WAF challenge."""
    global _LAST_LENDRIVE_CHALLENGE
    return bool(_LAST_LENDRIVE_CHALLENGE)


def set_lendrive_challenge_status(status: bool) -> None:
    """Menyetel status challenge Lendrive."""
    global _LAST_LENDRIVE_CHALLENGE
    _LAST_LENDRIVE_CHALLENGE = bool(status)


def clear_lendrive_cache() -> int:
    """Membersihkan seluruh berkas cache Lendrive di disk."""
    cache_dir = _get_cache_dir()
    count = 0
    try:
        for f in os.listdir(cache_dir):
            fp = os.path.join(cache_dir, f)
            if os.path.isfile(fp) and f.endswith(".json"):
                try:
                    os.remove(fp)
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count


def _set_cached_data(cache_key: str, data: Any) -> None:
    if not data:
        return
    cache_file = os.path.join(_get_cache_dir(), f"{cache_key}.json")
    try:
        tmp_file = f"{cache_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_file, cache_file)
    except Exception as error:
        app_log(f"Failed to write Lendrive cache: {error}", "DEBUG")


def _get_headers() -> Dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "id,en-US;q=0.9,en;q=0.8",
        "Referer": get_lendrive_base_url(),
    }
    try:
        settings = load_settings()
        cookie = str(settings.get("lendrive_cookie") or "").strip()
        if cookie:
            headers["Cookie"] = cookie
    except Exception:
        pass
    return headers


def clean_lendrive_title(raw_title: str) -> str:
    """Membersihkan judul anime dari tag rilis seperti [Dual Subs], [HEVC], Sub Indo, dll."""
    title = html.unescape(raw_title or "").strip()
    # Hapus tag kurung siku di awal/akhir
    title = re.sub(r'\[(?:LENDRIVE|Dual\s*Subs?|HEVC|x265|1080p|720p|480p|10bit|Batch|BD|BluRay)[^\]]*\]', '', title, flags=re.I)
    # Hapus suffix umum Lendrive/Samehadaku
    title = re.sub(r'\s*(?:[-–—]\s*)?(?:Dual\s*Subs?|Subtitle\s*Indonesia|Sub\s*Indo|Lendrive|HEVC|x265).*$', '', title, flags=re.I)
    # Hapus sisa strip / simbol di akhir
    title = re.sub(r'[\s\-–—]+$', '', title).strip()
    return title


def extract_lendrive_episode_number(text: str) -> Optional[str]:
    """Ekstraksi nomor episode dari string judul atau link Lendrive."""
    patterns = [
        r'\b(?:Ep|Eps|Episode)\s*[-_.]?\s*(\d+(?:\.\d+)?)\b',
        r'[-_]\s*(\d{1,4})\s*(?:\[|\(|\.mkv|\.mp4|$)',
        r'[-_]ep(?:isode)?[-_](\d+)',
        r'\b(\d{1,3})\s*(?:Dual\s*Subs?|HEVC)',
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            val = m.group(1).lstrip("0") or "0"
            return val
    return None


def _parse_lendrive_post_cards(html_text: str) -> List[Dict[str, Any]]:
    """Helper untuk mem-parsing kartu artikel/postingan dari HTML Lendrive."""
    results = []
    seen_urls = set()

    # 1. Cari pola <article> atau <div class="post-item|bs|box-item">
    cards = re.findall(
        r'<(?:article|div)[^>]*class=["\'][^"\']*(?:post|item|bs|article|box)[^"\']*["\'][^>]*>([\s\S]*?)</(?:article|div)>',
        html_text,
    )
    if not cards:
        # Fallback pencarian tautan dengan gambar thumbnail
        cards = re.findall(r'<a\s+[^>]*href=["\'][^"\']+["\'][^>]*>[\s\S]*?<img[^>]+>[\s\S]*?</a>', html_text)

    for card in cards:
        # Link & Title
        link_m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']*)["\']', card)
        if link_m:
            href = link_m.group(1).strip()
            raw_title = link_m.group(2).strip()
        else:
            link_m = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\']', card)
            if not link_m:
                continue
            href = link_m.group(1).strip()
            raw_title = ""

        if not raw_title:
            title_m = re.search(
                r'<(?:h2|h3|div|span)[^>]*class=["\'][^"\']*(?:title|entry-title|tt)[^"\']*["\'][^>]*>([\s\S]*?)</(?:h2|h3|div|span)>',
                card,
            )
            if title_m:
                raw_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

        if not raw_title:
            # Fallback ambil teks dari anchor
            text_m = re.search(r'<a[^>]*>([\s\S]*?)</a>', card)
            if text_m:
                raw_title = re.sub(r'<[^>]+>', '', text_m.group(1)).strip()

        if not href or href in seen_urls:
            continue
        # Abaikan tautan navigasi, tag, kategori umum, atau halaman statis
        if any(ign in href for ign in ("/tag/", "/category/", "/author/", "/page/", "/feed/", "wp-json", "comments")):
            continue

        seen_urls.add(href)
        clean_title = clean_lendrive_title(raw_title)

        # Thumbnail
        img_m = re.search(r'<img\s+[^>]*?\bsrc=["\']([^"\']+)["\']', card)
        img_src = img_m.group(1).strip() if img_m else ""
        if "data:image" in img_src:
            # Cek lazy load data-src
            data_src = re.search(r'data-src=["\']([^"\']+)["\']', card)
            if data_src:
                img_src = data_src.group(1).strip()

        # Episode number jika rilis episode
        ep_num = extract_lendrive_episode_number(raw_title) or extract_lendrive_episode_number(href)
        is_batch = bool(re.search(r'\bbatch\b', raw_title, re.I) or "/batch/" in href)
        is_episode = bool(ep_num and not is_batch)
        is_anime = not is_episode and not is_batch

        # Cek tag HEVC / Dual Subs
        has_hevc = bool(re.search(r'hevc|x265', raw_title, re.I))
        has_dualsubs = bool(re.search(r'dual\s*subs?', raw_title, re.I))

        # Format label
        badge_label = "x265 HEVC" if has_hevc else "Anime"
        if has_dualsubs:
            badge_label += " (DualSubs)"

        results.append({
            "title": clean_title or raw_title,
            "raw_title": raw_title,
            "url": href,
            "thumbnail": img_src,
            "episode": f"Episode {ep_num}" if ep_num else ("Batch" if is_batch else ""),
            "episode_number": ep_num,
            "is_batch": is_batch,
            "is_episode": is_episode,
            "is_anime": is_anime,
            "format_badge": badge_label,
            "provider": "lendrive",
        })

    return results


def search_lendrive(query: str, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Cari anime di Lendrive berdasarkan judul."""
    global _LAST_LENDRIVE_CHALLENGE
    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    cache_key = "search_" + hashlib.md5(clean_query.lower().encode("utf-8")).hexdigest()
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    base_url = get_lendrive_base_url()
    search_url = f"{base_url}/?s={urllib.parse.quote_plus(clean_query)}"
    results = []

    try:
        response = requests.get(search_url, headers=_get_headers(), timeout=timeout)
        if response.status_code == 200:
            if is_lendrive_challenge_page(response.text):
                _LAST_LENDRIVE_CHALLENGE = True
                app_log("Lendrive search received WAF anti-bot verification challenge", "WARN")
                return []
            _LAST_LENDRIVE_CHALLENGE = False
            results = _parse_lendrive_post_cards(response.text)
        else:
            app_log(f"Lendrive search returned HTTP {response.status_code} for query: {clean_query}", "WARN")
    except Exception as error:
        app_log(f"Lendrive search network error: {error}", "ERROR")

    if results:
        _set_cached_data(cache_key, results)
    return results


def get_lendrive_latest_episodes(page: int = 1, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Mengambil rilisan anime/episode terbaru dari Lendrive."""
    global _LAST_LENDRIVE_CHALLENGE
    cache_key = f"latest_page_{page}"
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    base_url = get_lendrive_base_url()
    url = f"{base_url}/page/{page}/" if page > 1 else f"{base_url}/"

    results = []
    try:
        response = requests.get(url, headers=_get_headers(), timeout=timeout)
        if response.status_code == 200:
            if is_lendrive_challenge_page(response.text):
                _LAST_LENDRIVE_CHALLENGE = True
                app_log("Lendrive latest releases received WAF anti-bot verification challenge", "WARN")
                return []
            _LAST_LENDRIVE_CHALLENGE = False
            results = _parse_lendrive_post_cards(response.text)
        else:
            app_log(f"Lendrive latest releases returned HTTP {response.status_code}", "WARN")
    except Exception as error:
        app_log(f"Lendrive latest releases network error: {error}", "ERROR")

    if results:
        _set_cached_data(cache_key, results)
    return results


def _parse_lendrive_link_elements(links_html: str) -> List[Dict[str, Any]]:
    """Helper mengekstrak mirror links dan menandai direct link seperti Pixeldrain dan Mediafire."""
    links: List[Dict[str, Any]] = []
    for a_match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', links_html):
        link_url = a_match.group(1).strip()
        host_name = html.unescape(re.sub(r'<[^>]+>', '', a_match.group(2))).strip()
        if not link_url or not host_name:
            continue

        # Abaikan tautan internal situs
        if "lendrive.web.id" in link_url and not any(k in link_url for k in ("download", "file", "drive")):
            continue

        is_pixeldrain = "pixeldrain.com" in link_url.lower()
        is_mediafire = "mediafire.com" in link_url.lower()
        is_gofile = "gofile.io" in link_url.lower()
        is_gdrive = "drive.google.com" in link_url.lower()
        is_kraken = "krakenfiles.com" in link_url.lower()

        pixeldrain_id = None
        direct_download_url = None

        if is_pixeldrain:
            pd_match = re.search(r'pixeldrain\.com/(?:u|api/file)/([a-zA-Z0-9]+)', link_url)
            if pd_match:
                pixeldrain_id = pd_match.group(1)
                direct_download_url = f"https://pixeldrain.com/api/file/{pixeldrain_id}?download"

        links.append({
            "host": host_name,
            "url": link_url,
            "is_direct": bool(direct_download_url),
            "is_pixeldrain": is_pixeldrain,
            "is_mediafire": is_mediafire,
            "is_gofile": is_gofile,
            "is_gdrive": is_gdrive,
            "is_kraken": is_kraken,
            "pixeldrain_id": pixeldrain_id,
            "direct_download_url": direct_download_url,
        })
    return links


def get_lendrive_download_links(page_url: str, timeout: int = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Mengambil dan mem-parsing tabel/blok link unduhan dari postingan Lendrive."""
    clean_url = str(page_url or "").strip()
    if not clean_url:
        return []

    cache_key = "dl_links_" + hashlib.md5(clean_url.encode("utf-8")).hexdigest()
    cached = _get_cached_data(cache_key)
    if cached is not None:
        return cached

    results: List[Dict[str, Any]] = []

    try:
        response = requests.get(clean_url, headers=_get_headers(), timeout=timeout)
        if response.status_code != 200:
            app_log(f"Lendrive download links page returned HTTP {response.status_code}", "WARN")
            return []
        html_text = response.text
    except Exception as error:
        app_log(f"Lendrive download links network error: {error}", "ERROR")
        return []

    # 1. Cari kontainer download khas WordPress anime (download-box, soraurl, box-download, dl-box)
    dl_blocks = re.findall(
        r'<(?:div|section|table)[^>]*class=["\'][^"\']*(?:download|soraurl|box-download|dl-box|soraddl|links_table)[^"\']*["\'][^>]*>([\s\S]*?)</(?:div|section|table)>',
        html_text,
    )

    if not dl_blocks:
        # Fallback: ambil blok di dalam <div class="entry-content"> yang memuat resolusi
        entry_content = re.search(r'<div[^>]*class=["\'][^"\']*entry-content[^"\']*["\'][^>]*>([\s\S]*?)</div>', html_text)
        if entry_content:
            dl_blocks = [entry_content.group(1)]

    for block in dl_blocks:
        # Ekstraksi per baris kualitas: <strong>1080p HEVC DualSubs</strong> atau <b>720p</b>
        rows = re.findall(
            r'<(?:strong|b|div|span|h4)[^>]*>\s*([0-9]{3,4}p[^\s<]*.*?)\s*</(?:strong|b|div|span|h4)>\s*([\s\S]*?)(?=<(?:strong|b|div|span|h4)[^>]*>\s*[0-9]{3,4}p|$)',
            block,
            re.I,
        )
        for quality_raw, links_html in rows:
            quality = re.sub(r'<[^>]+>', '', quality_raw).strip()
            # Standarisasi kualitas: "1080p HEVC DualSubs" -> "1080p"
            q_match = re.search(r'\b(480p|720p|1080p|2160p|4k)\b', quality, re.I)
            norm_quality = q_match.group(1).lower() if q_match else quality

            # Format default Lendrive adalah HEVC (x265)
            fmt = "MKV (x265 HEVC)"
            if "mp4" in quality.lower():
                fmt = "MP4 (x265 HEVC)"

            links = _parse_lendrive_link_elements(links_html)
            if links:
                results.append({
                    "format": fmt,
                    "quality": norm_quality,
                    "quality_full": quality,
                    "is_hevc": True,
                    "is_dualsubs": "dual" in quality.lower() or "dualsubs" in quality.lower(),
                    "links": links,
                })

    # Jika parsing baris di atas belum menemukan link, coba pola <li><strong>Quality</strong>...</li>
    if not results:
        li_items = re.findall(
            r'<li[^>]*>\s*<strong[^>]*>(.*?)</strong>\s*([\s\S]*?)</li>',
            html_text,
        )
        for quality_raw, links_html in li_items:
            quality = re.sub(r'<[^>]+>', '', quality_raw).strip()
            q_match = re.search(r'\b(480p|720p|1080p|2160p|4k)\b', quality, re.I)
            if not q_match:
                continue
            norm_quality = q_match.group(1).lower()
            links = _parse_lendrive_link_elements(links_html)
            if links:
                results.append({
                    "format": "MKV (x265 HEVC)",
                    "quality": norm_quality,
                    "quality_full": quality,
                    "is_hevc": True,
                    "is_dualsubs": True,
                    "links": links,
                })

    _set_cached_data(cache_key, results)
    return results


def get_lendrive_anime_details(page_url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Mengambil detail anime, informasi sinopsis, poster, dan daftar episode dari pos Lendrive."""
    clean_url = str(page_url or "").strip()
    if not clean_url:
        return {"ok": False, "error": "URL halaman kosong"}

    cache_key = "anime_details_" + hashlib.md5(clean_url.encode("utf-8")).hexdigest()
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

    # 1. Judul Anime
    title = ""
    h1_m = re.search(r'<h1[^>]*class=["\'][^"\']*(?:entry-title|title)[^"\']*["\'][^>]*>([\s\S]*?)</h1>', html_text)
    if not h1_m:
        h1_m = re.search(r'<h1[^>]*>([\s\S]*?)</h1>', html_text)
    if h1_m:
        title = clean_lendrive_title(re.sub(r'<[^>]+>', '', h1_m.group(1)))

    if not title:
        og_title = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html_text)
        if og_title:
            title = clean_lendrive_title(og_title.group(1))

    # 2. Poster / Thumbnail
    poster = ""
    og_img = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']', html_text)
    if og_img:
        poster = og_img.group(1).strip()
    if not poster:
        thumb_m = re.search(r'<div[^>]*class=["\'][^"\']*(?:thumb|poster)[^"\']*["\'][^>]*>\s*<img[^>]*?\bsrc=["\']([^"\']+)["\']', html_text)
        if thumb_m:
            poster = thumb_m.group(1).strip()

    # 3. Sinopsis
    synopsis = ""
    syn_m = re.search(r'<(?:div|p)[^>]*class=["\'][^"\']*(?:synopsis|entry-content|desc)[^"\']*["\'][^>]*>([\s\S]*?)</(?:div|p)>', html_text)
    if syn_m:
        raw_syn = re.sub(r'<[^>]+>', '', syn_m.group(1)).strip()
        synopsis = html.unescape(raw_syn)

    # 4. Daftar Episode
    # Pada Lendrive, pos serial biasanya menyediakan daftar episode di tabel atau link list
    episodes: List[Dict[str, Any]] = []
    seen_eps = set()

    # Cari daftar episode e.g. <ul class="episodelist"> atau link ke episode lain
    ep_links = re.findall(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
        html_text,
    )
    for href, text in ep_links:
        raw_t = re.sub(r'<[^>]+>', '', text).strip()
        ep_val = extract_lendrive_episode_number(raw_t) or extract_lendrive_episode_number(href)
        if ep_val and ep_val not in seen_eps:
            # Pastikan bukan link menu / kategori
            if any(k in href for k in ("/category/", "/tag/", "/genre/", "/page/")):
                continue
            seen_eps.add(ep_val)
            episodes.append({
                "episode": ep_val,
                "title": f"Episode {ep_val}",
                "url": href,
                "provider": "lendrive",
            })

    # Jika postingan ini sendiri adalah halaman episode tunggal dan daftar di atas kosong
    if not episodes:
        current_ep = extract_lendrive_episode_number(title) or extract_lendrive_episode_number(clean_url)
        if current_ep:
            episodes.append({
                "episode": current_ep,
                "title": f"Episode {current_ep}",
                "url": clean_url,
                "provider": "lendrive",
            })

    # Urutkan episode berdasarkan angka secara menurun (terbaru di atas)
    try:
        episodes.sort(key=lambda x: float(x.get("episode") or 0), reverse=True)
    except (ValueError, TypeError):
        pass

    details = {
        "ok": True,
        "title": title or "Anime Details",
        "url": clean_url,
        "poster": poster,
        "synopsis": synopsis,
        "episodes": episodes,
        "total_episodes": len(episodes),
        "is_hevc": True,
        "is_dualsubs": True,
        "provider": "lendrive",
    }

    _set_cached_data(cache_key, details)
    return details


def resolve_lendrive_best_direct_link(page_url: str, preferred_quality: str = "1080p") -> Optional[Dict[str, Any]]:
    """Mencari dan menyelesaikan direct download link terbaik untuk sebuah episode Lendrive.

    Memprioritaskan resolusi preferred_quality (default 1080p, fallback 720p),
    dan host Pixeldrain -> Mediafire -> direct link.
    """
    downloads = get_lendrive_download_links(page_url)
    if not downloads:
        return None

    # Normalisasi kualitas pilihan
    pref_q = preferred_quality.lower().strip()
    target_tier = None

    # 1. Cari kualitas yang cocok
    for dl in downloads:
        if dl.get("quality", "").lower() == pref_q:
            target_tier = dl
            break

    # Fallback ke kualitas lain jika preferensi tidak tersedia
    if not target_tier and downloads:
        # Urutan fallback: 1080p -> 720p -> 480p -> pertama yang ada
        for fallback_q in ["1080p", "720p", "480p"]:
            for dl in downloads:
                if dl.get("quality", "").lower() == fallback_q:
                    target_tier = dl
                    break
            if target_tier:
                break
        if not target_tier:
            target_tier = downloads[0]

    if not target_tier or not target_tier.get("links"):
        return None

    links = target_tier["links"]

    # 2. Prioritas Host: Pixeldrain (direct API) -> Mediafire -> Direct URL
    for l in links:
        if l.get("is_pixeldrain") and l.get("direct_download_url"):
            return {
                "direct_url": l["direct_download_url"],
                "host": "Pixeldrain",
                "quality": target_tier.get("quality", "1080p"),
                "format": target_tier.get("format", "MKV"),
                "is_direct": True,
                "is_hevc": True,
                "is_dualsubs": target_tier.get("is_dualsubs", True),
                "provider": "lendrive",
            }

    for l in links:
        if l.get("is_mediafire"):
            direct_mf = resolve_mediafire_direct_url(l["url"])
            if direct_mf:
                return {
                    "direct_url": direct_mf,
                    "host": "Mediafire",
                    "quality": target_tier.get("quality", "1080p"),
                    "format": target_tier.get("format", "MKV"),
                    "is_direct": True,
                    "is_hevc": True,
                    "is_dualsubs": target_tier.get("is_dualsubs", True),
                    "provider": "lendrive",
                }

    for l in links:
        if l.get("is_direct") and l.get("direct_download_url"):
            return {
                "direct_url": l["direct_download_url"],
                "host": l.get("host", "Direct"),
                "quality": target_tier.get("quality", "1080p"),
                "format": target_tier.get("format", "MKV"),
                "is_direct": True,
                "is_hevc": True,
                "is_dualsubs": target_tier.get("is_dualsubs", True),
                "provider": "lendrive",
            }

    return None
