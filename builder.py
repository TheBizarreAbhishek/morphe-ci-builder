import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import zipfile
import urllib.request
import urllib.parse
from apk_dl import HEADERS, download_file, get_html

# GitHub API helper to fetch releases
def get_github_releases(repo):
    url = f"https://api.github.com/repos/{repo}/releases"
    req = urllib.request.Request(url, headers=HEADERS)
    # Add GitHub Token if present in environment
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
        
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[-] Failed to fetch releases for {repo}: {e}", file=sys.stderr)
        return []

def get_latest_release_tag(repo):
    releases = get_github_releases(repo)
    if releases:
        # Filter out pre-releases or drafts if possible
        stable_releases = [r for r in releases if not r.get('prerelease') and not r.get('draft')]
        if stable_releases:
            return stable_releases[0]['tag_name']
        return releases[0]['tag_name']
    return None

def download_github_release_asset(repo, tag, file_keyword, output_path):
    releases = get_github_releases(repo)
    if not releases:
        return False
        
    target_release = None
    if tag in ("latest", "dev", None):
        target_release = releases[0]
    else:
        for r in releases:
            if r['tag_name'] == tag:
                target_release = r
                break
                
    if not target_release:
        print(f"[-] Release tag {tag} not found for {repo}", file=sys.stderr)
        return False
        
    assets = target_release.get('assets', [])
    target_asset = None
    for asset in assets:
        name = asset['name']
        # Simple keywords like .jar, .apk, cli
        if file_keyword in name.lower():
            # Exclude signature/asc files
            if not name.endswith('.asc') and not name.endswith('.sha256') and not name.endswith('.md5'):
                target_asset = asset
                break
                
    if not target_asset:
        # Fallback to check if any asset matches
        for asset in assets:
            if not asset['name'].endswith('.asc'):
                target_asset = asset
                break
                
    if not target_asset:
        print(f"[-] No asset matching {file_keyword} found in release {tag} of {repo}", file=sys.stderr)
        return False
        
    download_url = target_asset['browser_download_url']
    return download_file(download_url, output_path)

def get_patch_supported_versions(cli_jar, patches_jar, pkg_name):
    """
    Runs ReVanced CLI to find versions supported by the patches jar.
    """
    print(f"[+] Getting supported versions for package {pkg_name}...")
    cmd = ["java", "-jar", cli_jar, "list-patches", "-p", patches_jar, "--filter-package-name", pkg_name]
    
    # In some older CLI versions, the argument structure might differ:
    # CLI v2/v3/v4 support --patches and -f
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        
        # Parse output to find version list
        # Look for patterns like "Compatible versions: 19.09.37, 19.16.39"
        versions = set()
        # Find all version-like patterns: e.g. 19.09.37
        # We can extract all lines containing "compatible" or matching version lists
        version_pattern = re.compile(r'\b\d+\.\d+\.\d+(?:\.\d+)?\b')
        
        for line in output.split('\n'):
            if pkg_name in line or "compatible" in line.lower() or "version" in line.lower():
                matches = version_pattern.findall(line)
                for m in matches:
                    versions.add(m)
                    
        # Let's clean up any weird parsing by validating version numbers
        valid_versions = []
        for v in sorted(list(versions)):
            parts = v.split('.')
            if len(parts) >= 2 and all(p.isdigit() for p in parts):
                valid_versions.append(v)
                
        # If valid_versions is empty, try running the list-patches with fallback arguments
        if not valid_versions:
            # Fallback format: --patches instead of -p, -f instead of --filter-package-name
            cmd = ["java", "-jar", cli_jar, "list-patches", "--patches", patches_jar, "-f", pkg_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            matches = version_pattern.findall(output)
            for m in matches:
                parts = m.split('.')
                if len(parts) >= 2 and all(p.isdigit() for p in parts):
                    valid_versions.append(m)
                    
        return sorted(list(set(valid_versions)), key=lambda x: [int(c) for c in x.split('.') if c.isdigit()])
    except Exception as e:
        print(f"[-] Error querying supported versions: {e}", file=sys.stderr)
        return []

def run_patching_cli(cli_jar, patches_jar, stock_apk, unsigned_patched_apk, excluded_patches, included_patches, exclusive_patches, patcher_args):
    """
    Calls ReVanced CLI to apply patches.
    """
    print(f"[+] Launching ReVanced CLI patcher...")
    cmd = [
        "java", "-jar", cli_jar,
        "patch",
        "-p", patches_jar,
        "-o", unsigned_patched_apk,
        stock_apk
    ]
    
    # Check if CLI version is v4+ (uses different args, e.g. -p or --patches)
    # We can detect by running java -jar cli.jar --help
    try:
        help_res = subprocess.run(["java", "-jar", cli_jar, "patch", "--help"], capture_output=True, text=True, timeout=5)
        help_out = help_res.stdout + help_res.stderr
        if "--patches" in help_out and "-p" not in help_out:
            # Modify to use --patches
            cmd[3] = "--patches"
    except:
        pass

    # Exclusions
    if excluded_patches:
        for patch in excluded_patches:
            cmd.extend(["-d", patch])
            
    # Inclusions
    if included_patches:
        for patch in included_patches:
            cmd.extend(["-i", patch])
            
    # Exclusive patching mode
    if exclusive_patches:
        cmd.append("--exclusive")
        
    # Additional patcher args (parsed from string)
    if patcher_args:
        # Split args by spaces but respect quotes if necessary
        # Simplest is shell lex split or raw append
        import shlex
        cmd.extend(shlex.split(patcher_args))
        
    print(f"[+] Execute command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("[+] Patching successful!")
            return True
        else:
            print(f"[-] Patching failed with exit code {result.returncode}", file=sys.stderr)
            print(f"[-] Patcher output:\n{result.stdout}\n{result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[-] Patcher execution failed: {e}", file=sys.stderr)
        return False

def sign_apk(apksigner_jar, keystore_path, unsigned_apk, signed_apk):
    """
    Signs the patched APK with our custom keystore using apksigner.jar.
    """
    ks_pass = os.environ.get("KS_PASS", "autopatcherpass")
    ks_alias = os.environ.get("KS_ALIAS", "abhishek babu")
    
    print(f"[+] Signing APK: {unsigned_apk} -> {signed_apk}")
    cmd = [
        "java", "-jar", apksigner_jar,
        "sign",
        "--ks", keystore_path,
        "--ks-pass", f"pass:{ks_pass}",
        "--key-pass", f"pass:{ks_pass}",
        "--ks-key-alias", ks_alias,
        "--out", signed_apk,
        unsigned_apk
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("[+] Signing successful!")
            return True
        else:
            print(f"[-] Signing failed: {result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[-] apksigner execution failed: {e}", file=sys.stderr)
        return False

def build_magisk_module(patched_apk, stock_apk, app_name, pkg_name, version, arch, module_prop_name, rv_brand, build_dir, template_dir):
    """
    Creates a flashable Magisk module ZIP containing the patched APK.
    """
    output_filename = f"{app_name.lower()}-{rv_brand.lower()}-module-v{version.replace('.', '-')}-{arch}.zip"
    output_path = os.path.join(build_dir, output_filename)
    print(f"[+] Packaging Magisk Module: {output_path}")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy template files
        shutil.copytree(template_dir, tmpdir, dirs_exist_ok=True)
        
        # Write config
        module_arch = "arm64" if arch == "arm64-v8a" else ("arm" if arch in ("arm-v7a", "armeabi-v7a") else "")
        repo_env = os.environ.get("GITHUB_REPOSITORY", "")
        github_url = f"https://github.com/{repo_env}" if repo_env else "https://github.com"
        config_content = f'PKG_NAME={pkg_name}\nPKG_VER={version}\nMODULE_ARCH={module_arch}\nGITHUB_URL={github_url}\n'
        with open(os.path.join(tmpdir, "config"), "w") as f:
            f.write(config_content)
            
        # Write module.prop
        # NEXT_VER_CODE can be current date YYYYMMDD
        import datetime
        next_ver_code = datetime.datetime.now().strftime("%Y%m%d")
        
        prop_content = [
            f"id={module_prop_name}",
            f"name={app_name} {rv_brand}",
            f"version=v{version}",
            f"versionCode={next_ver_code}",
            f"author=Abhishek Babu",
            f"description={app_name} {rv_brand} module systemless overlay by Abhishek Babu."
        ]
        
        # Include updateJson if GITHUB_REPOSITORY is present
        repo_env = os.environ.get("GITHUB_REPOSITORY")
        if repo_env:
            update_json_name = f"{app_name.lower()}-{rv_brand.lower()}-update.json"
            update_json_url = f"https://raw.githubusercontent.com/{repo_env}/update/{update_json_name}"
            prop_content.append(f"updateJson={update_json_url}")
            
        with open(os.path.join(tmpdir, "module.prop"), "w") as f:
            f.write("\n".join(prop_content) + "\n")
            
        # Copy patched APK as base.apk
        shutil.copy2(patched_apk, os.path.join(tmpdir, "base.apk"))
        
        # Copy stock APK if available
        if stock_apk and os.path.exists(stock_apk):
            os.makedirs(os.path.join(tmpdir, "stock"), exist_ok=True)
            shutil.copy2(stock_apk, os.path.join(tmpdir, "stock", "base.apk"))
            
        # Create ZIP archive
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(tmpdir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, tmpdir)
                    zipf.write(file_path, arcname)
                    
    print(f"[+] Magisk module created successfully: {output_path}")
    return output_path
