# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Based on https://github.com/mozilla/autophone/blob/master/autophonetreeherder.py

import datetime
import glob
import logging
from platform import node
import os
import re
import tempfile
import time
import traceback
import urlparse
import requests
import uuid
import json

import mozinfo
import mozversion
from thclient import TreeherderClient, TreeherderJobCollection

from s3 import S3Error

logger = logging.getLogger()

releases = {'mozilla-central':'Nightly',
            'mozilla-beta': 'Beta',
            'mozilla-aurora': 'Aurora',
            'mozilla-release': 'Release',
            'mozilla-esr31': 'ESR31',
            'mozilla-esr38': 'ESR38'}

# Based on https://github.com/mozilla/treeherder/blob/master/treeherder/etl/buildbot.py; adapted to work with mozinfo
platforms = [
    {
        'regex': re.compile(r'(mac|OS X).*(10\.10|yosemite).*(64)?',
                            re.IGNORECASE),
        'attributes': {
            'os_name': 'mac',
            'platform': 'osx-10-10',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'(mac|OS X).*(10\.9|mavericks).*(64)?',
                            re.IGNORECASE),
        'attributes': {
            'os_name': 'mac',
            'platform': 'osx-10-9',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'(mac|OS X).*(10\.8|mountain lion).*(64)?',
                            re.IGNORECASE),
        'attributes': {
            'os_name': 'mac',
            'platform': 'osx-10-8',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'(mac|OS X).*(10\.7|lion).*(64)?', re.IGNORECASE),
        'attributes': {
            'os_name': 'mac',
            'platform': 'osx-10-7',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'(mac|OS X).*(10\.6|snow[ ]?leopard).*(64)?',
                            re.IGNORECASE),
        'attributes': {
            'os_name': 'mac',
            'platform': 'osx-10-6',
            'architecture': 'x86_64',
        }
    }, # ** Windows
    {
        'regex': re.compile(r'win(dows)?.*(5|5\.1|xp).*32', re.IGNORECASE),
        'attributes': {
            'os_name': 'win',
            'platform': 'windowsxp',
            'architecture': 'x86',
        }
    },
    {
        'regex': re.compile(r'win(dows)?.*(6\.2|8).*64', re.IGNORECASE),
        'attributes': {
            'os_name': 'win',
            'platform': 'windows8-64',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'win(dows)?.*(6\.2|8).*32', re.IGNORECASE),
        'attributes': {
            'os_name': 'win',
            'platform': 'windows8-32',
            'architecture': 'x86',
        }
    },
    {
        'regex': re.compile(r'win(dows)?.*(6\.1|7).*32', re.IGNORECASE),
        'attributes': {
            'os_name': 'win',
            'platform': 'windows7-32',
            'architecture': 'x86',
        }
    },
    {
        'regex': re.compile(r'win(dows)?.*(6\.1|7).*64', re.IGNORECASE),
        'attributes': {
            'os_name': 'win',
            'platform': 'windows7-64',
            'architecture': 'x86_64',
        }
    }, # ** Linux **
    {
        'regex': re.compile(r'(linux|ubuntu).*64', re.IGNORECASE),
        'attributes': {
            'os_name': 'linux',
            'platform': 'linux64',
            'architecture': 'x86_64',
        }
    },
    {
        'regex': re.compile(r'(linux|ubuntu).*32', re.IGNORECASE),
        'attributes': {
            'os_name': 'linux',
            'platform': 'linux32',
            'architecture': 'x86',
        }
    }
]

def timestamp_now():
    return int(time.mktime(datetime.datetime.now().timetuple()))


def get_platform_attributes(pf):
    """ Map a string like "Win 7 32-bit" to platform attributes recognized by
    Treeherder
    """
    logger.debug('get_platform_attributes - pf: %s', pf)
    for d in platforms:
        if d['regex'].match(pf):
            logger.debug('get_platform_attributes - matched pattern: %s',
                         d['regex'].pattern)
            return d['attributes']


def collect_job_info(job, binary='', installer=''):
    """ Set job attributes (build, machine, revision, etc.)
        formatted to match Treeherder UI expectations.
        Using mozinfo and mozversion
        ref: https://github.com/mozilla/treeherder/blob/master/ui/js/values.js
        job - TestJob
        binary - path to firefox-bin
        installer - installer filename
    """
    if not binary:
        raise ValueError('Missing argument: binary.')
    build = mozversion.get_version(binary=binary)
    machine = mozinfo.info
    machine_string = build_string =  ' '.join([machine['os'],
                                               machine['version'],
                                               str(machine['bits'])])
    # Narrow down build architecture; doesn't necessarily match platform
    if installer:
        job.build['package'] = installer
        if '64' in installer:
            build_string = ' '.join([machine['os'], machine['version'], '64'])
        if '32' in installer:
            build_string = ' '.join([machine['os'], machine['version'], '32'])

    # These don't match the expected Treeherder display; better than nothing.
    backup_attributes = {
        'platform': ' '.join([machine['os'].capitalize(),
                              machine['version'],
                              machine['processor']]),
        'os_name': machine['os'],
        'architecture': machine['processor']
    }
    job.build.update(backup_attributes)
    job.machine.update(backup_attributes)
    platform_attributes = get_platform_attributes(machine_string)
    if platform_attributes:
        job.machine.update(platform_attributes)
    platform_attributes = get_platform_attributes(build_string)
    if platform_attributes:
        job.build.update(platform_attributes)
    job.machine['host'] = node()
    job.build['product'] = build['application_name']
    repo_exp = re.compile(r'https://hg.mozilla.org/.*(mozilla-\w+)$')
    repo_match = repo_exp.match(build['application_repository'])
    if repo_match:
        job.build['repo'] = repo_match.group(1)
    else:
        repo_url = build['application_repository'].rsplit('/')
        job.build['repo'] = repo_url[-1]
    job.build['release'] = releases[job.build['repo']]
    job.build['revision'] = build['application_changeset']
    job.build['build_id'] = build['application_buildid']


def upload_file(s3_bucket, key_prefix, filepath, logger, job=None):
    filename = os.path.basename(filepath)
    # add timestamp in case filename not unique
    name = str(timestamp_now()) + filename
    s3_key = (key_prefix + name).replace(' ', '-')
    try:
        upload_url = s3_bucket.upload(filepath, s3_key)
        logger.info('Artifact uploaded to %s' % upload_url)
        if job:
            job.job_details.append({
                    'url': upload_url,
                    'value': filename,
                    'content_type': 'link',
                    'title': 'artifact uploaded'})
        return upload_url
    except (S3Error, IOError):
        message = 'Failed to upload %s.' % filename
        if job:
            job.job_details.append({
                    'value': message,
                    'content_type': 'text',
                    'title': 'Error'})
        logger.exception('\n'.join([message, traceback.format_exc()]))


def pretty(data):
    return json.dumps(data, indent=4, separators=(',', ': '))


class JobState(object):
    COMPLETED = 'completed'
    PENDING = 'pending'
    RUNNING = 'running'


class Tier2Treeherder(object):

    def __init__(self, logger, options, s3_bucket=None):
        self.logger = logger
        self.options = options
        self.s3_bucket = s3_bucket
        self.logger.debug(type(self).__name__)

        self.url = self.options.treeherder_url
        if not self.url:
            self.logger.debug(type(self).__name__ + ': no treeherder url')
            return

        self.server = self.options.treeherder_server
        self.protocol = self.options.treeherder_protocol
        self.host = self.options.treeherder_server
        self.credentials = self.options.treeherder_credentials
        self.retries = self.options.treeherder_retries
        self.retry_wait = self.options.treeherder_retry_wait

    def __str__(self):
        # Do not publish sensitive information
        whitelist = ('url',
                     'server',
                     'protocol',
                     'host',
                     'retries',
                     'retry_wait')
        d = {}
        for attr in whitelist:
            d[attr] = getattr(self, attr)
        return '%s' % d

    # TODO post_collection
    def post_request(self, project, job_collection):
        dump = json.dumps(job_collection.get_collection_data(), indent=4, separators=(',', ': '))
        self.logger.debug(type(self).__name__ + '.post_request - '
                          'job_collection =\n%s' %
                          pretty(job_collection.get_collection_data()))

        client = TreeherderClient(protocol=self.protocol,
                                  host=self.server)
        for attempt in range(1, self.retries + 1):
            try:
                client.post_collection(
                    project,
                    self.credentials[project]['consumer_key'],
                    self.credentials[project]['consumer_secret'],
                    job_collection)
                self.logger.debug(type(self).__name__ +
                                  '.post_request - collection posted')
                return
            except requests.exceptions.Timeout:
                message = ('Attempt %d to post result to '
                           'Treeherder timed out.' % attempt)
                self.logger.error(message)
                time.sleep(self.retry_wait)
            except Exception as e:
                message = ('Error submitting request to Treeherder\n\n'
                           'Exception: %s\n'
                           'TreeherderJobCollection %s\n' %
                           (e, pretty(job_collection.get_collection_data())))
                self.logger.exception(message)
                return
        log.error('Error submitting request to Treeherder.')

    # based on request_treeherder_revision_hash at
    # https://github.com/mozilla/autophone/blob/master/utils.py
    def request_revision_hash(self, project, rev):
        """Return the Treeherder revision_hash.
        :param project: repository name for the revision.
        :param rev: revision id for the changeset.
        """
        if not self.url or not project or not rev:
            self.logger.debug(type(self).__name__ + '.request_revision_hash - ' + 'missing url, project or revision.')
            return None

        revurl = '%s/api/project/%s/revision-lookup/?revision=%s' % (
            self.url, project, rev)
        revision_lookup = requests.get(revurl)
        message = 'GET: %s ' % revurl
        self.logger.debug(type(self).__name__ + '.request_revision_hash - ' + message)

        if revision_lookup.ok:
            if revision_lookup.json().get(rev):
                return revision_lookup.json().get(rev).get('revision_hash')
            else:
                message = 'Revision %s not found for %s.\n' % (rev, project)

        message += ('Attempt to get Treeherder revision hash failed - \n\n'
                   'status: %s \n'
                   'reason: %s \n'
                   'headers: %s \n'
                   'body: %s\n' % (revision_lookup.status_code,
                                 revision_lookup.reason,
                                 revision_lookup.headers,
                                 revision_lookup.text))
        self.logger.error(message)
        return None

    def submit_pending(self, jobs):
        """Submit jobs pending notifications to Treeherder
        :param jobs: Lists of jobs to be reported. (TestJob)
        """
        self.logger.debug(type(self).__name__ + '.submit_pending: jobs =\n%s' % jobs)
        if not self.url or not jobs:
            self.logger.debug(type(self).__name__ + '.submit_pending: no url/job')
            return

        tjc = TreeherderJobCollection(job_type='update')

        for j in jobs:
            project = j.build['repo']
            revision = j.build['revision']
            revision_hash = self.request_revision_hash(project, revision)
            if not revision_hash:
                self.logger.debug(type(self).__name__ +
                                  '.submit_pending: no revision hash')
                return
            j.submit_timestamp = timestamp_now()

            self.logger.info('creating Treeherder job %s for %s %s, '
                                        'revision_hash: %s' % (
                                            j.job_guid, j.name, project,
                                            revision_hash))

            tj = tjc.get_job()
            tj.add_description(j.description)
            tj.add_reason(j.reason)
            tj.add_revision_hash(revision_hash)
            tj.add_project(project)
            tj.add_who(j.who)
            tj.add_job_guid(j.job_guid)
            tj.add_job_name(j.job_name)
            tj.add_job_symbol(j.job_symbol)
            tj.add_group_name(j.group_name)
            tj.add_group_symbol(j.group_symbol)
            tj.add_product_name(j.build['product'])
            tj.add_state(JobState.PENDING)
            tj.add_submit_timestamp(j.submit_timestamp)
            # XXX need to send these until Bug 1066346 fixed.
            tj.add_start_timestamp(j.submit_timestamp)
            tj.add_end_timestamp(j.submit_timestamp)
            tj.add_build_url(j.build_url)
            tj.add_build_info(j.build['os_name'],
                             j.build['platform'],
                             j.build['architecture'])
            tj.add_machine(j.machine['host'])
            tj.add_machine_info(j.machine['os_name'],
                                j.machine['platform'],
                                j.machine['architecture'])
            # TODO determine type of build
            tj.add_option_collection({'opt': True})

            tjc.add(tj)
        #self.logger.debug(type(self).__name__ + '.submit_pending: tjc: %s' % (
        #    tjc.to_json()))

        self.post_request(project, tjc)

    def submit_running(self, jobs):
        """Submit jobs running notifications to Treeherder
        :param jobs: Lists of jobs to be reported. (TestJob)
        """
        self.logger.debug(type(self).__name__ + '.submit_running: jobs =\n%s' % jobs)
        if not self.url or not jobs:
            self.logger.debug(type(self).__name__ + '.submit_running: no url/job')
            return

        tjc = TreeherderJobCollection(job_type='update')

        for j in jobs:
            project = j.build['repo']
            revision = j.build['revision']
            revision_hash = self.request_revision_hash(project, revision)
            if not revision_hash:
                self.logger.debug(type(self).__name__ +
                                  '.submit_running: no revision hash')
                return
            self.logger.debug(type(self).__name__ + '.submit_running: '
                                         'for %s %s' % (j.name, project))

            if not j.start_timestamp:
                j.start_timestamp = timestamp_now()
            if not j.submit_timestamp:
                # If a 'pending' submission was never made for this job,
                # the submit_timestamp may be blank.
                j.submit_timestamp = timestamp_now()

            tj = tjc.get_job()
            tj.add_description(j.description)
            tj.add_reason(j.reason)
            tj.add_revision_hash(revision_hash)
            tj.add_project(project)
            tj.add_who(j.who)
            tj.add_job_guid(j.job_guid)
            tj.add_job_name(j.job_name)
            tj.add_job_symbol(j.job_symbol)
            tj.add_group_name(j.group_name)
            tj.add_group_symbol(j.group_symbol)
            tj.add_product_name(j.build['product'])
            tj.add_state(JobState.RUNNING)
            tj.add_submit_timestamp(j.submit_timestamp)
            tj.add_start_timestamp(j.start_timestamp)
            # XXX need to send these until Bug 1066346 fixed.
            tj.add_end_timestamp(j.start_timestamp)
            #
            tj.add_machine(j.machine['host'])
            tj.add_build_url(j.build_url)
            tj.add_build_info(j.build['os_name'],
                              j.build['platform'],
                              j.build['architecture'])
            tj.add_machine(j.machine['host'])
            tj.add_machine_info(j.machine['os_name'],
                                j.machine['platform'],
                                j.machine['architecture'])
            tj.add_option_collection({'opt': True})

            tjc.add(tj)

        #self.logger.debug(type(self).__name__ + '.submit_running: tjc: %s' %
        #                             tjc.to_json())

        self.post_request(project, tjc)

    def submit_complete(self, jobs):
        """ Submit results to Treeherder, including uploading logs.
        All jobs are submitted to the same project in one
        TreeherderJobCollection.

        :param jobs: list of jobs (TestJob).
        """
        self.logger.debug(type(self).__name__ + '.submit_complete: jobs =\n%s' % jobs)
        if not self.url or not jobs:
            self.logger.debug(type(self).__name__ + '.submit_complete: no url/job')
            return

        tjc = TreeherderJobCollection()

        for j in jobs:
            project = j.build['repo']
            revision = j.build['revision']
            revision_hash = self.request_revision_hash(project, revision)
            if not revision_hash:
                self.logger.debug(type(self).__name__ +
                                  '.submit_complete: no revision hash')
                return
            self.logger.debug(type(self).__name__ + '.submit_complete '
                                         'for %s %s' % (j.name, project))
            j.end_timestamp = timestamp_now()
            # A usercancelled job may not have a start_timestamp
            # since it may have been cancelled before it started.
            if not j.start_timestamp:
                j.start_timestamp = j.end_timestamp
            # If a 'pending' submission was never made for this job,
            # the submit_timestamp may be blank.
            if not j.submit_timestamp:
                j.submit_timestamp = j.end_timestamp

            if j.test_result:
                if j.test_result.failed == 0:
                    failed = '0'
                else:
                    failed = '<em class="testfail">%s</em>' % j.test_result.failed

                j.job_details.append({
                    'value': "%s/%s/%s" % (j.test_result.passed, failed, j.test_result.todo),
                    'content_type': 'raw_html',
                    'title': "%s-%s (pass/fail/todo)" % (j.job_name,
                                                        j.job_symbol)
                })

            tj = tjc.get_job()
            tj.add_description(j.description)
            tj.add_reason(j.reason)
            tj.add_revision_hash(revision_hash)
            tj.add_project(project)
            tj.add_who(j.who)
            # Note: job_guid should be added before artifacts.
            tj.add_job_guid(j.job_guid)
            tj.add_job_name(j.job_name)
            tj.add_job_symbol(j.job_symbol)
            tj.add_group_name(j.group_name)
            tj.add_group_symbol(j.group_symbol)
            tj.add_product_name(j.build['product'])
            tj.add_state(JobState.COMPLETED)
            tj.add_result(j.result)
            tj.add_submit_timestamp(j.submit_timestamp)
            tj.add_start_timestamp(j.start_timestamp)
            tj.add_end_timestamp(j.end_timestamp)
            if j.build_url:
                tj.add_build_url(j.build_url)
            tj.add_build_info(j.build['os_name'],
                              j.build['platform'],
                              j.build['architecture'])
            tj.add_machine(j.machine['host'])
            tj.add_machine_info(j.machine['os_name'],
                              j.machine['platform'],
                              j.machine['architecture'])
            tj.add_option_collection({'opt': True})

            # Job details and other artifacts

            # Add text_log_summary for each parsed log
            def process_parsed_log(log_file, log_url):
                if (not log_url) or (log_file not in j.parsed_logs):
                    return
                # TODO keep track of line numbers in log parser?
                error_lines = [{'line': line, 'linenumber': 1} for line in j.parsed_logs[log_file]]
                tj.add_log_reference(os.path.basename(log_file),
                                     log_url, parse_status='parsed')
                text_log_summary = {
                'header': {
                    'slave': j.machine['host'],
                    'revision': revision_hash
                },
                'step_data': {
                    'all_errors': error_lines,
                    'steps': [
                        {
                            'name': 'step',
                            'started_linenumber': 1,
                            'finished_linenumber': 1,
                            'duration': j.end_timestamp - j.start_timestamp,
                            'finished': '%s' % datetime.datetime.fromtimestamp(j.end_timestamp),
                            'errors': error_lines,
                            'error_count': len(error_lines),
                            'order': 0,
                            'result': j.result
                        },
                    ],
                    'errors_truncated': False
                    },
                'logurl': log_url
                }
                tj.add_artifact('text_log_summary', 'json',
                                json.dumps(text_log_summary))
                self.logger.debug(type(self).__name__ + '.submit_complete text_log_summary: %s' % pretty(text_log_summary))

            # File uploads
            if self.s3_bucket:
                prefix = j.unique_s3_prefix
                filepaths = j.log_files + j.config_files
                for path in filepaths:
                    url = upload_file(self.s3_bucket, prefix,
                                      path, self.logger, j)
                    process_parsed_log(path, url)
                if j.upload_dir:
                    for f in glob.glob(os.path.join(j.upload_dir, '*')):
                        url = upload_file(self.s3_bucket, prefix, f,
                                          self.logger, j)
                        process_parsed_log(path, url)


            tj.add_artifact('Job Info', 'json', {'job_details': j.job_details})
            for a in j.artifacts:
                tj.add_artifact(*a)

            tjc.add(tj)

            message = j.message
            if j.test_result:
                message += '\nTestResult: %s %s' % (j.test_result.status,
                                                    j.name)
            if message:
                self.logger.info(message)

        self.post_request(project, tjc)


# based on https://github.com/mozilla/autophone/blob/master/options.py
class TreeherderOptions(object):
    """Encapsulate the command line and ini file options used to configure
    Treeherder submission. Each attribute is initialized to an 'empty' value
    which also is of the same type as the final option value so that the
    appropriate getters can be determined."""
    def __init__(self):
        # command line options
        self.treeherder_url = ''
        self.treeherder_credentials_path = ''
        self.treeherder_retries = 5
        self.treeherder_retry_wait = 5
        self._treeherder_protocol = ''
        self._treeherder_server = ''
        # same format as credentials.json generation by
        # treeherder service
        self.treeherder_credentials = {} # computed

    def _parse_treeherder_url(self):
        p = urlparse.urlparse(self.treeherder_url)
        self._treeherder_protocol = p.scheme
        self._treeherder_server = p.netloc

    @property
    def treeherder_protocol(self):
        if self.treeherder_url and not self._treeherder_protocol:
            self._parse_treeherder_url()
        return self._treeherder_protocol

    @property
    def treeherder_server(self):
        if self.treeherder_url and not self._treeherder_server:
            self._parse_treeherder_url()
        return self._treeherder_server

    def __str__(self):
        # Do not publish sensitive information
        whitelist = ('treeherder_url',
                     'treeherder_retries',
                     'treeherder_retry_wait',
                     '_treeherder_protocol',
                     '_treeherder_server',
                    )
        d = {}
        for attr in whitelist:
            d[attr] = getattr(self, attr)
        return pretty(d)

    def __repr__(self):
        return self.__str__()


class TestJob(object):
    """ Public job data that is relevant to Treeherder """
    def __init__(self, **kwargs):
        self.name = '' # internal name
        self.job_name = ''
        self.job_symbol = ''
        self.job_guid = str(uuid.uuid4())
        self.group_name = ''
        self.group_symbol = ''
        self.description = ''
        self.start_timestamp = ''
        self.end_timestamp = ''
        self.submit_timestamp = ''
        # Data that we might upload to S3
        # Expecting absolute paths
        self.log_files = []
        self.config_files = []
        self.upload_dir = ''
        # For special 'Job Info' artifact retrieved by Treeherder UI.
        # List of dicts.
        # May include test results, links to logs, etc.
        self.job_details = []
        self.artifacts = [] #tuples of name, type, blob
        self.build_url = ''
        self.build = {
            'product': 'Firefox',
            'release': '',
            'repo': '',
            # Used in Treeherder build info: win, mac, linux
            'os_name': '',
            # Used in Treeherder revision summary,
            # ex: 'windows7-64' will be displayed as Windows 7 x64
            'platform': '',
            # Used in Treeheder build info: x86, x86_64
            'architecture': '',
            'package': '',
            'revision': '',
            'build_id': '',
            'build_url': ''
        }
        self.machine = {
            'os_name': '',
            # Used in Treeherder revision summary,
            # ex: 'windows7-64' will be displayed as Windows 7 x64
            'platform': '',
            'architecture': '',
            'host': ''
        }
        self.result = ''
        # should have str/int status, passed, failed, todo attributes
        # e.g. https://github.com/mozilla/autophone/blob/master/phonetest.py#L554 PhoneTestResult
        self.test_result = None
        self.reason = ''
        self.description = ''
        self.who = ''
        self.message = '' # e.g. summary to report alongside test results
        self.parsed_logs = {}

    @property
    def unique_s3_prefix(self):
        prefix = '{0}/{1}/{2}/{3}/{4}/{5}/'.format(self.build['repo'],
                                                   self.build['release'],
                                                   self.build['platform'],
                                                   self.build['architecture'],
                                                   self.build['build_id'],
                                                   self.job_guid)
        return prefix.replace(' ', '-')

    def __str__(self):
        # Do not publish sensitive information
        whitelist = ('job_name',
                     'group_name',
                     'description',
                     'job_guid',
                     'who',
                     'reason',
                     'message',
                     'result',
                     'start_timestamp',
                     'end_timestamp',
                     'submit_timestamp',
                     'build',
                     'machine',
                     'job_details',
                     'artifacts',
                     'log_files',
                     'config_files'
                    )
        d = {}
        for attr in whitelist:
            d[attr] = getattr(self, attr)
        return pretty(d)

    def __repr__(self):
        return self.__str__()

