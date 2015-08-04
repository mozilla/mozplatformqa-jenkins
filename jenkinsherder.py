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
import argparse
import re
import sys
import subprocess
from mozlog.unstructured import logger as mozlogger
import traceback

from s3 import S3Bucket

import sclogparse
import treeherder_config
from treeherding import (TestJob, TreeherderSubmission, TreeherderOptions,
                         timestamp_now, get_platform_attributes)
# TODO
logger = mozlogger.getLogger('jenkinsherder')
logger.setLevel(mozlogger.DEBUG)


class SteeplechaseJob(TestJob):
    """ Public job data that is relevant to Treeherder """
    def __init__(self, platform_info, **kwargs):
        super(SteeplechaseJob, self).__init__(**kwargs)
        self.jenkins_build_tag = '' # computed
        self.jenkins_build_url = '' # computed
        self.name = self.jenkins_build_tag
        self.build.update(platform_info['build'])
        self.machine.update(platform_info['machine'])

    @property
    def unique_s3_prefix(self):
        # e.g. mozilla-aurora/aurora/mac/x86_64/20150520030205/
        # jenkins-webrtc-aurora-mac-nightly-win64-529/somesuffix
        if not self.jenkins_build_tag:
            return super(SteeplechaseJob, self).unique_s3_prefix
        prefix = '{0}/{1}/{2}/{3}/{4}/{5}/'.format(self.build['repo'],
                                                   self.build['release'],
                                                   self.build['platform'],
                                                   self.build['architecture'],
                                                   self.build['build_id'],
                                                   self.jenkins_build_tag)
        return prefix.replace(' ', '-')

def get_config(argv):
    config = dict(treeherder_config.config)
    parser = argparse.ArgumentParser(description='Jenkins Steeplechase Treeherder Results Parser')
    parser.add_argument('--package', required=True)
    parser.add_argument('--package2', default=None)
    parser.add_argument('--host1', required=True)
    parser.add_argument('--host2', required=True)
    parser.add_argument('--arch1', required=True, dest='arch1')
    parser.add_argument('--arch2', required=True, dest='arch2')
    parser.add_argument('--os1', required=True, dest='os1')
    parser.add_argument('--os2', required=True, dest='os2')
    parser.add_argument('--html-manifest', required=True, dest='html_manifest')
    parser.add_argument('--specialpowers-path', required=True, dest='specialpowers_path')
    parser.add_argument('--prefs-file', required=True, dest='prefs_file')
    parser.add_argument('--signalling-server', required=True, dest='signalling_server')
    parser.add_argument('--save-logs-to', required=True, dest='log_dest')
    parser.add_argument('--steeplechase', required=True)
    parser.add_argument('--jenkins-build-url',
                        default=os.environ.get('BUILD_URL', ''))
    parser.add_argument('--jenkins-build-tag', default=os.environ.get('BUILD_TAG', ''))
    parser.add_argument("--no-treeherding", action="store_true")
    parser.add_argument('--job-name')
    parser.add_argument('--job-symbol')
    parser.add_argument('--treeherder-url')
    parser.add_argument('--treeherder-credentials-path')
    parser.add_argument('--s3-credentials-path')
    args = parser.parse_args(argv)

    pfi = platform_info(args.package, args.arch1, args.host1, args.os1)
    if args.package2:
        package2 = args.package2
    else:
        package2 = args.package

    pfi2 = platform_info(package2, args.arch2, args.host2, args.os2)

    my_dir = os.path.dirname(os.path.realpath(__file__))
    my_ini = os.path.join(my_dir, 'jenkinsherder.ini')

    cp = ConfigParser()
    cp.read(my_ini)

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
    config['jenkins_build_tag'] = args.jenkins_build_tag
    config['jenkins_build_url'] = args.jenkins_build_url
    # overwrite default treeherder config
    if args.job_name:
        config['job_name'] = args.job_name
    if args.job_symbol:
        config['job_symbol'] = args.job_symbol
    if args.treeherder_url:
        config['treeherder_url'] = args.treeherder_url
    if args.treeherder_credentials_path:
        config['treeherder_credentials_path'] = args.treeherder_credentials_path
    if args.s3_credentials_path:
        config['s3_credentials_path'] = args.s3_credentials_path
    config['no_treeherding'] = args.no_treeherding or False

    return config


def platform_info(package, arch, host, os_string):
    dirname, filename = os.path.split(package)
    exp = re.compile(r"^firefox-latest-([^\.]+)\.en-US\.([^\.]+)\.(.*)$")
    match = exp.match(filename)
    release = match.group(1)
    whole_platform = match.group(2)
    extension = match.group(3)

    arch_exp = re.compile(r"^(.*)-(.*)$")
    arch_match = arch_exp.match(whole_platform)
    if arch_match:
        platform = arch_match.group(1)
    else:
        platform = whole_platform

    if arch == 'i686':
        # Match Treeherder expectations
        arch = 'x86'

    if platform == 'linux' or platform == 'mac':
        os_name = platform
    elif platform == 'win32' or platform == 'win64':
        os_name = 'win'

    build_file_path = os.path.join (dirname,
                                   'firefox-latest-%s.en-US.%s.txt' %
                                   (release, whole_platform))
    with open(build_file_path, 'r') as build_file:
        build_id = build_file.readline().rstrip("\r\n")
        repo_line = build_file.readline().rstrip("\r\n")
    repo_exp = re.compile(r"^https://hg.mozilla.org/.*(mozilla-\w+)/rev/(.*)$")
    repo_match = repo_exp.match(repo_line)
    if repo_match:
        repo = repo_match.group(1)
        revision = repo_match.group(2)
    else:
        repo = revision = ''

    platform_info = { 'build':
                        {
                            'product': 'Firefox',
                            'release': release,
                            'repo': repo,
                            'os_name': os_name,
                            'platform': platform,
                            'architecture': arch,
                            'package': filename,
                            'revision': revision,
                            'build_id': build_id
                        },
                      'machine':
                        {
                            'os_name': os_name,
                            'platform': ' '.join([os_name, arch]),
                            'architecture': arch,
                            'host': host
                        }
                    }

    # Update to format expected by Treeherder
    platform_attributes = get_platform_attributes(os_string)
    if platform_attributes:
        platform_info['build'].update(platform_attributes)
        platform_info['machine'].update(platform_attributes)

    # Refine build architecture based on package info
    if platform == 'linux':
        platform_info['build']['architecture'] = arch
    elif platform == 'win32' and '64' in os_string:
        attributes = get_platform_attributes(os_string.replace('64','32'))
        platform_info['build'].update(attributes)
    elif platform == 'win64' and '64' not in os_string:
        platform_info['build']['architecture'] = 'x86_64'

    return platform_info


def get_result_summary(results):
    summary = []
    def add_line(title, value):
        summary.append({
            'title': title,
            'value': str(value),
            'content_type': 'text'})

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
    total_failed = results.get('total failed')
    total_passed = results.get('total passed')
    if total_failed is None or total_passed is None:
        return 'busted'
    if not results.get('clients') and total_failed > 0:
        return 'testfailed'
    for client in results['clients']:
        passed = (len(client['setup failures']) == 0
                  and len(client['cleanup failures']) == 0
                  and len(client['session failures']) == 0
                  and len(client['failed blocks']) < 20)
        if not passed:
            return 'testfailed'
    return 'success'


def run_steeplechase(config, log):
    cmd = sys.executable
    cmd += ' %s' % config['steeplechase']
    cmd += ' --package %s' % config['platform_info']['build']['package']

    if config['platform_info']['build']['package'] != config['platform_info2']['build']['package']:
        cmd += ' --package2 %s' % config['platform_info2']['build']['package']

    cmd += ' --save-logs-to %s' % config['log_dest']
    cmd += ' --prefs-file %s' % config['prefs_file']
    cmd += ' --specialpowers-path %s' % config['specialpowers_path']
    cmd += ' --signalling-server %s' % config['signalling_server']
    cmd += ' --html-manifest %s' % config['html_manifest']
    cmd += ' --host1 %s' % config['platform_info']['machine']['host']
    cmd += ' --host2 %s' % config['platform_info2']['machine']['host']
    cmd += ' 1>&2'

    p = subprocess.Popen(cmd, bufsize=1, stderr=subprocess.PIPE, shell=True)
    out, err = p.communicate()
    log.info(err)
    status = p.returncode

    return err, status


def get_log_files(logdir):
    if not os.path.exists(logdir):
        return []
    def relevant(f):
        return (f.endswith('.log') and os.path.isfile(os.path.join(logdir, f)))

    log_names = filter(relevant, os.listdir(logdir))
    log_paths = [os.path.join(logdir, f) for f in log_names]
    return [os.path.abspath(f) for f in log_paths]


def get_treeherder_options(url, credentials_path):
    options = TreeherderOptions()
    options.treeherder_url = url
    options.treeherder_credentials_path = os.path.abspath(credentials_path)
    try:
        with open(options.treeherder_credentials_path) as f:
            credentials_string = f.read()
            options.treeherder_credentials = json.loads(credentials_string)
            return options
    except IOError:
        msg = ('Treeherder credentials file not '
               'found at {0}.'.format(options.treeherder_credentials_path))
        logger.error(msg)


def get_s3_bucket(credentials_path):
    try:
        with open(credentials_path) as f:
            config_string = f.read()
            s3_config = json.loads(config_string)
            return S3Bucket(s3_config['s3_bucket_name'],
                            s3_config['aws_access_key_id'],
                            s3_config['aws_access_key'],
                            logger)
    except IOError:
        msg = ('S3 credentials file not '
               'found at {0}.'.format(credentials_path))
        logger.error(msg)


def main(argv):
    config = get_config(argv)
    logger.debug('config = %s' % json.dumps(config,
                                            indent=4,
                                            separators=(',', ': ')))

    if not config['no_treeherding']:
        th_options = get_treeherder_options(
                        config['treeherder_url'],
                        config['treeherder_credentials_path'])
        try:
            treeherder = TreeherderSubmission(logger, th_options,
                            get_s3_bucket(config['s3_credentials_path']))
        except Exception:
            logger.error('Setup of Treeherder submission '
                         'failed: %s' % traceback.format_exc())

        # Each job represents one Firefox instance in the WebRTC pair
        job1 = SteeplechaseJob(config['platform_info'])
        job2 = SteeplechaseJob(config['platform_info2'])
        for j in [job1, job2]:
            j.job_name = config['job_name']
            j.job_symbol = config['job_symbol']
            j.group_name = config['group_name']
            j.group_symbol = config['group_symbol']
            j.description = config['job_description']
            j.reason = config['job_reason']
            j.who = config['job_who']
        try:
            if job1.build['repo'] == job2.build['repo']:
                treeherder.submit_running([job1, job2])
            else:
                # Jobs that belong to different repos cannot be submitted in
                # one collection
                treeherder.submit_running([job1])
                treeherder.submit_running([job2])
        except Exception:
            logger.error('Treeherder submission '
                         'failed: %s' % traceback.format_exc())

    sclog = mozlogger.getLogger('steeplechase')
    sclog.setLevel(mozlogger.DEBUG)

    # First, run steeplechase.
    try:
        sclog, status = run_steeplechase(config, sclog)
    except Exception as e:
        sclog.info("Running steeplechase failed: %s" % traceback.format_exc())

    # Second, process the output.
    log_files = get_log_files(config['log_dest'])
    try:
        reader = sclogparse.MemoryLineReader(sclog)
        results = reader.parse()
        result_string = get_result_string(results)
        job_details = get_result_summary(results)
    except Exception as e:
        logger.error('Obtaining result '
                     'summary failed: %s' % traceback.format_exc())
        result_string = 'busted'
        job_details = []
        results = {}

    # Populate jobs and submit job to treeherder (including log upload)
    if not config['no_treeherding']:
        job1.end_timestamp = job2.end_timestamp = timestamp_now()
        for j in [job1, job2]:
            j.log_files += log_files
            j.result = result_string
            j.job_details += job_details
            j.jenkins_build_tag = config['jenkins_build_tag']
            j.jenkins_build_url = config['jenkins_build_url']
            j.job_details.append({
                        'url': j.jenkins_build_url,
                        'value': 'Jenkins Build URL (VPN required)',
                        'content_type': 'link',
                        'title': 'artifact uploaded'})
            j.job_details.append({
                        'value': j.jenkins_build_tag,
                        'content_type': 'text',
                        'title': 'artifact uploaded'})
            if results:
                j.artifacts.append(('Results', 'json', results))
            # TODO - fix this fake log parsing
            if log_files:
                failures = []
                if results.get('total failed'):
                    failures = ['Total failed: %s' % results['total failed']]
                j.parsed_logs[log_files[0]] = failures
        try:
            if job1.build['repo'] == job2.build['repo']:
                treeherder.submit_complete([job1, job2])
            else:
                # Jobs that belong to different repos cannot be submitted in one
                # collection
                treeherder.submit_complete([job1])
                treeherder.submit_complete([job2])
        except Exception as e:
            logger.error('Treeherder submission '
                         'failed: %s' % traceback.format_exc())

    return result_string


if __name__ == '__main__':
    result_string = main(sys.argv[1:])
    if result_string == 'busted':
        raise Exception('Something went wrong in the test harness.')
    elif result_string != 'success':
        sys.exit(1)
    else:
        sys.exit(0)

