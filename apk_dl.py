import os
import re
import sys
import urllib.request
import urllib.parse
from html.parser import HTMLParser
import zipfile

# Standard headers to bypass basic user-agent blocks
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

def download_file(url, output_path):
    print(f"[+] Downloading: {url} -> {output_path}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as response, open(output_path, 'wb') as out_file:
            data = response.read()
            if not data.startswith(b"PK\x03\x04"):
                snippet = data[:500].decode('utf-8', errors='ignore')
                print(f"[-] Download failed: Not a valid ZIP/APK archive. Magic: {data[:4]}", file=sys.stderr)
                print(f"[-] Content Snippet:\n{snippet}\n", file=sys.stderr)
                return False
            out_file.write(data)
            
        # Verify the ZIP structure (for both JARs and APKs)
        try:
            with zipfile.ZipFile(output_path, 'r') as z:
                namelist = z.namelist()
                # AndroidManifest.xml check is only applicable for APK files
                if output_path.endswith('.apk') and "AndroidManifest.xml" not in namelist:
                    print(f"[-] Download rejected: File is a split APK bundle or missing AndroidManifest.xml", file=sys.stderr)
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    return False
        except Exception as ze:
            print(f"[-] Download verification failed: {ze}", file=sys.stderr)
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
            
        print(f"[+] Download complete: {output_path}")
        return True
    except Exception as e:
        print(f"[-] Download failed: {e}", file=sys.stderr)
        return False

def get_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[-] Request to {url} failed: {e}", file=sys.stderr)
        return None

# Simple HTML parser to find links
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for name, value in attrs:
                if name == 'href':
                    self.links.append(value)

def parse_links(html):
    parser = LinkParser()
    parser.feed(html)
    return parser.links

# ----------------- Archive.org Downloader -----------------

def get_archive_versions(archive_url):
    """
    Fetches the archive.org page and returns a list of versions found.
    Filenames are formatted as: [pkg]-[version]-[arch].apk
    """
    html = get_html(archive_url)
    if not html:
        return []
    
    links = parse_links(html)
    versions = set()
    # Pattern to match com.google.android.youtube-20.05.46-all.apk
    # or com.google.android.apps.youtube.music-8.30.54-arm64-v8a.apk
    pattern = re.compile(r'.*?-(?P<version>\d+\.\d+\.\d+(?:\.\d+)?)-(?:all|arm64-v8a|arm-v7a|x86|x86_64)\.apk$')
    
    for link in links:
        match = pattern.match(link)
        if match:
            versions.add(match.group('version'))
            
    return sorted(list(versions), key=lambda x: [int(c) for c in x.split('.') if c.isdigit()])

def download_archive(archive_url, version, arch, output_path):
    """
    Downloads an APK of a specific version and architecture from the archive.org index page.
    """
    html = get_html(archive_url)
    if not html:
        return False
        
    links = parse_links(html)
    
    # Normalize arch names
    normalized_arch = arch
    if arch == "arm64-v8a":
        normalized_arch = "arm64-v8a"
    elif arch in ("arm-v7a", "armeabi-v7a"):
        normalized_arch = "arm-v7a"
    elif arch == "all":
        normalized_arch = "all"
        
    # Search for matching file
    target_file = None
    # Let's check for exact version and architecture
    for link in links:
        if version in link and normalized_arch in link and link.endswith('.apk'):
            target_file = link
            break
            
    # Fallback to 'all' if specific arch is not found
    if not target_file and normalized_arch != "all":
        for link in links:
            if version in link and "all" in link and link.endswith('.apk'):
                target_file = link
                break
                
    # Fallback to any file matching the version if still not found
    if not target_file:
        for link in links:
            if version in link and link.endswith('.apk'):
                target_file = link
                break

    if not target_file:
        print(f"[-] No matching file found for version {version} and arch {arch} at {archive_url}", file=sys.stderr)
        return False
        
    download_url = f"{archive_url.rstrip('/')}/{target_file}"
    return download_file(download_url, output_path)

# ----------------- APKMirror Downloader (Best Effort) -----------------

def get_apkmirror_versions(apkmirror_url):
    """
    Gets the latest versions of an app listed on APKMirror.
    """
    cat_name = apkmirror_url.rstrip('/').split('/')[-1]
    uploads_url = f"https://www.apkmirror.com/uploads/?appcategory={cat_name}"
    html = get_html(uploads_url)
    if not html:
        return []
        
    # Simple regex to extract versions from infoSlide
    # Example: Version:</span><span class="infoSlide-value">19.09.37 </span>
    matches = re.findall(r'Version:</span><span class="infoSlide-value">([^<]+)</span>', html)
    versions = []
    for m in matches:
        version = m.strip()
        # Exclude alpha/beta unless explicitly requested
        if 'beta' not in version.lower() and 'alpha' not in version.lower():
            versions.append(version)
    return versions

def download_apkmirror(apkmirror_url, version, arch, output_path):
    """
    Scrapes and downloads a specific version and arch from APKMirror.
    """
    # 1. Access the version details page
    base_path = apkmirror_url.replace("https://www.apkmirror.com", "").strip('/')
    cat_name = base_path.split('/')[-1]
    version_slug = f"{cat_name}-{version.replace('.', '-')}-release"
    version_url = f"https://www.apkmirror.com/{base_path}/{version_slug}/"
        
    html = get_html(version_url)
    if not html:
        return False
        
    links = parse_links(html)
    
    # 2. Look for the download details page matching arch
    # e.g., /apk/google-inc/youtube/youtube-19-09-37-release/youtube-19-09-37-release-android-apk-download/
    download_details_url = None
    for link in links:
        if f"/{cat_name}/{version_slug}-android-apk-download/" in link:
            download_details_url = f"https://www.apkmirror.com{link}"
            break
            
    if not download_details_url:
        # Fallback search for any download page link
        for link in links:
            if "-android-apk-download/" in link:
                download_details_url = f"https://www.apkmirror.com{link}"
                break
                
    if not download_details_url:
        print(f"[-] Could not find download details page on APKMirror for {version_slug}", file=sys.stderr)
        return False
        
    # 3. Access download details page to get final download button page URL
    html = get_html(download_details_url)
    if not html:
        return False
        
    links = parse_links(html)
    final_button_url = None
    for link in links:
        if "/wp-content/themes/APKMirror/download.php?key=" in link:
            final_button_url = f"https://www.apkmirror.com{link}"
            break
            
    if not final_button_url:
        for link in links:
            if "key=" in link:
                final_button_url = f"https://www.apkmirror.com{link}"
                break
                
    if not final_button_url:
        print(f"[-] Could not find final download button page on APKMirror", file=sys.stderr)
        return False
        
    # 4. Access the final button page to get the direct download link
    html = get_html(final_button_url)
    if not html:
        return False
        
    # The direct link is inside <span class="accent_color"><a href="download.php?id=...">click here</a></span>
    # or starts with span > a[rel="nofollow"]
    # Let's extract all links matching rel="nofollow" or download.php?id=
    links = parse_links(html)
    direct_link = None
    for link in links:
        if "download.php?id=" in link and "key=" in link:
            if link.startswith("http"):
                direct_link = link
            elif link.startswith("/"):
                direct_link = f"https://www.apkmirror.com{link}"
            else:
                direct_link = f"https://www.apkmirror.com/wp-content/themes/APKMirror/{link}"
            break
            
    if not direct_link:
        # regex search for rel="nofollow" links
        matches = re.findall(r'href="([^"]+download\.php\?[^"]+)"', html)
        if matches:
            direct_link = matches[0]
            if not direct_link.startswith('http'):
                direct_link = f"https://www.apkmirror.com{direct_link}"
                
    if not direct_link:
        print(f"[-] Could not find direct download link on final APKMirror page", file=sys.stderr)
        return False
        
    return download_file(direct_link, output_path)
