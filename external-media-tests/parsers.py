# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import datetime
import re
import os
import traceback

BUSTED = 'busted'
TESTFAILED = 'testfailed'
UNKNOWN = 'unknown'
EXCEPTION = 'exception'
SUCCESS = 'success'


# from https://github.com/mozilla/treeherder/blob/master/treeherder/log_parser/parsers.py
class ParserBase(object):
    """
    Base class for all parsers.
    """
    RE_MOZHARNESS_PREFIX = re.compile(r"(?P<time>^\d+-\d+-\d+ \d+:\d+:\d+) +"
                                      r"(?:DEBUG|INFO|WARNING|"
                                      r"ERROR|CRITICAL|FATAL) - +")

    def __init__(self, name):
        """Setup the artifact to hold the extracted data."""
        self.name = name
        self.clear()

    def clear(self):
        """Reset this parser's values for another run."""
        self.artifact = []
        self.complete = False

    def parse_line(self, line, lineno):
        """Parse a single line of the log"""
        raise NotImplementedError

    def get_artifact(self):
        """By default, just return the artifact as-is."""
        return self.artifact


class MozharnessStepParser(ParserBase):
    """
    Parse mozharness steps.

    Example:
    2015-07-16 06:27:55  INFO - #####
    2015-07-16 06:27:55  INFO - ##### Running clobber step.    <-- start
    2015-07-16 06:27:55  INFO - #####
    ...
    2015-07-16 06:27:58  INFO - ##### Finished clobber step. Success: True
    ^ end
    ...
    2015-07-16 06:28:58  INFO - #####
    2015-07-16 06:28:58  INFO - ##### Skipping install step.   <-- start
    2015-07-16 06:28:58  INFO   #####                          <-- end

    Step format:
        "steps": [
        {
            "errors": [],
            "name": "clobber", # name of mozharness action
            "started": "2015-07-16 11:05:50",
            "started_linenumber": 8,
            "finished_linenumber": 20,
            "finished": "2015-07-16 11:05:52",
            "result": 0,
            "error_count": 0,
            "duration": 2, # in seconds
            "order": 0  # the order the process came in the log file
        },
        ...
    ]
    """
    # mozharness log levels.
    # DEBUG, INFO, WARNING, ERROR, CRITICAL, FATAL, IGNORE

    PARSER_MAX_STEP_ERROR_LINES = 100
    # after having started any section
    ST_STARTED = "started"
    # after having finished any section
    ST_FINISHED = "finished"
    STEP_PATTERN = r'(?P<name>.*?) step.'
    SUCCESS_PATTERN = r'Success: (?P<success>True|False|None)\s*'
    RESULT_PATTERN = r'Result: (?P<result>%s|%s|%s|%s|%s)\s*' % (BUSTED,
                                                                 TESTFAILED,
                                                                 EXCEPTION,
                                                                 SUCCESS,
                                                                 UNKNOWN)
    RE_STEP_START = re.compile(''.join([
        r'#{5} Running ',
        STEP_PATTERN
    ]))
    RE_STEP_END = re.compile(''.join([
        r'#{5} Finished ',
        STEP_PATTERN,
        ' ',
        SUCCESS_PATTERN,
        '$'
    ]))
    RE_TEST_END = re.compile(''.join([
        r'#{5} Finished ',
        STEP_PATTERN,
        ' ',
        SUCCESS_PATTERN,
        ' - ',
        RESULT_PATTERN,
        '$'
    ]))
    RE_SKIP_START = re.compile(''.join([
        r'#{5} Skipping ',
        STEP_PATTERN,
        '$'
    ]))
    RE_SKIP_END = re.compile(r'#{5}$')

    def __init__(self):
        super(MozharnessStepParser, self).__init__("step_data")
        self.stepnum = -1
        self.artifact = {
            "steps": [],
            "all_errors": [],
            "errors_truncated": False
        }
        self.sub_parser = ErrorParser()
        # Step 'started' or step 'finished'
        self.state = None
        # The current step is a 'skipped' step
        self.skipping = False

    def parse_line(self, line, lineno):
        """ Parse a single line of the log """
        prefix_match = self.RE_MOZHARNESS_PREFIX.match(line)
        if prefix_match:
            trimline = self.RE_MOZHARNESS_PREFIX.sub('', line)
        else:
            trimline = line
        # Check start of step
        if not self.state == self.ST_STARTED:
            match = (self.RE_STEP_START.match(trimline) or
                     self.RE_SKIP_START.match(trimline))
            if match:
                self.state = self.ST_STARTED
                self.stepnum += 1
                self.steps.append({
                    "name": match.group('name'),
                    "started": prefix_match.group('time'),
                    "started_linenumber": lineno,
                    "order": self.stepnum,
                    "errors": [],
                    # in case no end-of-step is found (log truncated)
                    "finished": prefix_match.group('time'),
                    "finished_linenumber": lineno + 1,
                    "result": "unknown"

                })
                if match.re == self.RE_SKIP_START:
                    self.skipping = True
            return

        # Check end of step
        if self.skipping:
            match = self.RE_SKIP_END.match(trimline)
        else:
            match = (self.RE_STEP_END.match(trimline) or
                     self.RE_TEST_END.match(trimline))
        if match:
            step_errors = self.sub_parser.get_artifact()
            step_error_count = len(step_errors)
            if step_error_count > self.PARSER_MAX_STEP_ERROR_LINES:
                step_errors = step_errors[:self.PARSER_MAX_STEP_ERROR_LINES]
                self.artifact['errors_truncated'] = True
            started = self.current_step['started']
            finished = prefix_match.group('time')
            self.current_step.update({
                "finished": finished,
                "finished_linenumber": lineno,
                "result": self.get_result(match.groupdict().get('success'),
                                          self.skipping,
                                          match.groupdict().get('result'),
                                          step_error_count),
                "errors": step_errors,
                "error_count": step_error_count,
                "duration": self.get_duration(started, finished)
            })
            self.artifact["all_errors"].extend(step_errors)
            self.sub_parser.clear()
            self.skipping = False
            self.state = self.ST_FINISHED
            return

        # Check middle of step
        self.sub_parser.parse_line(line, lineno)

    @property
    def steps(self):
        """Return the list of steps in the artifact"""
        return self.artifact["steps"]

    @property
    def current_step(self):
        """Return the current step in the artifact"""
        return self.steps[self.stepnum]

    @staticmethod
    def get_result(success, skipped, result=None, error_count=0):
        if skipped:
            return 'skipped'
        if result:
            return result
        if error_count > 0:
            return 'testfailed'
        if not success:
            return 'busted'
        else:
            return 'success'

    @staticmethod
    def get_duration(started, finished):
        """
        Return duration in seconds

        started - string of the form %Y-%m-%d %H:%M:%S
        finished - string of the form %Y-%m-%d %H:%M:%S
        """
        time_format = '%Y-%m-%d %H:%M:%S'
        start = datetime.datetime.strptime(started, time_format)
        finish = datetime.datetime.strptime(finished, time_format)
        delta = max(start, finish) - min(start, finish)
        return delta.total_seconds()


class ErrorParser(ParserBase):
    """Error detection sub-parser"""
    failure_re = re.compile(r'(^TEST-UNEXPECTED-FAIL|TEST-UNEXPECTED-ERROR)|'
                            r'(.*CRASH: )|'
                            r'(Crash reason: )|'
                            r'(Automation Error: )|'
                            r'(AssertionError: )|'
                            r'(Failure)')

    def __init__(self):
        super(ErrorParser, self).__init__("errors")

    def add(self, line, lineno):
        self.artifact.append({
            "linenumber": lineno,
            "line": line.rstrip()
        })

    def parse_line(self, line, lineno):
        # Remove mozharness prefixes prior to matching
        trimline = re.sub(self.RE_MOZHARNESS_PREFIX, '', line)
        if self.failure_re.match(trimline):
            self.add(trimline, lineno)


# From https://github.com/mozilla/treeherder/blob/master/treeherder/log_parser/artifactbuilders.py
class LogViewArtifactBuilder(object):
    """
    This class is called for each line of the log file, so it has no
    knowledge of the log file itself, as a whole.  It only, optionally, has
    the url to the log file to add to its own artifact.
    """
    MAX_LINE_LENGTH = 500

    def __init__(self, url=None, name='buildbot_text'):
        """
        url - The url for this log.  It's optional, but it gets
                  added to the artifact.
        name - The name for this log. Should match name provided in
        log_reference.
        """
        self.artifact = {
            "logurl": url,
            "logname": name
        }
        self.lineno = 0
        self.parsers = [MozharnessStepParser()]
        self.name = "text_log_summary"

    def parse_line(self, line):
        """Parse a single line of the log."""
        line = line[:self.MAX_LINE_LENGTH]

        for parser in self.parsers:
            if not parser.complete:
                parser.parse_line(line, self.lineno)

        self.lineno += 1

    def get_artifact(self):
        """Return the job artifact built from all parsers."""
        for sp in self.parsers:
            self.artifact[sp.name] = sp.get_artifact()
        return self.artifact


def parse_log(log_file, log_url, logger):
    """
    Build text_log_summary artifact by running each parser on each line.

    log_file - path to file being parsed
    log_url - log_url to include in artifact produced
    """
    logview_builder = LogViewArtifactBuilder(url=log_url,
                                             name=os.path.basename(log_file))
    try:
        with open(log_file, 'r') as f:
            for line in f:
                logview_builder.parse_line(line)
        raise Exception
    except Exception:
        message = 'Failed to parse log file: %s' % log_file
        logger.exception('\n'.join([message, traceback.format_exc()]))

    return logview_builder.get_artifact()

if __name__ == '__main__':
    import json
    import sys
    import logging
    logging.basicConfig()
    logger = logging.getLogger()
    if len(sys.argv) > 1:
        log_file = sys.argv[1]
    else:
        log_file = 'sample_data.ignore/log_info_1.log'
    artifact = parse_log(log_file, 'some url', logger)
    print json.dumps(artifact, indent=4, separators=(',', ': '))
