import os

TREEHERDER_CONFIG = os.environ.get('TREEHERDER_CONFIG') or 'credentials.ignore'

config = {
    "treeherder_url": "https://treeherder.mozilla.org",
    "treeherder_credentials_path": os.path.join(TREEHERDER_CONFIG, "treeherder-prod-credentials.json"),
    "s3_credentials_path": os.path.join(TREEHERDER_CONFIG, "s3-credentials.json"),
    "group_name": "Paired WebRTC Steeplechase Tests",
    "group_symbol": "PW",
    "job_name": "WebRTC Steeplechase Pair",
    "job_symbol": "p",

    # See https://github.com/mozilla/treeherder/blob/master/treeherder/model/sample_data/job_data.json.sample
    "job_description": ("WebRTC Steeplchase tests on pairs different browser "
                            "versions, different platforms."),
    "job_reason": "scheduled",
    "job_who": "PlatformQuality"
}
