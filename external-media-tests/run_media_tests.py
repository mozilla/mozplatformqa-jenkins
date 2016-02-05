#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""run_media_tests.py
    Assumptions:
    - This script is run from directory ("Jenkins workspace") that contains
      clone of firefox-media-tests repo.

    Requires:
    - virtualenv, pip
    - Environment variables:
        - MOZHARNESSHOME: path to mozharness source dir
        - MINIDUMP_STACKWALK: path to minidump_stackwalk binary
        - TREEHERDER_CONFIG: path to credentials files
    - On Windows, the mozilla-build system
    - Treeherder-related actions require:
      - treeherding.py
      - s3.py
"""
import copy
import json
import os
import re
import sys
import traceback

mozharnesspath = os.environ.get('MOZHARNESSHOME')
if mozharnesspath:
    sys.path.insert(1, mozharnesspath)
else:
    print 'MOZHARNESSHOME not set'

from mozharness.base.log import (INFO, ERROR, WARNING, FATAL,
                                 SimpleFileLogger, MultiFileLogger)
from mozharness.base.script import (BaseScript, PreScriptAction,
                                    PostScriptAction)
from mozharness.mozilla.testing.firefox_media_tests import (
    FirefoxMediaTestsBase, TESTFAILED, SUCCESS
from mozharness.mozilla.testing.testbase import (TestingMixin,
                                                 testing_config_options)
from mozharness.mozilla.testing.unittest import TestSummaryOutputParserHelper

)


BUSTED = 'busted'
TESTFAILED = 'testfailed'
UNKNOWN = 'unknown'
EXCEPTION = 'exception'
SUCCESS = 'success'

treeherding_config_options = [
    [["--no-treeherding"],
     {"action": "store_true",
      "dest": "treeherding_off",
      "default": False,  # i.e. Treeherding is on by default
      "help": "Disable submission to Treeherder",
      }],
    [["--job-name"],
     {"action": "store",
      "dest": "job_name",
      "help": ("Job name to submit to Treeherder. "
               "e.g. MSE Video Playback Tests"),
      }],
    [["--job-symbol"],
     {"action": "store",
      "dest": "job_symbol",
      "help": "Job symbol to submit to Treeherder. Typically one letter.",
      }],
    [["--treeherder-url"],
     {"action": "store",
      "dest": "treeherder_url",
      "help": "e.g. https://treeherder.allizom.org",
      }],
    [["--treeherder-credentials"],
     {"action": "store",
      "dest": "treeherder_credentials_path",
      "help": "Path to credentials json file.",
      }],
    [["--s3-credentials"],
     {"action": "store",
      "dest": "s3_credentials_path",
      "help": "Path to credentials json file",
      }],
]


class JobResultParser(TestSummaryOutputParserHelper):
    """ Parses test output to determine overall result."""
    def __init__(self, **kwargs):
        super(JobResultParser, self).__init__(**kwargs)
        self.return_code = 0
        # External-resource errors that should not count as test failures
        self.exception_re = re.compile(r'^TEST-UNEXPECTED-ERROR.*'
                                       r'TimeoutException: Error loading page,'
                                       r' timed out')
        self.exceptions = []

    def parse_single_line(self, line):
        super(JobResultParser, self).parse_single_line(line)
        if self.exception_re.match(line):
            self.exceptions.append(line)

    @property
    def status(self):
        status = UNKNOWN
        if self.passed and self.failed == 0:
            status = SUCCESS
        elif self.exceptions:
            status = EXCEPTION
        elif self.failed:
            status = TESTFAILED
        elif self.return_code:
            status = BUSTED
        return status


class TreeherdingMixin(object):
    """ Provides ability to upload job results to Treeherder.

    Uploads logs to S3 bucket

    Requires BaseScriptMixin

    Requires the VirtualenvMixin in order to install dependencies:
        treeherder-client, requests, boto, mozinfo, mozversion

    Interacts with TestingMixing via an 'install' PostScriptAction

    Config dependencies:
        - treeherder_url
        - treeherder_credentials_path (path to json file)
        - s3_credentials_path (path to json file)
        - group/job name/symbol, who, description, reason
    """
    def __init__(self, *args, **kwargs):
        super(TreeherdingMixin, self).__init__(*args, **kwargs)
        # instantiate after virtualenv is created
        self.treeherder = None  # TreeherderSubmission
        self.job = None  # TestJob
        self.job_result_parser = None  # JobResultParser
        if self.config['treeherding_off']:
            return
        dirs = self.query_abs_dirs()
        th_requirements = os.path.join(dirs['base_work_dir'],
                                       'treeherding_requirements.txt')
        if os.path.isfile(th_requirements):
            self.register_virtualenv_module(requirements=[th_requirements])
        self.register_virtualenv_module('mozversion',
                                        method='pip', optional=True)

    @PostScriptAction('create-virtualenv')
    def setup_treeherding(self, action, success=None):
        if not success:
            return
        if self.config['treeherding_off']:
            self.info("Treeherding is off; nothing to do.")
            return
        self.activate_virtualenv()
        try:
            # Imports are inside methods (rather than global) because we depend
            # on virtual-environment setup that should happen earlier in the
            # mozharness script. (We want to work in exactly one venv and that
            # venv should be created by mozharness. We don't want a mozharness
            # venv within a venv created externally.)
            from treeherding import TreeherderSubmission, TestJob
            options = self._get_treeherder_options()
            s3_bucket = self._get_s3_bucket()
            self.info("Initializing Treeherder client")
            self.treeherder = TreeherderSubmission(self.log_obj.logger,
                                                   options,
                                                   s3_bucket)
            self.job = TestJob()
        except Exception:
            self.warning("Unable to init Treeherder client: %s" %
                         traceback.format_exc())

    def _get_treeherder_options(self):
        """ Returns TreeherderOptions instance populated based on config.

        Prerequisite: A venv has been created and necessary packages have been
        installed.
        """
        self.info("Collecting Treeherder options.")
        from treeherding import TreeherderOptions
        c = self.config
        options = TreeherderOptions()
        options.treeherder_url = c['treeherder_url']
        dirs = self.query_abs_dirs()
        credentials_path = os.path.join(dirs['base_work_dir'],
                                        c['treeherder_credentials_path'])
        options.treeherder_credentials_path = credentials_path
        try:
            with open(options.treeherder_credentials_path) as f:
                credentials_string = f.read()
                options.treeherder_credentials = json.loads(credentials_string)
        except IOError:
            msg = ('Treeherder credentials file not '
                   'found at {0}.'.format(options.treeherder_credentials_path))
            self.warning(msg)
        return options

    def _get_s3_bucket(self):
        """ Returns S3Bucket instance populated based on config.

        Prerequisite: A venv has been created and necessary packages have been
        installed.
        """
        self.info("Setting up S3Bucket.")
        from s3 import S3Bucket
        c = self.config
        dirs = self.query_abs_dirs()
        credentials_path = os.path.join(dirs['base_work_dir'],
                                        c['s3_credentials_path'])
        try:
            with open(credentials_path) as f:
                config_string = f.read()
                s3_config = json.loads(config_string)
                return S3Bucket(s3_config['s3_bucket_name'],
                                s3_config['aws_access_key_id'],
                                s3_config['aws_access_key'],
                                self.log_obj.logger)
        except IOError:
            msg = ('S3 credentials file not '
                   'found at {0}.'.format(credentials_path))
            self.warning(msg)

    @PostScriptAction('install')
    def initialize_job(self, action, success=None):
        """ Populate basic job info (build, machine, group/job name/symbol).

        Should override this to add job info that is specific to your script.
        """
        if not success:
            return
        if self.config['treeherding_off'] or not self.treeherder:
            self.info("Treeherding is off or not set up; nothing to do.")
            return
        from treeherding import collect_job_info
        try:
            collect_job_info(self.job, self.binary_path,
                             os.path.basename(self.installer_path))
            c = self.config
            self.job.group_name = c['group_name']
            self.job.group_symbol = c['group_symbol']
            self.job.job_name = c['job_name']
            self.job.job_symbol = c['job_symbol']
            self.job.description = c['job_description']
            self.job.reason = c['job_reason']
            self.job.who = c['job_who']
        except Exception:
            self.warning("Unable to init job data (build, machine): %s" %
                         traceback.format_exc())

    def submit_treeherder_running(self):
        """ Submit job to Treeherder with status "running".
        Prerequisite: job should be populated with basic info like
        job/group name/symbol, revision, project.
        """
        if self.config['treeherding_off'] or not self.treeherder:
            self.info("Treeherding is off or not set up; nothing to do.")
            return
        self.treeherder.submit_running([self.job])

    @PreScriptAction('submit_treeherder_complete')
    def update_job_complete(self, action):
        """ Prepare results and artifacts (log files, config files) """
        if self.job_result_parser:
            # anything with status, passed, failed and todo int/str attributes
            # is a suitable test_result
            self.job.test_result = self.job_result_parser
            self.job.result = self.job.test_result.status
        else:
            self.job.result = UNKNOWN
        self.job.upload_dir = self.query_abs_dirs().get('abs_log_dir')

    def submit_treeherder_complete(self):
        """ Submit job to Treeherder with status "completed".
        Prerequisite: job should be populated with results and artifacts
        """
        if self.config['treeherding_off'] or not self.treeherder:
            self.info("Treeherding is off or not set up; nothing to do.")
            return
        self.treeherder.submit_complete([self.job])


class FirefoxMediaTest(TreeherdingMixin, TestingMixin, FirefoxMediaTestsBase):
    error_list = [
        {'substr': 'FAILED (errors=', 'level': WARNING},
        {'substr': r'''Could not successfully complete transport of message to Gecko, socket closed''', 'level': ERROR},
        {'substr': r'''Connection to Marionette server is lost. Check gecko''', 'level': ERROR},
        {'substr': 'Timeout waiting for marionette on port', 'level': ERROR},
        {'regex': re.compile(r'''(TEST-UNEXPECTED|PROCESS-CRASH|CRASH|ERROR|FAIL)'''), 'level': ERROR},
        #{'regex': re.compile(r'''(\b((?!Marionette|TestMarionette|NoSuchElement|XPathLookup|NoSuchWindow|StaleElement|ScriptTimeout|ElementNotVisible|NoSuchFrame|InvalidResponse|Javascript|Timeout|InvalidElementState|NoAlertPresent|InvalidCookieDomain|UnableToSetCookie|InvalidSelector|MoveTargetOutOfBounds)\w*)Exception)'''), 'level': ERROR},
        {'regex': re.compile(r'''(TEST-UNEXPECTED|PROCESS-CRASH|CRASH|ERROR|FAIL)'''), 'level': ERROR},
        {'regex': re.compile(r'''(\b\w*Exception)'''), 'level': ERROR},
        {'regex': re.compile(r'''(\b\w*Error)'''), 'level': ERROR},
     ]

    config_options = [
        [["--symbols-url"],
         {"action": "store",
          "dest": "symbols_url",
          "default": None,
          "help": "URL to the crashreporter-symbols.zip",
          }],
        [["--media-urls"],
         {"action": "store",
          "dest": "media_urls",
          "default": "firefox_media_tests/urls/default.ini",
          "help": "Path to ini file that lists media urls for tests.",
          }],
        [["--profile"],
         {"action": "store",
          "dest": "profile",
          "default": None,
          "help": "Path to FF profile that should be used by Marionette",
          }],
        [["--test-timeout"],
         {"action": "store",
          "dest": "test_timeout",
          "default": 10000,
          "help": ("Number of seconds without output before"
                    "firefox-media-tests is killed."
                    "Set this based on expected time for all media to play."),
          }],
        [["--tests"],
         {"action": "store",
          "dest": "tests",
          "default": None,
          "help": ("Test(s) to run. Path to test_*.py or "
                   "test manifest (*.ini)"),
          }],
        [["--jenkins-build-tag"],
         {"action": "store",
          "dest": "jenkins_build_tag",
          "default": os.environ.get('BUILD_TAG', ''),
          "help": "$BUILD_TAG in shell Jenkins build step",
          }],
        [["--jenkins-build-url"],
         {"action": "store",
          "dest": "jenkins_build_url",
          "default": os.environ.get('BUILD_URL', ''),
          "help": "$BUILD_URL in shell Jenkins build step",
          }],
        [["--log-date-format"],
         {"action": "store",
          "dest": "log_date_format",
          "default": None,
          "help": r"Default: '%H:%M:%S'",
          }],
        [["--e10s"],
         {"dest": "e10s",
          "action": "store_true",
          "default": False,
          "help": "Enable e10s when running marionette tests."}],
        [["--browsermob-script"],
         {"dest": "browsermob_script",
          "action": "store",
          "default": None,
          "help": "path to the browsermob-proxy shell script or batch file"}],
        [["--browsermob-port"],
         {"dest": "browsermob_port",
          "action": "store",
          "default": None,
          "help": "port to run the browsermob proxy on"}],
    ] + (copy.deepcopy(testing_config_options) +
         copy.deepcopy(treeherding_config_options))

    def __init__(self):
        super(FirefoxMediaTest, self).__init__(
              config_options=self.config_options,
              all_actions=['clobber',
                           'download-and-extract',
                           'create-virtualenv',
                           'install',
                           'submit_treeherder_running',
                           'run_marionette_tests',
                           'submit_treeherder_complete',
                           ],
              default_actions=['clobber',
                               'download-and-extract',
                               'create-virtualenv',
                               'install',
                               'submit_treeherder_running',
                               'run_marionette_tests',
                               'submit_treeherder_complete',
                               ],
              config={'download_symbols': True, },
        )

    # Allow config to set log_date_format
    def new_log_obj(self, default_log_level="info"):
        c = self.config
        log_dir = os.path.join(c['base_work_dir'], c.get('log_dir', 'logs'))
        log_config = {
            "logger_name": 'Simple',
            "log_name": 'log',
            "log_dir": log_dir,
            "log_level": default_log_level,
            "log_format": '%(asctime)s %(levelname)8s - %(message)s',
            "log_to_console": True,
            "append_to_log": False,
        }
        # This is the only difference with overridden method
        if c.get('log_date_format'):
            log_config['log_date_format'] = c['log_date_format']
        log_type = self.config.get("log_type", "multi")
        for key in log_config.keys():
            value = self.config.get(key, None)
            if value is not None:
                log_config[key] = value
        if log_type == "multi":
            self.log_obj = MultiFileLogger(**log_config)
        else:
            self.log_obj = SimpleFileLogger(**log_config)

    @PostScriptAction()
    def log_action_completed(self, action, success=None):
        """ Record end of each action to simplify parsing log into steps """
        msg = '##### Finished %s step. Success: %s' % (action, success)
        if action == 'run_marionette_tests' and self.job_result_parser:
            msg += ' - Result: %s' % (self.job_result_parser.status or UNKNOWN)
        self.info(msg)

    def _query_cmd(self):
        """ Determine how to call firefox-media-tests """
        cmd = super(FirefoxMediaTest, self).__init__()
        # configure logging
        dirs = self.query_abs_dirs()
        log_dir = dirs['abs_log_dir']
        cmd += ['--gecko-log', os.path.join(log_dir, 'gecko.log')]
        self.media_logs.add('gecko.log')
        cmd += ['--log-tbpl', '-']
        cmd += ['--log-html', os.path.join(log_dir, 'media_tests.html')]
        self.media_logs.add('media_tests.html')
        cmd += ['--log-mach', os.path.join(log_dir, 'media_tests_mach.log')]
        self.media_logs.add('media_tests_mach.log')
        return cmd

    def run_marionette_tests(self):
        cmd = self._query_cmd()
        # Useful for Treeherder job submission
        self.job_result_parser = JobResultParser(
                                    config=self.config,
                                    log_obj=self.log_obj,
                                    error_list=self.error_list)
        return_code = self.run_command(cmd,
                                       output_timeout=self.test_timeout,
                                       output_parser=self.job_result_parser)
        self.job_result_parser.return_code = return_code
        status = self.job_result_parser.status
        if status == SUCCESS:
            return_code = 0
            self.info("Marionette: %s" % status)
        else:
            self.error("Marionette: %s" % status)

        dirs = self.query_abs_dirs()
        log_dir = dirs.get('abs_log_dir')
        if not log_dir:
            return
        scrnshots_dir = os.path.join(dirs['base_work_dir'], 'screenshots')
        old_scrnshots_dir = os.path.join(log_dir, 'screenshots')
        if os.access(old_scrnshots_dir, os.F_OK):
            self.rmtree(old_scrnshots_dir)
        if os.access(scrnshots_dir, os.F_OK):
            self.move(scrnshots_dir, old_scrnshots_dir)

    @PostScriptAction('create-virtualenv')
    def setup_treeherding(self, action, success=None):
        if not success:
            return
        if self.config['treeherding_off']:
            self.info("Treeherding is off; nothing to do.")
            return
        super(FirefoxMediaTest, self).setup_treeherding(action, success)
        from treeherding import TestJob

        class JenkinsJob(TestJob):
            def __init__(self, **kwargs):
                super(JenkinsJob, self).__init__(**kwargs)
                self.jenkins_build_tag = ''  # computed
                self.jenkins_build_url = ''  # computed

            @property
            def unique_s3_prefix(self):
                # e.g. mozilla-aurora/aurora/mac/x86_64/20150520030205/
                # jenkins-webrtc-aurora-mac-nightly-win64-529/somesuffix
                if not self.jenkins_build_tag:
                    return super(JenkinsJob, self).unique_s3_prefix
                prefix = ('{0}/{1}/{2}/'
                          '{3}/{4}/{5}/').format(self.build['repo'],
                                                 self.build['release'],
                                                 self.build['platform'],
                                                 self.build['architecture'],
                                                 self.build['build_id'],
                                                 self.jenkins_build_tag)
                return prefix.replace(' ', '-')

        self.job = JenkinsJob()

    @PostScriptAction('install')
    def initialize_job(self, action, success=None):
        if not success:
            return
        if self.config['treeherding_off'] or not self.treeherder:
            self.info("Treeherding is off or not set up; nothing to do.")
            return
        super(FirefoxMediaTest, self).initialize_job(action, success)
        c = self.config
        self.job.jenkins_build_tag = c['jenkins_build_tag']
        self.job.jenkins_build_url = c['jenkins_build_url']
        self.job.name = c['jenkins_build_tag']
        if c['jenkins_build_url']:
            self.job.job_details.append({
                        'url': self.job.jenkins_build_url,
                        'value': 'Jenkins Build URL (VPN required)',
                        'content_type': 'link',
                        'title': 'artifact uploaded'})
        else:
            self.warning('Job has no Jenkins build url')
        if c['jenkins_build_tag']:
            self.job.job_details.append({
                        'value': self.job.jenkins_build_tag,
                        'content_type': 'text',
                        'title': 'artifact uploaded'})
        else:
            self.warning('Job has no Jenkins build tag')

    @PreScriptAction('submit_treeherder_complete')
    def update_job_complete(self, action):
        if self.config['treeherding_off'] or not self.treeherder:
            self.info("Treeherding is off or not set up; nothing to do.")
            return
        super(FirefoxMediaTest, self).update_job_complete(action)
        dirs = self.query_abs_dirs()
        log_dir = dirs.get('abs_log_dir')

        # copy media_urls ini file with txt extension for convenient web view
        url_config = os.path.abspath(self.media_urls)
        wrk_url_config = os.path.join(dirs['abs_work_dir'],
                                      os.path.basename(url_config) + '.txt')
        if os.access(wrk_url_config, os.F_OK):
            self.rmtree(wrk_url_config)
        if os.access(url_config, os.F_OK):
            self.copyfile(url_config, wrk_url_config)
        self.job.config_files.append(wrk_url_config)

        # instead of uploading all logs, upload broadest, error and gecko
        if log_dir:
            def add_log(name, parse=False):
                if not name:
                    return
                log_path = os.path.join(log_dir, name)
                if os.path.exists(log_path):
                    self.job.log_files.append(log_path)
                if parse:
                    self.job.parsed_logs.append(log_path)

            add_log(self.log_obj.log_files.get('error'))
            # Never upload debug logs
            if self.log_obj.log_level == 'debug':
                main_log = self.log_obj.log_files.get('info')
            else:
                main_log = self.log_obj.log_files.get(self.log_obj.log_level)
            add_log(main_log, parse=True)
            # in case of SimpleFileLogger
            add_log(self.log_obj.log_files.get('default'))
            # extra log files saved by marionette
            for f in self.media_logs:
                add_log(f)
            # Replace default upload dir (all logs) with screenshots
            screenshots_dir = os.path.join(log_dir, 'screenshots')
            if os.path.exists(screenshots_dir):
                self.job.upload_dir = os.path.abspath(screenshots_dir)
            else:
                self.job.upload_dir = ''


if __name__ == '__main__':
    media_test = FirefoxMediaTest()
    media_test.run_and_exit()
