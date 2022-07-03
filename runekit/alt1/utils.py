import codecs

import requests


def fetch_bom_json(url: str):
    req = requests.get(url)
    req.raise_for_status()

    if req.content[:3] == codecs.BOM_UTF8:
        req.encoding = "utf-8-sig"

    return req.json()
