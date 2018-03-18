#!/usr/bin/env python3.6
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

cache_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'cache')
if not os.path.exists(cache_path):
    os.mkdir(cache_path)


def http_get(url, name, is_json=True, data=None):
    if data:
        data = urlencode(data).encode('ascii')

    cache_file = os.path.join(cache_path, name)
    if os.path.exists(cache_file):
        with open(cache_file, encoding='utf-8') as f:
            if is_json:
                return json.load(f)
            else:
                return f.read()
    with open(cache_file, 'w', encoding='utf-8') as f:
        print(f'fetching {url}', file=sys.stderr)
        if is_json:
            content = json.load(urlopen(url, data=data))
            json.dump(content, f, indent=2, sort_keys=True)
        else:
            content = urlopen(url, data=data).read().decode('utf-8')
            f.write(content)
        return content
