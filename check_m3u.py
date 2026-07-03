import asyncio
import aiohttp

# Path configured to check your exact tv.m3u file
M3U_FILE = "tv.m3u" 

async def check_url(session, url, timeout=5):
    """Checks if a streaming link returns a valid response."""
    # Try HEAD request first (efficient)
    try:
        async with session.head(url, timeout=timeout, allow_redirects=True) as response:
            if response.status in [200, 201, 206, 301, 302]:
                return True
    except Exception:
        pass
    
    # Fallback to GET because some IPTV links explicitly reject HEAD requests
    try:
        async with session.get(url, timeout=timeout, allow_redirects=True) as response:
            if response.status in [200, 201, 206, 301, 302]:
                return True
    except Exception:
        return False
    return False

async def main():
    try:
        with open(M3U_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: {M3U_FILE} not found in the root directory!")
        return

    lines = content.splitlines()
    header = "#EXTM3U\n"
    
    entries = []
    current_extinf = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF") or line.startswith("# Source:"):
            if current_extinf is None:
                current_extinf = line
            else:
                current_extinf += "\n" + line
        elif not line.startswith("#"):
            if current_extinf:
                entries.append((current_extinf, line))
                current_extinf = None
            else:
                entries.append(("", line))

    print(f"Found {len(entries)} channels in {M3U_FILE} to verify. Starting check...")

    valid_entries = []
    
    # Disable SSL checking for robust stream verification
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_url(session, url) for _, url in entries]
        results = await asyncio.gather(*tasks)
        
        for i, is_valid in enumerate(results):
            extinf, url = entries[i]
            if is_valid:
                valid_entries.append((extinf, url))
                print(f"[LIVE] {url}")
            else:
                print(f"[DEAD - REMOVED] {url}")

    # Rebuild the file
    new_m3u = header
    for extinf, url in valid_entries:
        if extinf:
            new_m3u += f"{extinf}\n"
        new_m3u += f"{url}\n"

    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write(new_m3u)
        
    print(f"Cleanup complete. Kept {len(valid_entries)} out of {len(entries)} streams.")

if __name__ == "__main__":
    asyncio.run(main())
