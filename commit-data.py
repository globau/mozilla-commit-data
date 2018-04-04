#!/usr/bin/env python3.6
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# quick-n-dirty script to consolidate data about a given mozilla-central commit

import base64
import datetime
import json
import re
import sys
from email.utils import parseaddr

from mozautomation import commitparser

from network import http_get
from phabricator import Revision

ATTACHMENT_TYPE_MOZREVIEW = 'text/x-review-board-request'
ATTACHMENT_TYPE_GITHUB = 'text/x-github-request'
ATTACHMENT_TYPE_PHABRICATOR = 'text/x-phabricator-request'

bugs = {}
commits = {}


def parse_bug_ids(s):
    bug_id_re = re.compile(
        r'((?:bug|(?=\b#?\d{5,})|^(?=\d))(?:\s*#?)(\d+)(?=\b))', re.I)
    bug_ids_with_duplicates = [int(m[1]) for m in bug_id_re.findall(s)]
    bug_ids = list(set(bug_ids_with_duplicates))
    return [bug_id for bug_id in bug_ids if bug_id < 100000000]


def find_attachment(stats, attach_id):
    for p in stats['patches']:
        if p['id'] == attach_id:
            return p
    return None


def is_patch(attachment):
    return (
        attachment['is_patch'] == 1 or
        attachment['content_type'] in (
            ATTACHMENT_TYPE_MOZREVIEW,
            ATTACHMENT_TYPE_GITHUB,
            ATTACHMENT_TYPE_PHABRICATOR,
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


def normalize_people(people):
    people_rel_bits = {}
    for person in people:
        user = person['user']
        rel = person['rel']
        if user not in people_rel_bits:
            people_rel_bits[user] = dict(user=user, rel={})
        people_rel_bits[user]['rel'][rel] = True

    norm_people = {}
    for person in people_rel_bits.values():
        norm_people[person['user']] = sorted(list(set(person['rel'])))

    return norm_people


def get_bug(bug_id, node):
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

    bug_data = dict(
        id=bug_id,
        url=f'https://bugzilla.mozilla.org/{bug_id}',
        creator=bug['creator'],
        comment_count=bug['comment_count'],
        product=bug['product'],
        component=bug['component'],
        bug_created_timestamp=bug['creation_time'],
        patches=[],

        assigned_to=[],
        status=[],
        flags=[],

        triaged=[],

        people=[],
    )

    people = [dict(user=bug['creator'], rel='reporter')]

    for attachment in bug_attachments:
        if not is_patch(attachment):
            continue

        patch_data = dict(
            content_type=attachment['content_type'],
            id=attachment['id'],
            timestamp=attachment['creation_time'],
            user=attachment['creator'],
            summary=attachment['summary'],
            status=[],
        )

        if patch_data['content_type'] == ATTACHMENT_TYPE_PHABRICATOR:
            rev_url = base64.b64decode(
                http_get(
                    f'{bmo}/bug/attachment/{patch_data["id"]}'
                    '?include_fields=data',
                    f'{patch_data["id"]}-attachment-data',
                )['attachments'][str(patch_data['id'])]['data']
            ).decode()
            revision = Revision(rev_url)
            patch_data['revision'] = dict(
                url=rev_url,
                phid=revision.phid,
                diffs=revision.diffs(),
            )

        bug_data['patches'].append(patch_data)
        people.append(dict(user=attachment['creator'],
                           rel='patch author'))

    for change_group in bug_history:
        for change in change_group['changes']:
            # assigned_to
            if change['field_name'] == 'assigned_to':
                bug_data['assigned_to'].append(dict(
                    user=change['added'],
                    when=change_group['when']
                ))
                people.append(dict(user=change['added'],
                                   rel='assigned bug'))

            # triage (look for status-flags changed, or a component change)
            if (change['field_name'].startswith('cf_status_firefox')
                    and change['added'] != '---'):
                bug_data['triaged'].append(dict(
                    user=change_group['who'],
                    action=f'{change["field_name"]}: {change["added"]}',
                    timestamp=change_group['when'],
                ))
                people.append(dict(user=change_group['who'],
                                   rel='triaged'))

            elif (change['field_name'] == 'component'
                  and change['removed'] == 'Untriaged'):
                bug_data['triaged'].append(dict(
                    user=change_group['who'],
                    action=f'{change["field_name"]} -> {change["added"]}',
                    timestamp=change_group['when'],
                ))
                people.append(dict(user=change_group['who'],
                                   rel='triaged'))

            # reviews
            if change['field_name'] == 'flagtypes.name':
                add_attachment_flag(bug_data, change_group, change,
                                    'review')
                add_attachment_flag(bug_data, change_group, change,
                                    'feedback')
                add_bug_flag(bug_data, change_group, change, 'needinfo')

            # attachment obsoletion
            elif change['field_name'] == 'attachments.isobsolete':
                if change['added'] == '1':
                    status = 'obsoleted'
                else:
                    status = 'unobsoleted'

                attachment = find_attachment(bug_data,
                                             change['attachment_id'])
                if not attachment:
                    raise Exception(f'attach {change["attachment_id"]}')
                attachment['status'].append(dict(
                    status=status,
                    timestamp=change_group['when'],
                ))
                people.append(dict(user=change_group['who'],
                                   rel='obsoleted attachment'))

            # status
            elif change['field_name'] == 'status':
                bug_data['status'].append(dict(
                    status=change['added'],
                    user=change_group['who'],
                    timestamp=change_group['when'],
                ))
                people.append(dict(user=change_group['who'],
                                   rel='bug status'))

    # go backwards through attachment statuses.  if we most recently
    # unobsoleted it, or we never obsoleted nor unobsoleted it, then it is
    # considered active.
    for patch in bug_data['patches']:
        for patch_status in reversed(patch['status']):
            if patch_status['status'] == 'unobsoleted':
                patch['active'] = True
                break
            if patch_status['status'] == 'obsoleted':
                patch['active'] = False
                break
        else:
            patch['active'] = True

    bug_data['people'] = normalize_people(people)
    bugs[bug_id] = bug_data

    bug_data['people'] = normalize_people(people)

    return bug_data


# noinspection PyTypeChecker
def get_commit_data(node):
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
    bug_ids = parse_bug_ids(rev['summary'])
    if len(bug_ids) == 0:
        raise Exception(f'failed to find bug-id in: {rev["summary"]}')
    if len(bug_ids) > 1:
        raise Exception(f'found multiple bug-ids in: {rev["summary"]}')
    bug_id = bug_ids[0]

    if bug_id not in bugs:
        bugs[bug_id] = get_bug(bug_id, node)

    # calc stats

    stats = dict(
        hg_url=f'https://hg.mozilla.org/mozilla-central/rev/{rev["node"]}',
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
    )

    stats['people'].append(dict(user=rev['user'], rel='push author'))
    stats['people'].append(dict(user=rev['pushuser'], rel='push user'))

    if backout:
        stats['backout'] = dict(
            summary=backout['summary'],
            user=backout['user'],
            timestamp=datetime.datetime.fromtimestamp(
                backout['pushdate'][0]).strftime('%Y-%m-%dT%H:%M:%SZ'),
        )
        stats['people'].append(dict(user=backout['user'],
                                    rel='backout author'))

    # tidy up
    stats['people'] = normalize_people(stats['people'])

    commits[stats['node']] = stats


def main(revs):
    try:
        for rev in revs:
            get_commit_data(rev)
    except KeyboardInterrupt:
        pass

    print(json.dumps({'commits': commits, 'bugs': bugs}, indent=2,
                     sort_keys=True))


if __name__ == '__main__':
    if len(sys.argv) == 1:
        raise Exception('syntax: commit-data.py <rev> [...]')

    main(sys.argv[1:])

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
