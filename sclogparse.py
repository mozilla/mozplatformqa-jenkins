#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Parses the Steeplechase log"""

import dateutil.parser
import re
import json
import sys


_anomalies = []

REGEXPS = {
    'test start': r'.*Waiting for results\.\.\.$',
    'test end': r'.*All clients finished$',
    'sc error': r'steeplechase ERROR',
    'session start': r'.*Run step: PC_.*_GUM',
    'client start': r'.*Log output for (.*):$',
    'client end': r'.*<<<<<<<$',
    'test failure': r'.*{"action":"test_unexpected_fail"',
    'result summary': r'.*Result summary.*$',
    'test finished': r'.*Test finished',
    'total passed': r'.*Passed: (\d*)',
    'total failed': r'.*Failed: (\d*)',
}

for key in REGEXPS:
    REGEXPS[key] = re.compile(REGEXPS[key])


class Client_Early_Exit_Error(Exception):

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class Unexpected_EOF_Error(Exception):

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


def log_anomaly(number, line):
    _anomalies.append((number, line))


def check_for_anomalies(number, line):
    if REGEXPS["sc error"].match(line):
        log_anomaly(number, line)

class LineReader():
    def line_reader(self):
        number = 0
        for line in self.buffer:
            number += 1
            check_for_anomalies(number, line)

            # Keep requeuing the line as long as send(True) is called
            while 1:
                repeat = yield number, line.strip()

                # received a next(), move on
                if not repeat:
                    break

                # received a send(True)
                while repeat:
                    repeat = yield None

        raise Unexpected_EOF_Error(number)

    def parse(self):
        reader = self.line_reader()
        results = create_results()

        try:
            process_log(reader, results)
        except Unexpected_EOF_Error as err:
            log_anomaly(err.value, 'Reached unexpected EOF')

        results['anomalies'] = _anomalies
        return results


class FileLineReader(LineReader):
    def __init__(self, filename):
        self.buffer = open(filename, "r")

class MemoryLineReader(LineReader):
    def __init__(self, buf):
        self.buffer = buf.split('\n')


def requeue_line(reader):
    reader.send(True)


def create_results():
    return {
        'clients': [],
        'total passed': None,
        'total failed': None,
        'anomalies': [],
    }


def create_client_results():
    return {
        'name': None,
        'setup failures': [],
        'cleanup failures': [],
        'session failures': [],
        'failed blocks': [],
    }


def process_log(reader, results):
    process_steeplechase_setup(reader, results)
    process_client(reader, results)
    process_client(reader, results)
    process_steeplechase_cleanup(reader, results)

def process_steeplechase_setup(reader, results):
    try:
        for number, line in reader:
            if REGEXPS["test start"].match(line):
                requeue_line(reader)
                return
    except Unexpected_EOF_Error as err:
        log_anomaly(err.value, 'Tests are busted. No test start found.')
        raise

def process_client(reader, results):
    # Process lines until we find a client start. If we find a test end, we don't have any failures.
    try:
        for number, line in reader:
            if REGEXPS['result summary'].match(line):
                requeue_line(reader)
                return

            if REGEXPS['client start'].match(line):
                requeue_line(reader)
                break

    except Unexpected_EOF_Error as err:
        log_anomaly(err.value, 'Tests are busted. No test end or client start found')
        raise

    client_results = create_client_results()
    results['clients'].append(client_results)

    number, line = reader.next()
    m = REGEXPS['client start'].match(line)
    client_name = m.group(1)
    client_results['name'] = client_name

    try:
        process_client_setup(reader, client_results)
        process_client_session(reader, client_results)
        process_client_cleanup(reader, client_results)
    except Client_Early_Exit_Error as err:
        log_anomaly(err.value, 'Tests are busted. %(name)s exited early' %
                    {'name': client_name})
        raise


def process_client_setup(reader, client_results):
    for number, line in reader:
        if REGEXPS['client end'].match(line):
            requeue_line(reader)
            return

        if REGEXPS['test failure'].match(line):
            client_results['setup failures'].append((number, line))

        if REGEXPS['session start'].match(line):
            requeue_line(reader)
            return

    raise Client_Early_Exit_Error(number)

def process_client_session(reader, client_results):
    client_results['blocks'] = 0
    first_dt = None
    last_dt = None
    pass_start_dt = None
    longest_pass_delta = None

    for number, line in reader:
        if REGEXPS['client end'].match(line):
            raise Client_Early_Exit_Error(number)

        if REGEXPS['test finished'].match(line):
            break

        if REGEXPS['test failure'].match(line):
            client_results['session failures'].append((number, line))

def process_client_cleanup(reader, client_results):
    for number, line in reader:

        if REGEXPS['client end'].match(line):
            break

        if REGEXPS['test failure'].match(line):
            client_results['cleanup failures'].append((number, line))


def process_steeplechase_cleanup(reader, results):
    total_passed = None
    total_failed = None

    for number, line in reader:
        m = REGEXPS['total passed'].match(line)
        if m:
            results['total passed'] = int(m.group(1))

        m = REGEXPS['total failed'].match(line)
        if m:
            results['total failed'] = int(m.group(1))
            return

def main():
    print json.dumps(parse(sys.argv[1]), indent=4, sort_keys=True)


if __name__ == '__main__':
    main()
