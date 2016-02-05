import os

TREEHERDER_CONFIG = os.environ.get('TREEHERDER_CONFIG') or 'credentials.ignore'

config = {

    "find_links": [
        "http://pypi.pub.build.mozilla.org/pub",
    ],
    "pip_index": False,

    "treeherder_url": "https://treeherder.allizom.org",

    # Paths are relative to 'base_work_dir'
    "treeherder_credentials_path": os.path.join(TREEHERDER_CONFIG, "treeherder-staging-credentials.json"),
    "s3_credentials_path": os.path.join(TREEHERDER_CONFIG, "s3-credentials.json"),
    "group_name": "VideoPuppeteer",
    "group_symbol": "VP",
    "job_name": "MSE Video Playback",
    "job_symbol": "m",

    # See https://github.com/mozilla/treeherder/blob/master/treeherder/model/sample_data/job_data.json.sample
    "job_description": "firefox-media-tests (video playback)",
    "job_reason": "scheduled",
    "job_who": "PlatformQuality",

    # For log parsing
    "log_date_format": '%Y-%m-%d %H:%M:%S'
}
