#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EPG MERGER - Unisce open-epg + epgshare (+ eventi opzionali) in epg.xml / epg.xml.gz.

Sorgenti:
  - https://www.open-epg.com/files/italy{1,2,3,4}.xml
  - https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz
  - events.xml (opzionale): se presente viene incluso; se manca, si salta.

Uso:
  python epg.py [-o epg.xml] [--gz epg.xml.gz] [--eventi-xml events.xml] [--no-eventi]

Note:
  - output nella root del repo (anche se lanciato da altra cartella)
  - solo dipendenza: requests (+ stdlib)
  - mai sovrascrive con un EPG vuoto (RuntimeError se zero channel)
"""

import argparse
import gzip
import io
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: serve 'requests'. Esegui: pip install requests", file=sys.stderr)
    raise

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# CONFIGURAZIONE
# =============================================================================
OUTPUT_XML = "epg.xml"
OUTPUT_GZ = "epg.xml.gz"
EVENTI_XML = "events.xml"
TIMEOUT = 30

URLS_GZIP = [
    'https://www.open-epg.com/files/italy1.xml',
    'https://www.open-epg.com/files/italy2.xml',
    'https://www.open-epg.com/files/italy3.xml',
    'https://www.open-epg.com/files/italy4.xml',
    'https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz'
]


# =============================================================================
# PATH / UTILS
# =============================================================================
def find_repo_root():
    start_dirs = [Path.cwd().resolve(), Path(__file__).resolve().parent]
    for start_dir in start_dirs:
        for directory in (start_dir, *start_dir.parents):
            if (directory / ".git").exists():
                return directory
    return Path.cwd().resolve()


def resolve_path(name):
    p = Path(name)
    if not p.is_absolute():
        p = find_repo_root() / name
    return p


def normalize_tvg_id(name):
    """Normalizzazione unica tvg-id per EPG e M3U: lower, senza accenti, solo [a-z0-9]."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name).strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\.it\b", "", s)
    s = re.sub(r"fullhd|\bhd\b", "", s)
    s = re.sub(r"hd", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


# =============================================================================
# MERGER
# =============================================================================
def download_and_parse_xml(url):
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    try:
        response = requests.get(url, timeout=TIMEOUT, verify=False)
        response.raise_for_status()
        try:
            with gzip.open(io.BytesIO(response.content), 'rb') as f_in:
                xml_content = f_in.read()
        except (gzip.BadGzipFile, OSError):
            xml_content = response.content
        return ET.ElementTree(ET.fromstring(xml_content))
    except requests.exceptions.RequestException as e:
        print(f"Errore durante il download da {url} (verifica SSL disabilitata): {e}")
    except ET.ParseError as e:
        print(f"Errore nel parsing del file XML da {url}: {e}")
    return None


def generate_epg(output_xml=OUTPUT_XML, output_gz=OUTPUT_GZ,
                 eventi_xml=EVENTI_XML, include_eventi=True):
    print("=" * 60)
    print("EPG MERGER - open-epg + epgshare")
    print("=" * 60)
    print()
    t0 = time.time()

    out_xml = resolve_path(output_xml)
    out_gz = resolve_path(output_gz)
    path_eventi = resolve_path(eventi_xml)

    root_finale = ET.Element('tv')
    tree_finale = ET.ElementTree(root_finale)
    seen_channel_ids = set()
    seen_programmes = set()
    cutoff = datetime.now() - timedelta(days=1)

    def parse_epg_time(s):
        try:
            return datetime.strptime((s or "")[:14], "%Y%m%d%H%M%S")
        except Exception:
            return None

    def add_element(element):
        tag = element.tag
        if tag == 'channel':
            nid = normalize_tvg_id(element.get('id', ''))
            if not nid or nid in seen_channel_ids:
                return False
            element.set('id', nid)
            seen_channel_ids.add(nid)
            root_finale.append(element)
            return True
        elif tag == 'programme':
            nch = normalize_tvg_id(element.get('channel', ''))
            if not nch:
                return False
            element.set('channel', nch)
            stop = parse_epg_time(element.get('stop', ''))
            if stop is not None and stop < cutoff:
                return False
            title_el = element.find('title')
            title_txt = title_el.text if title_el is not None and title_el.text else ''
            key = (nch, element.get('start', ''), element.get('stop', ''), title_txt)
            if key in seen_programmes:
                return False
            seen_programmes.add(key)
            root_finale.append(element)
            return True
        else:
            root_finale.append(element)
            return True

    for url in URLS_GZIP:
        tree = download_and_parse_xml(url)
        if tree is None:
            print(f"[{url}] download/parsing fallito, salto")
            continue
        root = tree.getroot()
        tot_c = tot_p = add_c = add_p = 0
        for element in list(root):
            if element.tag == 'channel':
                tot_c += 1
                if add_element(element):
                    add_c += 1
            elif element.tag == 'programme':
                tot_p += 1
                if add_element(element):
                    add_p += 1
            else:
                add_element(element)
        print(f"[{url}] channels {add_c}/{tot_c}, programmes {add_p}/{tot_p}")

    if include_eventi:
        if path_eventi.is_file():
            try:
                tree_eventi = ET.parse(str(path_eventi))
                root_eventi = tree_eventi.getroot()
                tot_c = tot_p = add_c = add_p = 0
                for element in list(root_eventi):
                    if element.tag == 'channel':
                        tot_c += 1
                        if add_element(element):
                            add_c += 1
                    elif element.tag == 'programme':
                        tot_p += 1
                        if add_element(element):
                            add_p += 1
                if tot_c == 0 and tot_p == 0:
                    for channel in root_eventi.findall(".//channel"):
                        tot_c += 1
                        if channel not in list(root_finale) and add_element(channel):
                            add_c += 1
                    for programme in root_eventi.findall(".//programme"):
                        tot_p += 1
                        if add_element(programme):
                            add_p += 1
                print(f"[{path_eventi.name}] channels {add_c}/{tot_c}, programmes {add_p}/{tot_p}")
            except ET.ParseError as e:
                print(f"Errore nel parsing del file {path_eventi.name}: {e}")
        else:
            print(f"File non trovato: {path_eventi} (salto eventi)")
    else:
        print("[INFO] File eventi escluso (--no-eventi).")

    n_ch = len(root_finale.findall("channel"))
    n_pr = len(root_finale.findall("programme"))
    print(f"Totale EPG: {n_ch} channels, {n_pr} programmes")
    if n_ch == 0:
        raise RuntimeError("EPG vuoto: nessun channel dopo il merge, interrompo per non sovrascrivere con file vuoto")

    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree_finale, space="  ")
    with open(out_xml, 'wb') as f_out:
        tree_finale.write(f_out, encoding='utf-8', xml_declaration=True)
    print(f"File XML salvato: {out_xml}")

    # gzip.open su Python 3.10 non accetta mtime: uso GzipFile per build deterministiche
    buf = io.BytesIO()
    tree_finale.write(buf, encoding='utf-8', xml_declaration=True)
    with open(out_gz, 'wb') as f_out:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=6, mtime=0, fileobj=f_out) as f_gz:
            f_gz.write(buf.getvalue())
    print(f"File GZIP salvato: {out_gz}")
    print(f"    ({time.time() - t0:.1f}s)")
    print("=" * 60)
    print("COMPLETATO!")
    print("=" * 60)
    return str(out_xml)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Unisce open-epg + epgshare in epg.xml / epg.xml.gz")
    ap.add_argument("-o", "--output", default=OUTPUT_XML, help=f"File XML di output (default: {OUTPUT_XML})")
    ap.add_argument("--gz", default=OUTPUT_GZ, help=f"File GZIP di output (default: {OUTPUT_GZ})")
    ap.add_argument("--eventi-xml", default=EVENTI_XML, help=f"File eventi XML opzionale (default: {EVENTI_XML})")
    ap.add_argument("--no-eventi", action="store_true", help="Escludi il file eventi dal merge")
    args = ap.parse_args()

    generate_epg(args.output, output_gz=args.gz, eventi_xml=args.eventi_xml,
                 include_eventi=not args.no_eventi)
