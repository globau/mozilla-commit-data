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


class CommitException(Exception):
    pass


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
                raise CommitException(f'attachment {change["attachment_id"]} '
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
                raise CommitException(f'attachment {change["attachment_id"]}'
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
    bug_ids = parse_bug_ids(rev['summary'])
    if len(bug_ids) == 0:
        raise CommitException(f'failed to find bug-id in: {rev["summary"]}')
    if len(bug_ids) > 1:
        raise CommitException(f'found multiple bug-ids in: {rev["summary"]}')
    bug_id = bug_ids[0]

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
        stats['people'].append(dict(user=backout['user'],
                                    rel='backout author'))

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

        stats['patches'].append(patch_data)
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
            elif change['field_name'] == 'attachments.isobsolete':
                if change['added'] == '1':
                    status = 'obsoleted'
                else:
                    status = 'unobsoleted'

                attachment = find_attachment(stats, change['attachment_id'])
                if not attachment:
                    raise CommitException(f'attach {change["attachment_id"]}')
                attachment['status'].append(dict(
                    status=status,
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

    # see if we can identify the attachment that landed
    # this is fairly naive, comparing patch summaries to the commit summary.
    # this could be improved in a few ways, such as by looking at push
    # comments and presuming that the order of attachments is the same.
    # however, it is nearly impossible to be sure, so we'll just do a best
    # attempt here, generally avoiding false positives.

    landed_patch = None
    active_patches = []

    # go backwards through attachment statuses.  if we most recently
    # unobsoleted it, or we never obsoleted nor unobsoleted it, then it is
    # considered active.
    for patch in stats['patches']:
        for patch_status in reversed(patch['status']):
            if patch_status['status'] == 'unobsoleted':
                active_patches.append(patch)
                break
            if patch_status['status'] == 'obsoleted':
                break
        else:
            active_patches.append(patch)

    if len(active_patches) == 1:
        # *presume* this is the landed attachment
        landed_patch = active_patches[0]
    else:
        summary_base = commitparser.replace_reviewers(stats['summary'], None)
        for patch in active_patches:
            if (commitparser.replace_reviewers(patch['summary'], None) ==
                    summary_base):
                landed_patch = patch
                break

    if landed_patch:
        stats['landed_attachment_id'] = landed_patch['id']
    else:
        print(f'could not determine landed patch', file=sys.stderr)

    # tidy up
    stats['people'] = normalize_people(stats['people'])

    # display
    print(json.dumps(stats, indent=2, sort_keys=True))

try:
    if len(sys.argv) == 1:
        raise Exception('syntax: commit-data.py <rev>[..]')
    for rev_arg in sys.argv[1:]:
        try:
            main(rev_arg)
        except CommitException as e:
            print(f'Exception getting commit data: {e}', file=sys.stderr)
except KeyboardInterrupt:
    pass

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
