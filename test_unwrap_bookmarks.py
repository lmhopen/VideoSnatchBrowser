"""Test that Chrome root folder names (书签栏 etc.) are correctly unwrapped"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from videosnatch.bookmark_importer import parse_bookmark_html

# Test 1: Chrome export with "书签栏" folder at top level
test1 = r"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUAL="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000">书签栏</H3>
    <DL><p>
        <DT><A HREF="https://youtube.com/" ADD_DATE="1700000001">YouTube</A>
        <DT><A HREF="https://github.com/" ADD_DATE="1700000002">GitHub</A>
        <DT><H3 ADD_DATE="1700000010">我的文件夹</H3>
        <DL><p>
            <DT><A HREF="https://docs.python.org/" ADD_DATE="1700000011">Python Docs</A>
        </DL><p>
    </DL><p>
</DL><p>
"""

tf1 = "test_chrome_export.html"
with open(tf1, "w", encoding="utf-8") as f:
    f.write(test1)

data1 = parse_bookmark_html(tf1)
bb = data1["roots"]["bookmark_bar"]["children"]

print("=== Test 1: Chrome '书签栏' unwrapped ===")
print(f"Direct children of bookmark_bar: {len(bb)}")
for child in bb:
    if child["type"] == "url":
        print(f"  [URL] {child['name']}")
    elif child["type"] == "folder":
        print(f"  [FOLDER] {child['name']} ({len(child.get('children',[]))} items)")
        for gc in child.get("children", []):
            print(f"    [URL] {gc['name']}")

assert len(bb) == 3, f"Expected 3 direct children (YouTube, GitHub, My Folder), got {len(bb)}"
assert bb[0]["type"] == "url" and "youtube" in bb[0]["url"], "YouTube should be direct child"
assert bb[1]["type"] == "url" and "github" in bb[1]["url"], "GitHub should be direct child"
assert bb[2]["type"] == "folder" and bb[2]["name"] == "我的文件夹", "My Folder should be preserved"
print("PASSED\n")

# Test 2: English "Bookmarks Bar"
test2 = r"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUAL="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000">Bookmarks Bar</H3>
    <DL><p>
        <DT><A HREF="https://google.com/" ADD_DATE="1700000020">Google</A>
    </DL><p>
    <DT><H3 ADD_DATE="1700000030">Other Bookmarks</H3>
    <DL><p>
        <DT><A HREF="https://example.com/" ADD_DATE="1700000031">Example</A>
    </DL><p>
</DL><p>
"""

tf2 = "test_english_export.html"
with open(tf2, "w", encoding="utf-8") as f:
    f.write(test2)

data2 = parse_bookmark_html(tf2)
bb2 = data2["roots"]["bookmark_bar"]["children"]
other2 = data2["roots"]["other"]["children"]

print("=== Test 2: English 'Bookmarks Bar' + 'Other Bookmarks' ===")
print(f"Bookmark bar children: {len(bb2)}")
for c in bb2:
    print(f"  [{'FOLDER' if c['type']=='folder' else 'URL'}] {c['name']}")
print(f"Other bookmarks children: {len(other2)}")
# 书签栏和 Other Bookmarks 都在同一层 DL 内，所以两者都被解包到 bookmark_bar
assert len(bb2) >= 2, f"Expected at least 2 (Google + Example), got {len(bb2)}"
names = {c['name'] for c in bb2}
assert 'Google' in names, 'Google should be in bookmark bar'
assert 'Example' in names, 'Example should be in bookmark bar (from Other Bookmarks unwrap)'
print("PASSED (both 'Bookmarks Bar' and 'Other Bookmarks' unwrapped)\n")

# Test 3: No wrapping - bookmarks directly at root
test3 = r"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUAL="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><A HREF="https://a.com/" ADD_DATE="1700000040">A</A>
    <DT><A HREF="https://b.com/" ADD_DATE="1700000041">B</A>
    <DT><A HREF="https://c.com/" ADD_DATE="1700000042">C</A>
</DL><p>
"""

tf3 = "test_flat.html"
with open(tf3, "w", encoding="utf-8") as f:
    f.write(test3)

data3 = parse_bookmark_html(tf3)
bb3 = data3["roots"]["bookmark_bar"]["children"]
print(f"=== Test 3: Flat (no wrapping) ===")
print(f"Direct children: {len(bb3)}")
assert len(bb3) == 3, f"Expected 3, got {len(bb3)}"
print("PASSED\n")

# Cleanup
for f in [tf1, tf2, tf3]:
    os.remove(f)

print("=== ALL TESTS PASSED ===")
