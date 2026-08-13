"""
Create a desktop shortcut for Lead Scout on Windows.
Called by Create Desktop Shortcut.bat — do not run directly.
"""
import sys
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
ICON_PATH   = PROJECT_DIR / "assets" / "logo.ico"
MAIN_PY     = PROJECT_DIR / "main.py"
DESKTOP     = Path.home() / "Desktop"
SHORTCUT    = DESKTOP / "Lead Scout.lnk"

# Use pythonw.exe to suppress the console window on launch
PYTHON_EXE  = Path(sys.executable).with_name("pythonw.exe")
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)  # fallback if pythonw not found


def create_shortcut_powershell():
    """Create .lnk via PowerShell WScript.Shell (no extra deps)."""
    icon_line = f"$s.IconLocation = '{ICON_PATH}';" if ICON_PATH.exists() else ""
    ps_script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{SHORTCUT}')
$s.TargetPath = '{PYTHON_EXE}'
$s.Arguments = '"{MAIN_PY}"'
$s.WorkingDirectory = '{PROJECT_DIR}'
$s.Description = 'Lead Scout - O&G Sales Intelligence'
$s.WindowStyle = 7
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
    sc.Description     = "Lead Scout - O&G Sales Intelligence"
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
        print(f"\nLead Scout shortcut added to Desktop.")
    else:
        print(f"\n[!] Shortcut may not have been created. Check Desktop manually.")
