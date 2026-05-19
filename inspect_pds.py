#!/usr/bin/env python3
"""Quick diagnostic: show the first 40 lines of the downloaded PDS file."""
import requests, sys

URL = (
    "https://sbnarchive.psi.edu/pds3/non_mission/"
    "EAR_A_I0035_5_SDSSTAX_V1_1/data/sdsstax_ast_table.tab"
)

print(f"Fetching {URL} ...")
r = requests.get(URL, headers={"User-Agent": "diagnostic/1.0"}, timeout=60, stream=True)
r.raise_for_status()

# Read first 8 KB
chunk = b""
for c in r.iter_content(chunk_size=1024):
    chunk += c
    if len(chunk) >= 8192:
        break
r.close()

print(f"\nFirst 8 KB received ({len(chunk)} bytes)")
print(f"First 4 bytes (hex): {chunk[:4].hex()}")
print(f"\n--- First 40 lines ---")
text = chunk.decode("latin-1", errors="replace")
for i, line in enumerate(text.splitlines()[:40]):
    print(f"{i+1:3d}: {repr(line)}")
