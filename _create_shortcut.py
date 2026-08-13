"""
Create a desktop shortcut for ContactPuller on Windows.
Called by Create Desktop Shortcut.bat — do not run directly.
"""
import sys
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
ICON_PATH   = PROJECT_DIR / "assets" / "logo.ico"
MAIN_PY     = PROJECT_DIR / "main.py"
DESKTOP     = Path.home() / "Desktop"
SHORTCUT    = DESKTOP / "ContactPuller.lnk"

# Find the Python executable being used
PYTHON_EXE  = sys.executable


def create_shortcut_powershell():
    """Create .lnk via PowerShell WScript.Shell (no extra deps)."""
    icon_line = f"$s.IconLocation = '{ICON_PATH}';" if ICON_PATH.exists() else ""
    ps_script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{SHORTCUT}')
$s.TargetPath = '{PYTHON_EXE}'
$s.Arguments = '"{MAIN_PY}"'
$s.WorkingDirectory = '{PROJECT_DIR}'
$s.Description = 'ContactPuller - O&G Sales Intelligence'
{icon_line}
$s.Save()
"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as f:
        f.write(ps_script)
        ps_file = f.name
    os.system(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ps_file}"')
    os.unlink(ps_file)


def create_shortcut_win32():
    """Create .lnk via pywin32 (preferred, more reliable)."""
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(SHORTCUT))
    sc.TargetPath      = PYTHON_EXE
    sc.Arguments       = f'"{MAIN_PY}"'
    sc.WorkingDirectory= str(PROJECT_DIR)
    sc.Description     = "ContactPuller - O&G Sales Intelligence"
    if ICON_PATH.exists():
        sc.IconLocation = str(ICON_PATH)
    sc.save()


if __name__ == "__main__":
    print(f"Creating shortcut: {SHORTCUT}")
    try:
        create_shortcut_win32()
        print("[+] Shortcut created via win32com.")
    except ImportError:
        create_shortcut_powershell()
        print("[+] Shortcut created via PowerShell.")
    except Exception as e:
        print(f"[!] win32com failed ({e}), trying PowerShell...")
        create_shortcut_powershell()

    if SHORTCUT.exists():
        print(f"\nContactPuller shortcut added to Desktop.")
    else:
        print(f"\n[!] Shortcut may not have been created. Check Desktop manually.")
