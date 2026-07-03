import os
import re
import json
import time
import logging
import hashlib
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# Setup beautiful console formatting
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

# Adjust to match your exact repository profile 
REPO_OWNER = "3Mahin"
REPO_NAME = "IPTV"
M3U_FILE = "tv.m3u"
PROXY_FOLDER = "streams"
CACHE_FILE = "processed_cache.json"

VALIDATION_TIMEOUT = 45       # Cap execution loop phase
REVALIDATION_INTERVAL = 86400 # 24 Hours in seconds

def create_session():
    """Generates an elite request session disguised as an active media player."""
    session = requests.Session()
    retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleHLS/VLC Player",
        "Accept": "*/*"
    })
    return session

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache_data):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

def is_live_stream(url, session):
    """Verifies stream health cleanly via multi-layer HEAD/GET assessment."""
    try:
        response = session.head(url, timeout=3, allow_redirects=True)
        if response.status_code in [200, 206]:
            return True
    except Exception:
        pass
    try:
        response = session.get(url, timeout=4, stream=True, allow_redirects=True)
        if response.status_code == 200:
            return True
    except Exception:
        return False
    return False

def get_hls_variants(master_url, session):
    """Reads Master Playlists to identify adaptive streaming resolution variations."""
    variants = [{"res": "Original", "url": master_url, "bandwidth": 1500000}]
    if not master_url.lower().endswith(".m3u8"):
        return variants
    try:
        r = session.get(master_url, timeout=3)
        if r.status_code == 200 and "#EXT-X-STREAM-INF" in r.text:
            lines = r.text.splitlines()
            parsed_variants = []
            for idx, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF"):
                    bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                    res_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
                    
                    bw = int(bw_match.group(1)) if bw_match else 1000000
                    res = res_match.group(1) if res_match else f"Variant_{len(parsed_variants)}"
                    v_url = lines[idx + 1].strip() if idx + 1 < len(lines) else None
                    
                    if v_url:
                        if not v_url.startswith("http"):
                            # Resolve relative HLS URLs
                            from urllib.parse import urljoin
                            v_url = urljoin(master_url, v_url)
                        parsed_variants.append({"res": res, "url": v_url, "bandwidth": bw})
            if parsed_variants:
                return parsed_variants
    except Exception:
        pass
    return variants

def clean_filename(name, url):
    if not name:
        return f"stream_{hashlib.md5(url.encode()).hexdigest()[:8]}"
    name = re.sub(r'[^a-zA-Z0-9\s]', '', name).strip().lower().replace(' ', '_')
    name = re.sub(r'_+', '_', name)
    return f"{name}_{hashlib.md5(url.encode()).hexdigest()[:6]}"

def main():
    logger.info("Initializing system processing mapping sequences...")
    session = create_session()
    cache = load_cache()
    
    if not os.path.exists(M3U_FILE):
        logger.error(f"Target file {M3U_FILE} was missing initialization.")
        return

    with open(M3U_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Clear old proxy files directory to prevent dead configuration build ups
    if os.path.exists(PROXY_FOLDER):
        import shutil
        shutil.rmtree(PROXY_FOLDER)
    os.makedirs(PROXY_FOLDER, exist_ok=True)

    lines = content.splitlines()
    entries = []
    current_extinf = None
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF") or line.startswith("# Source:"):
            current_extinf = line
        elif line.startswith("http"):
            if current_extinf:
                entries.append((current_extinf, line))
                current_extinf = None

    logger.info(f"Loaded {len(entries)} raw stream configurations from {M3U_FILE}")
    
    now = time.time()
    valid_entries = []
    to_validate = []

    # Check cache
    for extinf, url in entries:
        if url in cache and cache[url].get("active") and (now - cache[url].get("time", 0)) < REVALIDATION_INTERVAL:
            valid_entries.append((extinf, url))
        else:
            to_validate.append((extinf, url))

    # Validate remaining links concurrently
    logger.info(f"Validating {len(to_validate)} channels against live server endpoints...")
    validated_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {executor.submit(is_live_stream, url, session): (extinf, url) for extinf, url in to_validate}
        for future in as_completed(future_map):
            extinf, url = future_map[future]
            try:
                alive = future.result()
                cache[url] = {"time": now, "active": alive}
                if alive:
                    valid_entries.append((extinf, url))
            except Exception:
                cache[url] = {"time": now, "active": False}
            
            validated_count += 1
            if validated_count % 20 == 0:
                logger.info(f"Validated {validated_count}/{len(to_validate)} items.")

    save_cache(cache)
    logger.info(f"Validation step finished. Verified alive targets matching metrics: {len(valid_entries)}")

    # Generate Proxy Playlists & Build Main Master Output
    master_m3u_lines = ["#EXTM3U"]
    
    for extinf, url in valid_entries:
        # Resolve clean safe unique file naming
        title_match = re.search(r',([^,]+)$', extinf)
        title = title_match.group(1) if title_match else ""
        safe_name = clean_filename(title, url)
        
        # Pull adaptive layer arrays
        variants = get_hls_variants(url, session)
        
        # Build individual proxy file
        proxy_content = ["#EXTM3U", "#EXT-X-VERSION:3"]
        for var in variants:
            proxy_content.append(f"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={var['bandwidth']},RESOLUTION={var['res']}")
            proxy_content.append(var['url'])
            
        proxy_filepath = os.path.join(PROXY_FOLDER, f"{safe_name}.m3u8")
        with open(proxy_filepath, "w", encoding="utf-8") as pf:
            pf.write("\n".join(proxy_content))

        # Build entry point for master output file using Github pages scheme link routing
        public_proxy_url = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/{PROXY_FOLDER}/{safe_name}.m3u8"
        master_m3u_lines.append(extinf)
        master_m3u_lines.append(public_proxy_url)

    # Rewrite master playlist
    with open(M3U_FILE, "w", encoding="utf-8") as mf:
        mf.write("\n".join(master_m3u_lines) + "\n")
        
    logger.info("Pipeline generation ended successfully. All operations written to repository storage structures.")

if __name__ == "__main__":
    main()
