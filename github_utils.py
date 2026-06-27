import os
import re
import sys
import json
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request

def run_command(cmd, cwd=None):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def gh_cli_available():
    ok, stdout, _ = run_command("gh --version")
    return ok

def create_github_release(repo, tag, title, body, assets_dir):
    """
    Creates or updates a GitHub Release and uploads all files in assets_dir.
    Uses 'gh' CLI if available, otherwise prints manual instructions.
    """
    if not gh_cli_available():
        print("[-] 'gh' CLI is not available. Skipping GitHub Release creation.", file=sys.stderr)
        return False

    print(f"[+] Creating/Updating GitHub Release for tag: {tag}...")
    assets = [os.path.join(assets_dir, f) for f in os.listdir(assets_dir) if os.path.isfile(os.path.join(assets_dir, f))]
    if not assets:
        print("[-] No assets found to upload.", file=sys.stderr)
        return False

    assets_str = " ".join([f'"{a}"' for a in assets])
    
    # Check if release already exists
    release_exists, _, _ = run_command(f'gh release view "{tag}"')
    
    if release_exists:
        print(f"[+] Release for tag {tag} already exists. Editing notes and uploading assets...")
        # Edit release notes
        run_command(f'gh release edit "{tag}" --title "{title}" --notes "{body}"')
        # Upload assets with clobber (overwrite)
        ok, stdout, stderr = run_command(f'gh release upload "{tag}" {assets_str} --clobber')
    else:
        print(f"[+] Creating new release for tag {tag}...")
        ok, stdout, stderr = run_command(f'gh release create "{tag}" {assets_str} --title "{title}" --notes "{body}"')
        
    if ok:
        print(f"[+] GitHub Release for {tag} completed successfully.")
        return True
    else:
        print(f"[-] GitHub Release failed: {stderr}", file=sys.stderr)
        return False

def update_magisk_auto_updater(build_dir, repo, tag_name):
    """
    Updates the update.json files on the 'update' branch.
    Uses Git to switch to 'update' branch, write JSONs, commit, and push.
    """
    print("[+] Updating Magisk Auto-Updater files...")
    
    # Verify we are in a git repository
    is_git, _, _ = run_command("git rev-parse --is-inside-work-tree")
    if not is_git:
        print("[-] Not a Git repository. Skipping auto-updater update.", file=sys.stderr)
        return False
        
    # Find all module zip files in build_dir
    modules = [f for f in os.listdir(build_dir) if f.endswith('.zip') and 'module' in f.lower()]
    if not modules:
        print("[*] No Magisk modules built. Skipping auto-updater JSONs.")
        return True
        
    # Read metadata for each module
    module_updates = []
    for mod in modules:
        zip_path = os.path.join(build_dir, mod)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                # Read module.prop
                if 'module.prop' in zipf.namelist():
                    prop_data = zipf.read('module.prop').decode('utf-8', errors='ignore')
                    
                    # Extract fields
                    version = None
                    version_code = None
                    update_json_name = None
                    
                    for line in prop_data.split('\n'):
                        if line.startswith('version='):
                            version = line.split('=')[1].strip()
                        elif line.startswith('versionCode='):
                            version_code = line.split('=')[1].strip()
                        elif line.startswith('updateJson='):
                            # e.g., https://raw.githubusercontent.com/user/repo/update/youtube-update.json
                            update_json_name = line.split('/')[-1].strip()
                            
                    if version and version_code and update_json_name:
                        # Construct download URL for release asset
                        dl_url = f"https://github.com/{repo}/releases/download/{tag_name}/{mod}"
                        module_updates.append({
                            'json_name': update_json_name,
                            'version': version,
                            'versionCode': int(version_code) if version_code.isdigit() else 1,
                            'zipUrl': dl_url,
                            'changelog': f"https://raw.githubusercontent.com/{repo}/update/build.md"
                        })
        except Exception as e:
            print(f"[-] Error reading module prop for {mod}: {e}", file=sys.stderr)

    if not module_updates:
        print("[*] No valid module.prop files with updateJson found.")
        return True

    # Store current branch name
    _, current_branch, _ = run_command("git branch --show-current")
    if not current_branch:
        current_branch = "main"
    current_branch = current_branch.strip()

    # Read build.md content before checking out to preserve it
    changelog_content = ""
    if os.path.exists("build.md"):
        try:
            with open("build.md", "r") as f:
                changelog_content = f.read()
        except Exception as e:
            print(f"[*] Warning: Could not read build.md: {e}", file=sys.stderr)

    # Fetch updates from origin
    run_command("git fetch origin")

    # Checkout or create update branch
    print("[+] Switching to 'update' branch...")
    checkout_ok, _, _ = run_command("git checkout update")
    if not checkout_ok:
        checkout_ok, _, _ = run_command("git checkout -b update origin/update")
    if not checkout_ok:
        # Create a fresh orphan branch
        checkout_ok, _, _ = run_command("git switch --orphan update")
        # Remove all existing files to start clean
        run_command("git rm -rf .")

    if not checkout_ok:
        print("[-] Failed to switch/create 'update' branch.", file=sys.stderr)
        return False

    # Write build.md to branch as changelog
    if changelog_content:
        with open("build.md", "w") as f:
            f.write(changelog_content)
    else:
        if not os.path.exists("build.md"):
            with open("build.md", "w") as f:
                f.write("# Changelog\n\nAutomated rolling releases update.\n")

    # Write each update.json file
    for item in module_updates:
        json_data = {
            "version": item['version'],
            "versionCode": item['versionCode'],
            "zipUrl": item['zipUrl'],
            "changelog": item['changelog']
        }
        with open(item['json_name'], 'w') as f:
            json.dump(json_data, f, indent=2)
        print(f"[+] Wrote auto-updater JSON: {item['json_name']}")

    # Configure Git user if running in actions
    if os.environ.get("GITHUB_ACTIONS"):
        run_command('git config user.name "github-actions[bot]"')
        run_command('git config user.email "github-actions[bot]@users.noreply.github.com"')

    # Commit and push
    run_command("git add build.md *-update.json")
    commit_ok, _, _ = run_command(f'git commit -m "Update Magisk modules auto-updater - Release {tag_name}"')
    if commit_ok:
        print("[+] Committed changes to 'update' branch. Pushing to origin...")
        push_ok, _, push_err = run_command("git push origin HEAD:update --force")
        if not push_ok:
            print(f"[-] Git push failed: {push_err}", file=sys.stderr)
    else:
        print("[*] No changes to commit on 'update' branch.")

    # Return to starting branch
    print(f"[+] Returning to original branch: {current_branch}...")
    run_command(f"git checkout {current_branch}")
    return True
