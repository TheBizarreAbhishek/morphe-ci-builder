import os
import re
import sys
import json
import shutil
import zipfile
import tempfile
import datetime
import concurrent.futures
from apk_dl import download_archive, get_archive_versions, get_apkmirror_versions, download_apkmirror, download_file
from builder import (
    get_latest_release_tag,
    download_github_release_asset,
    get_patch_supported_versions,
    run_patching_cli,
    sign_apk,
    build_magisk_module
)
from github_utils import create_github_release, update_magisk_auto_updater

# Utility to strip unused native libs from stock APK to reduce size
def strip_apk_native_libs(apk_path, arch):
    print(f"[+] Stripping unused architectures from {apk_path} (Target: {arch})...")
    
    # Define which libs to keep based on target arch
    keep_patterns = []
    if arch == "arm64-v8a":
        keep_patterns = ["lib/arm64-v8a/"]
    elif arch in ("arm-v7a", "armeabi-v7a"):
        keep_patterns = ["lib/armeabi-v7a/"]
    elif arch == "x86":
        keep_patterns = ["lib/x86/"]
    elif arch == "x86_64":
        keep_patterns = ["lib/x86_64/"]
        
    temp_apk = apk_path + ".temp"
    try:
        with zipfile.ZipFile(apk_path, 'r') as zin:
            with zipfile.ZipFile(temp_apk, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    name = item.filename
                    # If it's a native library, only copy it if it matches our keep_patterns
                    if name.startswith("lib/"):
                        # If we want to keep specific architectures
                        if keep_patterns and any(name.startswith(p) for p in keep_patterns):
                            zout.writestr(item, zin.read(name))
                        # If we are doing 'module' or stripping everything (non-lib code)
                        elif arch == "strip-all":
                            # Strip all native libs from the stock APK inside module
                            pass
                        # Otherwise, copy it if we don't have keep patterns (e.g. arch = all)
                        elif not keep_patterns:
                            zout.writestr(item, zin.read(name))
                    else:
                        # Copy all non-lib files
                        zout.writestr(item, zin.read(name))
        os.replace(temp_apk, apk_path)
        print("[+] Stripping complete.")
        return True
    except Exception as e:
        print(f"[-] Stripping failed: {e}", file=sys.stderr)
        if os.path.exists(temp_apk):
            os.remove(temp_apk)
        return False

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        return {"apps": []}
    with open(config_path, "r") as f:
        return json.load(f)

def check_config_updates(config, build_md_path):
    """
    Checks if there are new patch updates compared to the previous builds logged in build.md.
    """
    print("[+] Checking for config updates...")
    logged_patches = ""
    if os.path.exists(build_md_path):
        with open(build_md_path, "r") as f:
            logged_patches = f.read()

    updated_apps = []
    
    for app in config.get("apps", []):
        if not app.get("enabled", True):
            continue
            
        patches_src = app.get("patches_source", "ReVanced/revanced-patches")
        patches_ver = app.get("patches_version", "latest")
        
        # Get latest tag for patches repo
        latest_tag = get_latest_release_tag(patches_src)
        if not latest_tag:
            continue
            
        log_line = f"Patches: {patches_src}/{latest_tag}"
        if log_line not in logged_patches:
            print(f"[+] Found update for {app.get('name')}: {log_line}")
            updated_apps.append(app.get("name"))
            
    return updated_apps

def download_gmscore(build_dir):
    """
    Downloads the latest MicroG-RE APK for non-root users to the build output.
    """
    print("[+] Downloading latest MicroG-RE for non-root users...")
    gmscore_repo = "MorpheApp/MicroG-RE"
    latest_tag = get_latest_release_tag(gmscore_repo)
    if not latest_tag:
        print("[-] Could not resolve latest MicroG-RE tag.", file=sys.stderr)
        return False
        
    gmscore_apk_path = os.path.join(build_dir, f"MicroG-RE-{latest_tag}.apk")
    if os.path.exists(gmscore_apk_path):
        print(f"[+] MicroG-RE {latest_tag} already downloaded.")
        return True
        
    success = download_github_release_asset(gmscore_repo, latest_tag, ".apk", gmscore_apk_path)
    if success:
        print(f"[+] Downloaded MicroG-RE to {gmscore_apk_path}")
        return True
    return False

def resolve_app_download_info(app, bin_dir, temp_dir):
    """
    Resolves compatible versions and scrapes download links in parallel.
    """
    app_name = app.get("app_name", app.get("name"))
    pkg_name = app.get("pkg_name")
    patches_src = app.get("patches_source", "ReVanced/revanced-patches")
    patches_ver = app.get("patches_version", "latest")
    cli_src = app.get("cli_source", "ReVanced/revanced-cli")
    cli_ver = app.get("cli_version", "latest")
    integrations_src = app.get("integrations_source") or (patches_src.replace("-patches", "-integrations") if "-patches" in patches_src else None)
    
    rv_brand = app.get("rv_brand", "ReVanced")
    version_mode = app.get("version", "auto")
    apk_source = app.get("apk_source", "apkmirror")
    apkmirror_dlurl = app.get("apkmirror_dlurl")
    archive_dlurl = app.get("archive_dlurl")
    direct_dlurl = app.get("direct_dlurl")
    arch = app.get("arch", "all")
    
    cli_jar = os.path.join(bin_dir, f"{cli_src.split('/')[-1]}-{cli_ver}.jar")
    patches_jar = os.path.join(bin_dir, f"{patches_src.split('/')[-1]}-{patches_ver}.jar")
    integrations_apk = os.path.join(bin_dir, f"{integrations_src.split('/')[-1]}-latest.apk") if integrations_src else None
    
    # 1. Resolve compatible versions
    supported_versions = get_patch_supported_versions(cli_jar, patches_jar, pkg_name)
    
    target_version = None
    if version_mode == "auto":
        if supported_versions:
            target_version = supported_versions[-1]
        else:
            version_mode = "latest"
            
    if version_mode == "latest":
        if apk_source == "apkmirror" and apkmirror_dlurl:
            apkm_vers = get_apkmirror_versions(apkmirror_dlurl)
            if apkm_vers:
                target_version = apkm_vers[-1]
        elif apk_source == "archive" and archive_dlurl:
            archive_vers = get_archive_versions(archive_dlurl)
            if archive_vers:
                target_version = archive_vers[-1]
                
    elif version_mode not in ("auto", "latest"):
        target_version = version_mode
        
    if not target_version:
        return {"app": app, "error": f"Could not resolve target version for {app_name}."}
        
    stock_apk = os.path.join(temp_dir, f"stock-{pkg_name}-{target_version}-{arch}.apk")
    
    return {
        "app": app,
        "app_name": app_name,
        "pkg_name": pkg_name,
        "rv_brand": rv_brand,
        "cli_jar": cli_jar,
        "patches_jar": patches_jar,
        "integrations_apk": integrations_apk,
        "target_version": target_version,
        "stock_apk": stock_apk,
        "apk_source": apk_source,
        "apkmirror_dlurl": apkmirror_dlurl,
        "archive_dlurl": archive_dlurl,
        "direct_dlurl": direct_dlurl,
        "arch": arch
    }

def main():
    config = load_config()
    
    # Directories setup
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, "temp")
    bin_dir = os.path.join(base_dir, "bin")
    build_dir = os.path.join(base_dir, "build")
    template_dir = os.path.join(base_dir, "module-template")
    build_md_path = os.path.join(base_dir, "build.md")
    
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)

    # 1. Config update check option
    if len(sys.argv) > 1 and sys.argv[1] == "--config-update":
        updates = check_config_updates(config, build_md_path)
        if updates:
            print(" ".join(updates))
        sys.exit(0)

    changelog_entries = []
    build_succeeded = False
    
    print("[+] Starting Universal Patched Apps Build...")

    # Step 1: Pre-download unique tools in parallel
    unique_tools = {}
    for app in config.get("apps", []):
        if not app.get("enabled", True):
            continue
        cli_src = app.get("cli_source", "ReVanced/revanced-cli")
        cli_ver = app.get("cli_version", "latest")
        patches_src = app.get("patches_source", "ReVanced/revanced-patches")
        patches_ver = app.get("patches_version", "latest")
        integrations_src = app.get("integrations_source") or (patches_src.replace("-patches", "-integrations") if "-patches" in patches_src else None)
        
        cli_jar = os.path.join(bin_dir, f"{cli_src.split('/')[-1]}-{cli_ver}.jar")
        patches_jar = os.path.join(bin_dir, f"{patches_src.split('/')[-1]}-{patches_ver}.jar")
        
        unique_tools[cli_jar] = (cli_src, cli_ver, ".jar")
        unique_tools[patches_jar] = (patches_src, patches_ver, ".jar")
        
        if integrations_src:
            integrations_apk = os.path.join(bin_dir, f"{integrations_src.split('/')[-1]}-latest.apk")
            unique_tools[integrations_apk] = (integrations_src, "latest", ".apk")

    print("[+] Downloading build tools in parallel...")
    def download_tool_task(local_path, info):
        if os.path.exists(local_path):
            return True
        repo, tag, ext = info
        return download_github_release_asset(repo, tag, ext, local_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(download_tool_task, path, info): path for path, info in unique_tools.items()}
        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                success = future.result()
                if not success:
                    print(f"[-] Failed to download tool: {path}", file=sys.stderr)
            except Exception as e:
                print(f"[-] Exception downloading tool {path}: {e}", file=sys.stderr)

    # Step 2: Resolve target versions and download URLs in parallel
    print("[+] Resolving versions and download links in parallel...")
    resolved_apps = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(resolve_app_download_info, app, bin_dir, temp_dir): app for app in config.get("apps", []) if app.get("enabled", True)}
        for future in concurrent.futures.as_completed(futures):
            app = futures[future]
            try:
                info = future.result()
                if "error" in info:
                    print(f"[-] Error resolving info: {info['error']}", file=sys.stderr)
                else:
                    resolved_apps.append(info)
            except Exception as e:
                print(f"[-] Exception resolving info: {e}", file=sys.stderr)

    # Step 3: Download base APKs and GmsCore in parallel
    print("[+] Downloading base APKs in parallel...")
    def download_apk_task(info):
        stock_apk = info["stock_apk"]
        if os.path.exists(stock_apk):
            return True
            
        direct_dlurl = info["direct_dlurl"]
        apk_source = info["apk_source"]
        archive_dlurl = info["archive_dlurl"]
        apkmirror_dlurl = info["apkmirror_dlurl"]
        target_version = info["target_version"]
        arch = info["arch"]
        
        download_success = False
        if direct_dlurl:
            download_success = download_file(direct_dlurl, stock_apk)
        elif apk_source == "archive" and archive_dlurl:
            download_success = download_archive(archive_dlurl, target_version, arch, stock_apk)
        elif apk_source == "apkmirror" and apkmirror_dlurl:
            download_success = download_apkmirror(apkmirror_dlurl, target_version, arch, stock_apk)
            
        if not download_success:
            if archive_dlurl:
                download_success = download_archive(archive_dlurl, target_version, arch, stock_apk)
            elif apkmirror_dlurl:
                download_success = download_apkmirror(apkmirror_dlurl, target_version, arch, stock_apk)
                
        return download_success

    # Check if we need to download GmsCore (if any app compiles standalone APKs)
    has_apk_builds = any(info["app"].get("build_mode", "both") in ("apk", "both") for info in resolved_apps)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit APK downloads
        apk_futures = {executor.submit(download_apk_task, info): info for info in resolved_apps}
        # Submit GmsCore download if needed
        gmscore_future = executor.submit(download_gmscore, build_dir) if has_apk_builds else None
        
        # Await APK downloads
        for future in concurrent.futures.as_completed(apk_futures):
            info = apk_futures[future]
            try:
                success = future.result()
                info["download_success"] = success
                if not success:
                    print(f"[-] Download failed for base APK of {info['app_name']}", file=sys.stderr)
            except Exception as e:
                info["download_success"] = False
                print(f"[-] Exception downloading APK: {e}", file=sys.stderr)
                
        # Await GmsCore download
        if gmscore_future:
            try:
                gmscore_future.result()
            except Exception as e:
                print(f"[-] Exception downloading GmsCore: {e}", file=sys.stderr)

    # Step 4: Patching phase (Sequential to prevent CPU thrashing)
    print("[+] Entering Patching phase...")
    apksigner_jar = os.path.join(bin_dir, "apksigner.jar")
    keystore_path = os.path.join(base_dir, "custom.keystore")

    for info in resolved_apps:
        if not info.get("download_success"):
            continue
            
        app = info["app"]
        app_name = info["app_name"]
        pkg_name = info["pkg_name"]
        rv_brand = info["rv_brand"]
        cli_jar = info["cli_jar"]
        patches_jar = info["patches_jar"]
        integrations_apk = info["integrations_apk"]
        target_version = info["target_version"]
        stock_apk = info["stock_apk"]
        arch = info["arch"]
        
        build_mode = app.get("build_mode", "both")
        module_prop_name = app.get("module_prop_name", f"quasar-{app_name.lower()}-{rv_brand.lower()}")
        
        excluded_patches = app.get("exclude_patches", [])
        included_patches = app.get("include_patches", [])
        exclusive_patches = app.get("exclusive_patches", False)
        patcher_args = app.get("patcher_args", "")
        
        print(f"\n[+] Patching {app_name} ({rv_brand}) v{target_version}...")
        
        unsigned_patched_apk = os.path.join(temp_dir, f"patched-unsigned-{pkg_name}.apk")
        if os.path.exists(unsigned_patched_apk):
            os.remove(unsigned_patched_apk)
            
        # Copy stock APK for patching and strip architectures
        stock_apk_to_patch = os.path.join(temp_dir, f"stock-patching-{pkg_name}.apk")
        shutil.copy2(stock_apk, stock_apk_to_patch)
        strip_apk_native_libs(stock_apk_to_patch, arch)
        
        # Add integrations parameter if integrations file is available
        patch_cmd_args = patcher_args
        if integrations_apk and os.path.exists(integrations_apk):
            patch_cmd_args += f" -m '{integrations_apk}'"
            
        patched = run_patching_cli(
            cli_jar=cli_jar,
            patches_jar=patches_jar,
            stock_apk=stock_apk_to_patch,
            unsigned_patched_apk=unsigned_patched_apk,
            excluded_patches=excluded_patches,
            included_patches=included_patches,
            exclusive_patches=exclusive_patches,
            patcher_args=patch_cmd_args
        )
        
        if os.path.exists(stock_apk_to_patch):
            os.remove(stock_apk_to_patch)
            
        if not patched or not os.path.exists(unsigned_patched_apk):
            print(f"[-] Patching failed for {app_name}.", file=sys.stderr)
            continue
            
        # Sign the patched APK
        signed_apk = os.path.join(temp_dir, f"patched-signed-{pkg_name}.apk")
        if os.path.exists(signed_apk):
            os.remove(signed_apk)
            
        signed = sign_apk(
            apksigner_jar=apksigner_jar,
            keystore_path=keystore_path,
            unsigned_apk=unsigned_patched_apk,
            signed_apk=signed_apk
        )
        
        if not signed or not os.path.exists(signed_apk):
            print(f"[-] Signing failed for {app_name}.", file=sys.stderr)
            continue
            
        # Package outputs
        patched_apk_version = target_version
        
        if build_mode in ("apk", "both"):
            # Standalone APK
            final_apk_name = f"{app_name.lower()}-{rv_brand.lower()}-v{patched_apk_version.replace('.', '-')}-{arch}.apk"
            final_apk_path = os.path.join(build_dir, final_apk_name)
            shutil.copy2(signed_apk, final_apk_path)
            print(f"[+] Saved standalone APK: {final_apk_path}")
            
        if build_mode in ("module", "both"):
            # Strip native libraries from the stock APK inside module to save size
            stock_stripped_apk = os.path.join(temp_dir, f"stock-stripped-{pkg_name}.apk")
            shutil.copy2(stock_apk, stock_stripped_apk)
            strip_apk_native_libs(stock_stripped_apk, "strip-all")
            
            # Magisk Module ZIP
            module_zip = build_magisk_module(
                patched_apk=signed_apk,
                stock_apk=stock_stripped_apk,
                app_name=app_name,
                pkg_name=pkg_name,
                version=patched_apk_version,
                arch=arch,
                module_prop_name=module_prop_name,
                rv_brand=rv_brand,
                build_dir=build_dir,
                template_dir=template_dir
            )
            
            if os.path.exists(stock_stripped_apk):
                os.remove(stock_stripped_apk)
                
        # Clean up temp files
        if os.path.exists(unsigned_patched_apk):
            os.remove(unsigned_patched_apk)
        if os.path.exists(signed_apk):
            os.remove(signed_apk)
            
        # Log successful build details
        changelog_entries.append(f"* **{app_name} ({rv_brand})**: v{patched_apk_version} (Arch: {arch})")
        changelog_entries.append(f"  Patches: {info['patches_jar'].split('/')[-1]}")
        changelog_entries.append(f"  CLI: {info['cli_jar'].split('/')[-1]}")
        
        build_succeeded = True

    # 5. Write changelog and run Git/GitHub Release steps
    if build_succeeded:
        build_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changelog_content = f"# Build Release Logs\n\nBuild date: {build_time}\n\n" + "\n".join(changelog_entries) + "\n"
        with open(build_md_path, "w") as f:
            f.write(changelog_content)
        print(f"[+] Changelog saved to: {build_md_path}")
        
        # Check if running in GITHUB ACTIONS for Release
        repo_env = os.environ.get("GITHUB_REPOSITORY")
        next_ver_code = os.environ.get("NEXT_VER_CODE") or datetime.datetime.now().strftime("%Y%m%d")
        
        if repo_env:
            create_github_release(
                repo=repo_env,
                tag=next_ver_code,
                title=f"Release {next_ver_code}",
                body=changelog_content,
                assets_dir=build_dir
            )
            update_magisk_auto_updater(
                build_dir=build_dir,
                repo=repo_env,
                tag_name=next_ver_code
            )
    else:
        print("[-] All builds failed or no builds executed.")

if __name__ == "__main__":
    main()
