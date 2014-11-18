#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Submits jenkins steeplechase WebRTC test results to treeherder"""

from ConfigParser import ConfigParser
import glob
import hashlib
import json
import os
from sys import argv
import socket
import uuid
import argparse
import re

from thclient import TreeherderJobCollection
from thclient import TreeherderRequest
from thclient import TreeherderResultSetCollection

import sclogparse


def create_revision_hash():
    sha = hashlib.sha1()
    sha.update(str(uuid.uuid4()))

    return sha.hexdigest()


def get_config():
    parser = argparse.ArgumentParser(description='Jenkins Steeplechase Treeherder Results Parser')
    parser.add_argument('--package', required=True)
    parser.add_argument('--package2', default=None)
    parser.add_argument('--submit-time', required=True, type=int, dest='submit_time')
    parser.add_argument('--start-time', required=True, type=int, dest='start_time')
    parser.add_argument('--end-time', required=True, type=int, dest='end_time')
    parser.add_argument('--steeplechase-log', required=True, dest='steeplechase_log')
    parser.add_argument('--machine1', required=True, dest='machine1')
    parser.add_argument('--machine2', required=True, dest='machine2')
    parser.add_argument('--arch1', required=True, dest='arch1')
    parser.add_argument('--arch2', required=True, dest='arch2')
    args = parser.parse_args()

    pfi = platform_info(args.package, args.arch1, args.machine1)
    if args.package2:
        package2 = args.package2
    else:
        package2 = args.package

    pfi2 = platform_info(package2, args.arch2, args.machine2)

    my_dir = os.path.dirname(os.path.realpath(argv[0]))
    my_ini = os.path.join(my_dir, 'jenkinsherder.ini')

    cp = ConfigParser()
    cp.read(my_ini)

    config = {}
    config['treeherder'] = {}
    config['treeherder']['credentials'] = dict(cp.items('Credentials'))
    config['treeherder']['repo'] = dict(cp.items('Repo'))
    config['system'] = dict(cp.items('System'))
    config['times'] = {}
    config['times']['submit_time'] = args.submit_time
    config['times']['start_time'] = args.start_time
    config['times']['end_time'] = args.end_time
    config['platform_info'] = pfi
    config['platform_info2'] = pfi2
    config['files'] = {}
    config['files']['steeplechase_log'] = args.steeplechase_log

    return config

def platform_info(package, arch, machine):
    base_name, file = os.path.split(package)
    exp = re.compile(r"^firefox-latest-([^\.]+)\.en-US\.([^\.]+)\.(.*)$")
    match = exp.match(file)
    release = match.group(1)
    whole_platform = match.group(2)
    extension = match.group(3)

    arch_exp = re.compile(r"^([^\.]+)-(.*)$")
    arch_match = exp.match(whole_platform)
    if arch_match:
        platform = arch_match.group(1)
    else:
        platform = whole_platform

    if platform == 'linux':
        os_name = 'linux'
    elif platform == 'mac':
        os_name = 'mac'
    elif platform == 'win32':
        os_name = 'win'
    elif platform == 'win64':
        os_name = 'win'

    build_file = open(os.path.join(base_name, 'firefox-latest-%s.en-US.%s.txt' % (release, whole_platform)), 'r')
    buildid = build_file.readline().rstrip("\r\n")

    repo_line = build_file.readline().rstrip("\r\n")
    repo_exp = re.compile(r"^https://(.*/)rev/(.*)$")
    repo_match = repo_exp.match(repo_line)
    repo = repo_match.group(1)
    rev = repo_match.group(2)
    build_file.close()

    return { 'package': package, 'platform': platform, 'os_name': os_name, 'architecture': arch, 'release': release, 'buildid': buildid, 'repo': repo, 'rev': rev, 'machine': machine }


def get_app_information(config):
    repo = config['platform_info']['repo']
    rev = config['platform_info']['rev']
    return rev, repo

def get_files(config):
    return config['platform_info']['package'], config['platform_info2']['package']


def get_buildid(config):
    return config['platform_info']['buildid']

def get_result_summary(results):
    def add_line(title, value):
        summary['job_details'].append({
            'title': title,
            'value': str(value),
            'content_type': 'text'})

    summary = {'job_details': []}
    add_line('Total Failed', results['total failed'])
    add_line('Total Passed', results['total passed'])

    for client in results['clients']:
        name = client['name']
        add_line(name + ' Total Blocks', client['blocks'])
        add_line(name + ' Failed Blocks', len(client['failed blocks']))
        add_line(name + ' Session Failures', len(client['session failures']))
        add_line(name + ' Setup Failures', len(client['setup failures']))
        add_line(name + ' Cleanup Failures', len(client['cleanup failures']))

    return summary


def get_result_string(results):
    if (results['total failed'] is None or
        results['total passed'] is None):
            return 'busted'

    passed = True
    for client in results['clients']:
        passed = (passed and len(client['setup failures']) == 0
                         and len(client['cleanup failures']) == 0
                         and len(client['session failures']) == 0
                         and len(client['failed blocks']) < 20)
        if not passed:
            break

    if passed:
        return 'success'
    else:
        return 'testfailed'


def main():
    config = get_config()

    app_revision, app_repository = get_app_information(config)
    files = get_files(config)
    push_time = int(os.stat(files[0]).st_ctime)
    results = sclogparse.parse(config['files']['steeplechase_log'])
    result_set_hash = create_revision_hash()

    trsc = TreeherderResultSetCollection()
    trs = trsc.get_resultset()

    trs.add_revision_hash(result_set_hash)
    author = 'Firefox %s' % (config['platform_info']['release'].title())
    trs.add_author(author)
    trs.add_push_timestamp(push_time)

    tr = trs.get_revision()

    tr.add_revision(app_revision)
    tr.add_author(author)
    tr.add_comment(get_buildid(config))
    tr.add_files([os.path.basename(f) for f in files])
    tr.add_repository(app_repository)

    trs.add_revision(tr)
    trsc.add(trs)

    tjc = TreeherderJobCollection()
    tj = tjc.get_job()

    tj.add_revision_hash(result_set_hash)
    tj.add_project(config['treeherder']['repo']['project'])
    tj.add_job_guid(str(uuid.uuid4()))

    tj.add_group_name('WebRTC QA Tests')
    tj.add_group_symbol('WebRTC')
    tj.add_job_name('Sanity')
    tj.add_job_symbol('end')

    tj.add_build_info(config['platform_info']['os_name'], config['platform_info']['platform'], config['platform_info']['architecture'])
    tj.add_machine_info(config['platform_info']['os_name'], config['platform_info']['platform'], config['platform_info']['architecture'])
    tj.add_description('WebRTC Jenkins')
    tj.add_option_collection({'opt': True})  # must not be {}!
    tj.add_reason('testing')
    tj.add_who('Mozilla Platform QA')

    tj.add_submit_timestamp(config['times']['submit_time'])
    tj.add_start_timestamp(config['times']['start_time'])
    tj.add_end_timestamp(config['times']['end_time'])

    tj.add_state('completed')
    tj.add_machine(config['platform_info']['machine'])

    result_string = get_result_string(results)
    tj.add_result(result_string)
    if result_string != 'busted':
        summary = get_result_summary(results)
        tj.add_artifact('Job Info', 'json', summary)

    tj.add_artifact('Results', 'json', results)

    tjc.add(tj)

    print 'trsc = ' + json.dumps(json.loads(trsc.to_json()), sort_keys=True,
                                 indent=4, separators=(',', ': '))

    print 'tjc = ' + json.dumps(json.loads(tjc.to_json()), sort_keys=True,
                                indent=4, separators=(',', ': '))

    req = TreeherderRequest(
        protocol='http',
        host=config['treeherder']['repo']['host'],
        project=config['treeherder']['repo']['project'],
        oauth_key=config['treeherder']['credentials']['key'],
        oauth_secret=config['treeherder']['credentials']['secret']
    )

    req.post(trsc)
    req.post(tjc)


if __name__ == '__main__':
    main()
