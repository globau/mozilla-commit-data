#!/usr/bin/env python3.6
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# quick-n-dirty script to consolidate data about a given mozilla-central commit

import datetime
import json
import os
import re
import sys
from email.utils import parseaddr
from urllib.request import urlopen

cache_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'cache')
if not os.path.exists(cache_path):
    os.mkdir(cache_path)


def http_get(url, name, is_json=True):
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
            content = json.load(urlopen(url))
            json.dump(content, f, indent=2, sort_keys=True)
        else:
            content = urlopen(url).read().decode('utf-8')
            f.write(content)
        return content


def parse_bugs(s):
    bug_re = re.compile(
        r'((?:bug|(?=\b#?\d{5,})|^(?=\d))(?:\s*#?)(\d+)(?=\b))', re.I)
    bugs_with_duplicates = [int(m[1]) for m in bug_re.findall(s)]
    bugs = list(set(bugs_with_duplicates))
    return [bug for bug in bugs if bug < 100000000]


def find_attachment(stats, attach_id):
    for p in stats['patches']:
        if p['id'] == attach_id:
            return p
    return None


def is_patch(attachment):
    return (
        attachment['is_patch'] == 1 or
        attachment['content_type'] in (
            'text/x-review-board-request',
            'text/x-github-request',
            'text/x-phabricator-request',
        )
    )


def add_attachment_flag(stats, change_group, change, flagtype):
    if change['field_name'] != 'flagtypes.name':
        return

    for flag in change['added'].split(','):
        flag = flag.strip()

        if flag.startswith(f'{flagtype}?('):
            attachment = find_attachment(stats, change['attachment_id'])
            if not attachment:
                raise Exception(f'attachment {change["attachment_id"]} '
                                'not found')
            requestee = flag[len(f'{flagtype}?('):-1]
            attachment['status'].append(dict(
                status=f'{flagtype}?',
                requestee=requestee,
                timestamp=change_group['when'],
            ))
            stats['people'].append(dict(user=change_group['who'],
                                        rel=f'{flagtype} requester'))
            stats['people'].append(dict(user=requestee,
                                        rel=f'{flagtype} requestee'))

        elif flag == f'{flagtype}+' or flag == f'{flagtype}-':
            attachment = find_attachment(stats, change['attachment_id'])
            if not attachment:
                raise Exception(f'attachment {change["attachment_id"]}'
                                'not found')
            attachment['status'].append(dict(
                status=flag,
                requestee=change_group['who'],
                timestamp=change_group['when'],
            ))
            stats['people'].append(dict(user=change_group['who'],
                                        rel=f'{flagtype} requestee'))


def add_bug_flag(stats, change_group, change, flagtype):
    if change['field_name'] != 'flagtypes.name':
        return

    for flag in change['added'].split(','):
        flag = flag.strip()

        if flag.startswith(f'{flagtype}?('):
            requestee = flag[len(f'{flagtype}?('):-1]
            stats['flags'].append(dict(
                status=f'{flagtype}?',
                requestee=requestee,
                timestamp=change_group['when'],
            ))
            stats['people'].append(dict(user=change_group['who'],
                                        rel=f'{flagtype} requester'))
            stats['people'].append(dict(user=requestee,
                                        rel=f'{flagtype} requestee'))

        elif flag == f'{flagtype}+' or flag == f'{flagtype}-':
            stats['flags'].append(dict(
                status=flag,
                requestee=change_group['who'],
                timestamp=change_group['when'],
            ))
            stats['people'].append(dict(user=change_group['who'],
                                        rel=f'{flagtype} requestee'))

    for flag in change['removed'].split(','):
        flag = flag.strip()

        if flag.startswith(f'{flagtype}?('):
            requestee = flag[len(f'{flagtype}?('):-1]
            flag = dict(
                status=f'{flagtype}X',
                requestee=requestee,
                timestamp=change_group['when'],
            )
            if requestee != change_group['who']:
                flag['actor'] = change_group['who']
            stats['flags'].append(flag)
            stats['people'].append(dict(user=change_group['who'],
                                        rel=f'{flagtype} requestee'))

# noinspection PyTypeChecker
def main(node):
    # hg
    rev = http_get(
        f'https://hg.mozilla.org/mozilla-central/json-rev/{node}',
        f'{node}-hg')
    rev['summary'] = rev['desc'].splitlines()[0]
    rev['user'] = parseaddr(rev['user'])[1]

    if rev['backedoutby']:
        backedout_by = rev['backedoutby'][:12]
        backout = http_get(
            f'https://hg.mozilla.org/mozilla-central/json-rev/{backedout_by}',
            f'{backedout_by}-hg')
        backout['summary'] = backout['desc'].splitlines()[0]
        backout['user'] = parseaddr(backout['user'])[1]
    else:
        backout = None

    # patch
    patch = http_get(
        f'https://hg.mozilla.org/mozilla-central/raw-rev/{node}',
        f'{node}-patch', is_json=False)

    # bug-id
    bugs = parse_bugs(rev['summary'])
    if len(bugs) == 0:
        raise Exception(f'failed to find bug-id in: {rev["summary"]}')
    if len(bugs) > 1:
        raise Exception(f'found multiple bug-ids in: {rev["summary"]}')
    bug_id = bugs[0]

    # bug - meta
    bmo = 'https://bugzilla.mozilla.org/rest'
    bug = http_get(
        f'{bmo}/bug/{bug_id}',
        f'{node}-bug')['bugs'][0]

    # bug - history
    bug_history = http_get(
        f'{bmo}/bug/{bug_id}/history',
        f'{node}-bug_history')['bugs'][0]['history']

    # bug - attachments
    bug_attachments = list(http_get(
        f'{bmo}/bug/{bug_id}/attachment?exclude_fields=data',
        f'{node}-bug-attachments')['bugs'][str(bug_id)])

    # calc stats

    stats = dict(
        bug_url=f'https://bugzilla.mozilla.org/{bug_id}',
        bug_comment_count=bug['comment_count'],
        bug_product=bug['product'],
        bug_component=bug['component'],
        hg_url=f'https://hg.mozilla.org/mozilla-central/rev/{rev["node"][:12]}',
        summary=rev['summary'],

        node=rev['node'],
        author=rev['user'],
        pusher=rev['pushuser'],
        people=[],

        patch_size=len(patch),
        patch_lines_of_code=len(patch.splitlines()),
        patches=[],

        push_timestamp=datetime.datetime.fromtimestamp(
            rev['pushdate'][0]).strftime('%Y-%m-%dT%H:%M:%SZ'),

        bug_id=bug_id,
        bug_created_timestamp=bug['creation_time'],

        assigned_to=[],
        status=[],
        flags=[],

        triaged=[],
    )

    stats['people'].append(dict(user=bug['creator'], rel='reporter'))
    stats['people'].append(dict(user=rev['user'], rel='push author'))
    stats['people'].append(dict(user=rev['pushuser'], rel='push user'))

    if backout:
        stats['backout'] = dict(
            summary=backout['summary'],
            user=backout['user'],
            timestamp=datetime.datetime.fromtimestamp(
                backout['pushdate'][0]).strftime('%Y-%m-%dT%H:%M:%SZ'),
        )
        stats['people'].append(dict(user=backout['user'], rel='backout author'))

    for attachment in bug_attachments:
        if not is_patch(attachment):
            continue
        stats['patches'].append(dict(
            content_type=attachment['content_type'],
            id=attachment['id'],
            timestamp=attachment['creation_time'],
            user=attachment['creator'],
            summary=attachment['summary'],
            status=[],
        ))
        stats['people'].append(dict(user=attachment['creator'],
                                    rel='patch author'))

    for change_group in bug_history:
        for change in change_group['changes']:
            # assigned_to
            if change['field_name'] == 'assigned_to':
                stats['assigned_to'].append(dict(user=change['added'],
                                                 when=change_group['when']))
                stats['people'].append(dict(user=change['added'],
                                            rel='assigned bug'))

            # triage (look for status-flags changed, or a component change)
            if (change['field_name'].startswith('cf_status_firefox')
                    and change['added'] != '---'):
                stats['triaged'].append(dict(
                    user=change_group['who'],
                    action=f'{change["field_name"]}: {change["added"]}',
                    timestamp=change_group['when'],
                ))
                stats['people'].append(dict(user=change_group['who'],
                                            rel='triaged'))

            elif (change['field_name'] == 'component'
                  and change['removed'] == 'Untriaged'):
                stats['triaged'].append(dict(
                    user=change_group['who'],
                    action=f'{change["field_name"]} -> {change["added"]}',
                    timestamp=change_group['when'],
                ))
                stats['people'].append(dict(user=change_group['who'],
                                            rel='triaged'))

            # reviews
            if change['field_name'] == 'flagtypes.name':
                add_attachment_flag(stats, change_group, change, 'review')
                add_attachment_flag(stats, change_group, change, 'feedback')
                add_bug_flag(stats, change_group, change, 'needinfo')

            # attachment obsoletion
            elif (change['field_name'] == 'attachments.isobsolete'
                    and change['added'] == '1'):
                attachment = find_attachment(stats, change['attachment_id'])
                if not attachment:
                    raise Exception(f'attach {change["attachment_id"]}')
                attachment['status'].append(dict(
                    status='obsoleted',
                    timestamp=change_group['when'],
                ))
                stats['people'].append(dict(user=change_group['who'],
                                            rel='obsoleted attachment'))

            # status
            elif change['field_name'] == 'status':
                stats['status'].append(dict(
                    status=change['added'],
                    user=change_group['who'],
                    timestamp=change_group['when'],
                ))
                stats['people'].append(dict(user=change_group['who'],
                                            rel='bug status'))

    # tidy up
    people = {}
    for person in stats['people']:
        user = person['user']
        rel = person['rel']
        if person['user'] not in people:
            people[user] = dict(user=user, rel={})
        people[user]['rel'][rel] = True
    stats['people'] = {}
    for person in people.values():
        stats['people'][person['user']] = sorted(list(set(person['rel'])))

    # display
    print(json.dumps(stats, indent=2, sort_keys=True))


try:
    if len(sys.argv) == 1:
        raise Exception('syntax: commit-data.py <rev>[..]')
    for rev_arg in sys.argv[1:]:
        main(rev_arg)
except KeyboardInterrupt:
    pass
except Exception as e:
    if os.getenv('DEBUG'):
        raise
    print(e, file=sys.stderr)

# hg data
# https://hg.mozilla.org/mozilla-central/json-rev/c2e41df3f41f
# x patch size bytes
# x patch lines of code
# x author
# x pushed timestamp
# x backout? timestamp
# - backout? reason

# bugzilla data
# x creation timestamp
# x triaged timestamp (priority?)
# x approved timestamp (latest r+)
# x fixed timestamp
# x reviewers
# - needinfo targets?
# - checkin-needed flagged?
# - patches:
#   x creation timestamp
#   x reviewer(s)
#   x review timestamp
#   - review summary

# try
# - runs:
#   - timestamp
#   - result
