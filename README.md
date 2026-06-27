# Universal Patched Apps Builder and Magisk Module Automator

This project is an automated, versatile, and optimized builder for patching Android applications (supporting both ReVanced and Morphe ecosystems). It runs daily on GitHub Actions to check for upstream updates, patches the apps, and publishes the standalone APKs and flashable Magisk modules (with built-in auto-updates) directly to GitHub Releases.

## Project Structure

```text
morphe-ci-builder/
├── .github/
│   └── workflows/
│       ├── ci.yml           # Runs daily to check for patch updates
│       └── build.yml        # Performs the build, packaging, and release steps
├── bin/
│   ├── apksigner.jar        # Standard Android APK signer
│   ├── dexlib2.jar          # Library for Dalvik bytecode manipulation
│   └── paccer.jar           # Helper library
├── module-template/         # Template structure for flashable Magisk modules
│   ├── META-INF/            # Bootstrap installer scripts
│   ├── bin/                 # Native helper binaries (e.g. ksu_profile)
│   ├── customize.sh         # Dynamic installer script (handles auto-update/version matching)
│   ├── service.sh           # late_start service to mount APK systemlessly
│   └── uninstall.sh         # Cleans up bind mounts and native libraries on uninstall
├── config.json              # Central configuration file
├── main.py                  # Main build orchestrator and manager
├── builder.py               # Downloader and patching handler
├── apk_dl.py                # APK scraping and fallback download logic
├── github_utils.py          # GitHub Releases API and update branch pushing logic
└── test_builder.py          # Local unit/integration test script
```

---

## Configuration (`config.json`)

Configure your target applications, build preferences, and patching sources in `config.json`.

Example configuration:

```json
{
  "parallel_jobs": 2,
  "compression_level": 9,
  "remove_rv_integrations_checks": true,
  "apps": [
    {
      "name": "YouTube-Morphe",
      "app_name": "YouTube",
      "enabled": true,
      "pkg_name": "com.google.android.youtube",
      "patches_source": "MorpheApp/morphe-patches",
      "cli_source": "MorpheApp/morphe-cli",
      "rv_brand": "Morphe",
      "build_mode": "both",
      "version": "auto",
      "apk_source": "apkmirror",
      "apkmirror_dlurl": "https://www.apkmirror.com/apk/google-inc/youtube",
      "module_prop_name": "quasar-youtube-morphe",
      "arch": "arm64-v8a",
      "exclude_patches": ["sponsorblock"]
    }
  ]
}
```

### Config Key Details

| Key | Description | Default |
| --- | --- | --- |
| `name` | Unique identifier for the app target. | Required |
| `app_name` | Human-readable app name for release logging and titles. | `name` |
| `enabled` | Whether this app target should be built. | `true` |
| `pkg_name` | Package name of the Android application. | Required |
| `patches_source` | Upstream GitHub repository for patches (e.g. `MorpheApp/morphe-patches`). | `ReVanced/revanced-patches` |
| `cli_source` | Upstream GitHub repository for CLI tool. | `ReVanced/revanced-cli` |
| `rv_brand` | Branding tag for the patched output. | `ReVanced` |
| `build_mode` | Build output mode: `apk` (standalone), `module` (Magisk), or `both`. | `both` |
| `version` | App version to build: `auto` (highest compatible), `latest` (absolute latest), or specific version number. | `auto` |
| `apk_source` | Primary source for downloading base APK: `archive` (archive.org open directory) or `apkmirror`. | `archive` |
| `apkmirror_dlurl` | APKMirror category page URL. | Optional |
| `archive_dlurl` | Archive.org directory URL. | Optional |
| `direct_dlurl` | Direct download link for base APK (bypasses check/scrape completely). | Optional |
| `arch` | Target architecture: `arm64-v8a`, `arm-v7a`, or `all`. | `all` |

---

## Setup & Deployment

### 1. Repository Setup
1. Create a new, blank repository on GitHub.
2. Initialize and push the contents of the `morphe-ci-builder` folder to the repository's `main` branch.

### 2. GitHub Actions Permissions
Because the workflow needs to create releases and commit update JSON files to the `update` branch, you must grant write permissions to the runner:
1. Go to your repository **Settings** -> **Actions** -> **General**.
2. Scroll to **Workflow permissions**.
3. Select **Read and write permissions**.
4. Click **Save**.

### 3. Signing Security (GitHub Secrets)
To prevent your private keystore password and alias from being stored in plain text inside the script, you can save them as GitHub Repository Secrets:
1. Go to your repository **Settings** -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret**.
3. Add the following secrets:
   - `KS_PASS`: Set this to your custom password (defaults to `autopatcherpass` if not set).
   - `KS_ALIAS`: Set this to your custom key alias (defaults to `abhishek babu` if not set).

### 4. Verification
- The workflow `CI Scheduler` is set to run daily at 16:00 UTC.
- You can manually trigger the workflow anytime:
  1. Go to the **Actions** tab in your repository.
  2. Select **CI Scheduler** on the left.
  3. Click **Run workflow** -> Select `main` branch -> Click **Run workflow**.

---

## Local Development & Testing

You can build and test your configurations locally.

### Prerequisites
- Python 3.10+
- Java JDK 17+

### Run Tests
To verify code syntax and Magisk packaging logic:
```bash
python3 test_builder.py
```

### Run Build Locally
To execute the builder locally (which downloads the required APKs and patches, then builds outputs into the `build/` folder):
```bash
python3 main.py
```
