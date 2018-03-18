#!/usr/bin/env python3.6
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import re
import urllib

from network import http_get

api_token = os.getenv('PHAB_API_TOKEN')
if not api_token:
    raise Exception('You must have the PHAB_API_TOKEN environment variable '
                    'set to a valid Phabricator API token.')


class Revision:

    def __init__(self, revision_url):
        parts = urllib.parse.urlparse(revision_url)

        try:
            self.revision_id = re.match('D([\d]+)',
                                        parts.path.lstrip('/')).group(1)
        except AttributeError:
            raise Exception(f'invalid revision URL "{revision_url}"')

        self.base_url = f'{parts.scheme}://{parts.netloc}'

    def phid(self):
        return http_get(
            f'{self.base_url}/api/differential.revision.search',
            f'phid-{self.revision_id}',
            data={
                'constraints[ids][0]': self.revision_id,
                'api.token': api_token,
            }
        )['result']['data'][0]['phid']
