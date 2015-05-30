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
import sys
import subprocess
import datetime

from thclient import TreeherderJobCollection
from thclient import TreeherderRequest
from thclient import TreeherderResultSetCollection
import mozlog

import sclogparse

def create_revision_hash():
    sha = hashlib.sha1()
    sha.update(str(uuid.uuid4()))

    return sha.hexdigest()


def get_config(argv):
    parser = argparse.ArgumentParser(description='Jenkins Steeplechase Treeherder Results Parser')
    parser.add_argument('--package', required=True)
    parser.add_argument('--package2', default=None)
    parser.add_argument('--host1', required=True)
    parser.add_argument('--host2', required=True)
    parser.add_argument('--arch1', required=True, dest='arch1')
    parser.add_argument('--arch2', required=True, dest='arch2')
    parser.add_argument('--html-manifest', required=True, dest='html_manifest')
    parser.add_argument('--specialpowers-path', required=True, dest='specialpowers_path')
    parser.add_argument('--prefs-file', required=True, dest='prefs_file')
    parser.add_argument('--signalling-server', required=True, dest='signalling_server')
    parser.add_argument('--save-logs-to', required=True, dest='log_dest')
    parser.add_argument('--steeplechase', required=True)
    args = parser.parse_args(argv)

    pfi = platform_info(args.package, args.arch1, args.host1)
    if args.package2:
        package2 = args.package2
    else:
        package2 = args.package

    pfi2 = platform_info(package2, args.arch2, args.host2)

    my_dir = os.path.dirname(os.path.realpath(__file__))
    my_ini = os.path.join(my_dir, 'jenkinsherder.ini')

    cp = ConfigParser()
    cp.read(my_ini)

    config = {}
    config['treeherder'] = {}
    config['treeherder']['credentials'] = dict(cp.items('Credentials'))
    config['treeherder']['repo'] = dict(cp.items('Repo'))
    config['system'] = dict(cp.items('System'))
    config['times'] = {}
    config['platform_info'] = pfi
    config['platform_info2'] = pfi2
    config['files'] = {}
    config['log_dest'] = args.log_dest
    config['signalling_server'] = args.signalling_server
    config['prefs_file'] = args.prefs_file
    config['specialpowers_path'] = args.specialpowers_path
    config['html_manifest'] = args.html_manifest
    config['steeplechase'] = args.steeplechase
    config['times']['submit_time'] = datetime.datetime.now().strftime("%s")


    return config

def platform_info(package, arch, host):
    base_name, file = os.path.split(package)
    exp = re.compile(r"^firefox-latest-([^\.]+)\.en-US\.([^\.]+)\.(.*)$")
    match = exp.match(file)
    release = match.group(1)
    whole_platform = match.group(2)
    extension = match.group(3)

    arch_exp = re.compile(r"^(.*)-(.*)$")
    arch_match = arch_exp.match(whole_platform)
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

    return { 'package': package, 'platform': platform, 'os_name': os_name, 'architecture': arch, 'release': release, 'buildid': buildid, 'repo': repo, 'rev': rev, 'host': host }


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


def run_steeplechase(config, log):
    cmd = sys.executable
    cmd += ' %s' % config['steeplechase']
    cmd += ' --package %s' % config['platform_info']['package']

    if config['platform_info']['package'] != config['platform_info2']['package']:
        cmd += ' --package2 %s' % config['platform_info2']['package']

    cmd += ' --save-logs-to %s' % config['log_dest']
    cmd += ' --prefs-file %s' % config['prefs_file']
    cmd += ' --specialpowers-path %s' % config['specialpowers_path']
    cmd += ' --signalling-server %s' % config['signalling_server']
    cmd += ' --html-manifest %s' % config['html_manifest']
    cmd += ' --host1 %s' % config['platform_info']['host']
    cmd += ' --host2 %s' % config['platform_info2']['host']
    cmd += ' 1>&2'

    config['times']['start_time'] = datetime.datetime.now().strftime("%s")
    p = subprocess.Popen(cmd, bufsize=1, stderr=subprocess.PIPE, shell=True)
    out, err = p.communicate()
    log.info(err)
    status = p.returncode
    config['times']['end_time'] = datetime.datetime.now().strftime("%s")

    return err, status


def main(argv):
    config = get_config(argv)

    log = mozlog.getLogger('steeplechase')
    log.setLevel(mozlog.DEBUG)

    # First, run steeplechase.
    try:
        sclog, status = run_steeplechase(config, log)
    except Exception as e:
        log.info("run_steeplechase threw %s" % e)
        raise

    # Second, process the output. Note that this needs to be updated since
    # treeherder-dev is dead.
    return

    app_revision, app_repository = get_app_information(config)
    files = get_files(config)
    push_time = int(os.stat(files[0]).st_ctime)
    reader = sclogparse.MemoryLineReader(sclog)
    results = reader.parse()
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
    tj.add_machine(config['platform_info']['host'])

    result_string = get_result_string(results)
    tj.add_result(result_string)
    if result_string != 'busted':
        summary = get_result_summary(results)
        tj.add_artifact('Job Info', 'json', summary)

    tj.add_artifact('Results', 'json', results)

    tjc.add(tj)

    req = TreeherderRequest(
        protocol='http',
        host=config['treeherder']['repo']['host'],
        project=config['treeherder']['repo']['project'],
        oauth_key=config['treeherder']['credentials']['key'],
        oauth_secret=config['treeherder']['credentials']['secret']
    )

    req.post(trsc)
    req.post(tjc)
    # maybe we should return all of this generated json in case somebody embeds this script?

    return result_string, trsc, tjc

if __name__ == '__main__':
    try:
        result_string, trsc, tjc = main(sys.argv[1:])
        print 'trsc = ' + json.dumps(json.loads(trsc.to_json()), sort_keys=True,
                                     indent=4, separators=(',', ': '))

        print 'tjc = ' + json.dumps(json.loads(tjc.to_json()), sort_keys=True,
                                    indent=4, separators=(',', ': '))
        if result_string == 'busted':
            raise BaseException('Something went wrong in the test harness.')
        elif result_string != 'success':
            sys.exit(1)
        else:
            sys.exit(0)
    except Exception as e:
        print e
        raise


