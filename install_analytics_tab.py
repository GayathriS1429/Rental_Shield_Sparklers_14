"""Safely add RentalShield's Analytics route and a navigation link to an existing app.
Run this file from the RentalShield project root after extracting the integration bundle.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_FILE = ROOT / "app.py"
TEMPLATE_DIR = ROOT / "templates"
IMPORT_LINE = "from analytics_dashboard import build_analytics_context"
ROUTE_BLOCK = '''# --------------------------------------------------
# RENTALSHIELD ANALYTICS
# --------------------------------------------------

@app.get("/analytics")
def analytics():
    """Show source-backed rental market and neighbourhood context."""
    return render_template(
        "analytics.html",
        **build_analytics_context(BASE_DIR, request.args),
    )


'''


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = path.with_name(f"{path.name}.before_analytics_{stamp}.bak")
    shutil.copy2(path, destination)
    return destination


def patch_app() -> None:
    if not APP_FILE.exists():
        raise FileNotFoundError("app.py was not found. Run this installer from your project root.")
    source = APP_FILE.read_text(encoding="utf-8")
    changed = False

    if IMPORT_LINE not in source:
        import_anchor = "from risk_engine import generate_rental_report"
        if import_anchor not in source:
            raise RuntimeError(
                "Could not find the expected risk_engine import in app.py. "
                "No app.py changes were made. Please send the current app.py."
            )
        source = source.replace(import_anchor, import_anchor + "\n" + IMPORT_LINE, 1)
        changed = True

    if 'def analytics()' not in source and '@app.get("/analytics")' not in source:
        anchor = "# HEALTH CHECK"
        if anchor not in source:
            anchor = 'if __name__ == "__main__":'
        if anchor not in source:
            raise RuntimeError(
                "Could not find a safe insertion point in app.py. "
                "The file was not changed; restore from backup if necessary."
            )
        source = source.replace(anchor, ROUTE_BLOCK + anchor, 1)
        changed = True

    if changed:
        saved = backup(APP_FILE)
        APP_FILE.write_text(source, encoding="utf-8")
        print(f"Updated app.py (backup: {saved.name})")
    else:
        print("app.py already has the Analytics import and route; left unchanged.")


def patch_navigation() -> int:
    if not TEMPLATE_DIR.exists():
        print("No templates/ folder found. The /analytics route will still be available by URL.")
        return 0

    patched = 0
    for path in TEMPLATE_DIR.rglob("*.html"):
        if path.name.lower() == "analytics.html":
            continue
        source = path.read_text(encoding="utf-8")
        if "url_for('analytics')" in source or 'url_for("analytics")' in source:
            continue
        match = re.search(r"<nav\b([^>]*)>(.*?)</nav\s*>", source, flags=re.I | re.S)
        if not match:
            continue

        nav_inner = match.group(2)
        # Prefer adding a list item when the current navigation uses <ul><li>.
        if re.search(r"</ul\s*>", nav_inner, flags=re.I) and re.search(r"<li\b", nav_inner, flags=re.I):
            li_match = re.search(r"<li\b([^>]*)>", nav_inner, flags=re.I)
            a_match = re.search(r"<a\b([^>]*)>", nav_inner, flags=re.I)
            li_attrs = li_match.group(1) if li_match else ""
            class_match = re.search(r'class\s*=\s*(["\'])(.*?)\1', a_match.group(1), flags=re.I) if a_match else None
            a_class = f' class="{class_match.group(2)}"' if class_match else ""
            item = f'<li{li_attrs}><a href="{{{{ url_for(\'analytics\') }}}}"{a_class}>Analytics</a></li>'
            nav_inner = re.sub(r"</ul\s*>", item + "</ul>", nav_inner, count=1, flags=re.I)
        else:
            a_match = re.search(r"<a\b([^>]*)>", nav_inner, flags=re.I)
            class_match = re.search(r'class\s*=\s*(["\'])(.*?)\1', a_match.group(1), flags=re.I) if a_match else None
            a_class = f' class="{class_match.group(2)}"' if class_match else ""
            link = f'<a href="{{{{ url_for(\'analytics\') }}}}"{a_class}>Analytics</a>'
            nav_inner = nav_inner.rstrip() + "\n    " + link + "\n  "

        replacement = match.group(0).replace(match.group(2), nav_inner, 1)
        updated = source[:match.start()] + replacement + source[match.end():]
        saved = backup(path)
        path.write_text(updated, encoding="utf-8")
        print(f"Added Analytics navigation link to {path.relative_to(ROOT)} (backup: {saved.name})")
        patched += 1

    if not patched:
        print("No existing navigation block needed updating (or Analytics link already exists).")
        print("You can still open the page directly at /analytics.")
    return patched


def main() -> None:
    if not (ROOT / "analytics_dashboard.py").exists():
        raise FileNotFoundError("analytics_dashboard.py is missing. Extract all files from the bundle.")
    if not (TEMPLATE_DIR / "analytics.html").exists():
        raise FileNotFoundError("templates/analytics.html is missing. Extract all files from the bundle.")
    patch_app()
    patch_navigation()
    print("\nAnalytics integration complete. Start the app and open /analytics.")


if __name__ == "__main__":
    main()
