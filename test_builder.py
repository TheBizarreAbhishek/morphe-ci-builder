import os
import sys
import json
import zipfile
import shutil
import tempfile
from builder import build_magisk_module

def test_config_parsing():
    print("[+] Test: Parsing config.json...")
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    assert os.path.exists(config_path), "config.json should exist"
    
    with open(config_path, "r") as f:
        data = json.load(f)
        
    assert "apps" in data, "config.json should contain 'apps'"
    assert len(data["apps"]) > 0, "config.json should contain at least one app"
    
    for app in data["apps"]:
        assert "name" in app, "App config must contain name"
        assert "pkg_name" in app, "App config must contain pkg_name"
        assert "patches_source" in app, "App config must contain patches_source"
        
    print("[+] Test: config.json parsing successful!")

def test_magisk_module_packaging():
    print("[+] Test: Packaging Magisk Module...")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(base_dir, "module-template")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock APKs
        mock_patched_apk = os.path.join(tmpdir, "mock-patched.apk")
        with open(mock_patched_apk, "w") as f:
            f.write("mock-apk-content")
            
        mock_stock_apk = os.path.join(tmpdir, "mock-stock.apk")
        with open(mock_stock_apk, "w") as f:
            f.write("mock-stock-content")
            
        build_dir = os.path.join(tmpdir, "build")
        os.makedirs(build_dir, exist_ok=True)
        
        # Build mock module
        zip_path = build_magisk_module(
            patched_apk=mock_patched_apk,
            stock_apk=mock_stock_apk,
            app_name="TestApp",
            pkg_name="com.test.app",
            version="1.2.3",
            arch="arm64-v8a",
            module_prop_name="test-app-module",
            rv_brand="TestBrand",
            build_dir=build_dir,
            template_dir=template_dir
        )
        
        assert os.path.exists(zip_path), "Module zip should be created"
        
        # Verify ZIP contents
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            namelist = zipf.read('module.prop').decode('utf-8')
            assert "id=test-app-module" in namelist, "module.prop should contain custom ID"
            assert "name=TestApp TestBrand" in namelist, "module.prop should contain custom Name"
            
            config_list = zipf.read('config').decode('utf-8')
            assert "PKG_NAME=com.test.app" in config_list, "config file should contain package name"
            assert "MODULE_ARCH=arm64" in config_list, "config file should contain MODULE_ARCH"
            
            assert "base.apk" in zipf.namelist(), "ZIP should contain base.apk"
            assert "stock/base.apk" in zipf.namelist(), "ZIP should contain stock/base.apk"
            assert "service.sh" in zipf.namelist(), "ZIP should contain service.sh"
            
    print("[+] Test: Magisk Module packaging successful!")

if __name__ == "__main__":
    try:
        test_config_parsing()
        test_magisk_module_packaging()
        print("\n[+] All tests passed successfully!")
    except AssertionError as e:
        print(f"\n[-] Assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[-] Unexpected error during tests: {e}", file=sys.stderr)
        sys.exit(1)
