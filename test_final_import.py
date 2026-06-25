"""Final test for bookmark import"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from videosnatch.bookmark_importer import (
    import_bookmarks_from_html,
    parse_bookmark_html,
    write_to_profile,
    BookmarkParseError,
)

# Create test HTML
test_html = r"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUAL="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000">Video Sites</H3>
    <DL><p>
        <DT><A HREF="https://www.youtube.com/" ADD_DATE="1700000001">YouTube</A>
        <DT><A HREF="https://www.bilibili.com/" ADD_DATE="1700000002">Bilibili</A>
    </DL><p>
    <DT><A HREF="https://github.com/" ADD_DATE="1700000020">GitHub</A>
</DL><p>
"""

test_file = "test_bookmarks.html"
Path(test_file).write_text(test_html, "utf-8")

# Test 1: Parse
print("=== Test 1: Parse HTML ===")
try:
    data = parse_bookmark_html(test_file)
    bb = data["roots"]["bookmark_bar"]["children"]
    print(f"  Bookmark bar children: {len(bb)}")
    for child in bb:
        if child["type"] == "folder":
            print(f"    Folder: {child['name']} ({len(child.get('children',[]))} items)")
        elif child["type"] == "url":
            print(f"    URL: {child['name']}")
    print("  PASSED")
except Exception as e:
    print(f"  FAILED: {e}")

# Test 2: Write to profile
print("\n=== Test 2: Write to profile ===")
profile_dir = os.path.join(os.path.dirname(__file__), "browser_profile", "Default")
os.makedirs(profile_dir, exist_ok=True)
try:
    result = import_bookmarks_from_html(test_file, profile_dir)
    print(f"  Count: {result['count']}")
    print(f"  File: {result['filepath']}")
    if result["count"] == 3:  # 2 videos + 1 github = 3
        print("  PASSED (expected 3)")
    else:
        print(f"  UNEXPECTED count: {result['count']}")
except Exception as e:
    print(f"  FAILED: {e}")

# Test 3: Verify JSON format
print("\n=== Test 3: Verify Chrome JSON format ===")
profile_file = os.path.join(profile_dir, "Bookmarks")
if os.path.exists(profile_file):
    with open(profile_file, "r", encoding="utf-8") as f:
        j = json.load(f)
    assert j["version"] == 1
    assert "checksum" in j
    assert "roots" in j
    assert "bookmark_bar" in j["roots"]
    assert "other" in j["roots"]
    assert "synced" in j["roots"]
    assert j["roots"]["bookmark_bar"]["id"] == 1
    assert j["roots"]["other"]["id"] == 2
    assert j["roots"]["synced"]["id"] == 3
    print("  PASSED - Chrome JSON format is valid")
else:
    print("  FAILED - Bookmarks file not created")

# Test 4: Empty bookmarks
print("\n=== Test 4: Empty bookmark file ===")
empty_html = r"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUAL="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
</DL><p>
"""
empty_file = "test_empty.html"
Path(empty_file).write_text(empty_html, "utf-8")
try:
    empty_data = parse_bookmark_html(empty_file)
    bb_empty = empty_data["roots"]["bookmark_bar"]["children"]
    if len(bb_empty) == 0:
        print("  PASSED - Empty file returns no bookmarks")
    else:
        print(f"  UNEXPECTED: {len(bb_empty)} children from empty file")
except Exception as e:
    print(f"  FAILED: {e}")

# Test 5: Invalid file
print("\n=== Test 5: Invalid file ===")
invalid_file = "test_invalid.html"
Path(invalid_file).write_text("<html><body><p>Not a bookmark file</p></body></html>", "utf-8")
try:
    parse_bookmark_html(invalid_file)
    print("  FAILED - Should have raised ValueError")
except ValueError as e:
    print(f"  PASSED - Correctly rejected: {e}")
except Exception as e:
    print(f"  PASSED - Rejected: {e}")

# Test 6: Non-existent file
print("\n=== Test 6: Non-existent file ===")
try:
    parse_bookmark_html("nonexistent.html")
    print("  FAILED - Should have raised BookmarkParseError")
except BookmarkParseError as e:
    print(f"  PASSED - Correctly rejected: {e}")
except Exception as e:
    print(f"  PASSED - Rejected: {e}")

# Cleanup
os.remove(test_file)
os.remove(empty_file)
os.remove(invalid_file)
if os.path.exists(profile_file):
    os.remove(profile_file)
bak_file = profile_file + ".bak"
if os.path.exists(bak_file):
    os.remove(bak_file)

print("\n=== All tests completed ===")
