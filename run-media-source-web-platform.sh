#!/bin/bash

# This must be called from an environment where python is setup correctly.
# The firefox binary and the tests binary have to have been downloaded and expanded.
# The first argument is a url to the firefox binary. The second is a url to the tests
# package. The third argument is the platform. The valid values are "mac", "win32",
# "win64", and "linux".
# On Windows, this requires the mozilla-build system, installed at c:\mozilla-build.
# This also requires pip and virtualenv to have been installed into the python 
# distribution. Not ideal.

FIREFOX_ARCHIVE=$1
TESTS_ARCHIVE=$2
PLATFORM=$3

WORKSPACE=`pwd`

if [ "$PLATFORM" = "win32" ]; then
    export PATH=/bin:/c/mozilla-build/wget:/c/mozilla-build/info-zip:/c/mozilla-build/python:/c/mozilla-build/python/Scripts
elif [ "$PLATFORM" = "win64" ]; then
    export PATH=/bin:/c/mozilla-build/wget:/c/mozilla-build/info-zip:/c/mozilla-build/python:/c/mozilla-build/python/Scripts
fi

function usage {
    echo "Usage: run-media-source-web-platform.sh <firefox_archive> <tests_archive> <platform> <python_installation>"
    exit 1
}

function unimplemented {
    echo "UNIMPLEMENTED"
    exit 20
}

if [ "x$PLATFORM" = "x" ]; then
    usage
fi

function unpack_mac_archive {
  archive_name=`basename $FIREFOX_ARCHIVE`
  hdiutil attach -quiet -mountpoint /Volumes/MSE $WORKSPACE/$archive_name
  rm -rf $WORKSPACE/firefox.app
  cp -r /Volumes/MSE/*.app $WORKSPACE/firefox.app
  hdiutil detach /Volumes/MSE
}

function unpack_win_archive {
  cd $WORKSPACE
  rm -rf firefox
  firefox_archive_name=`basename $FIREFOX_ARCHIVE`
  unzip -q $firefox_archive_name
  cd ..
}

function unpack_linux_archive {
  unimplemented
}

function download_archive {
  archive_name=`basename $1`
  rm $archive_name
  wget $1
}

function unpack_tests_archive {
  cd $WORKSPACE
  rm -rf tests
  mkdir -p tests
  tests_archive_name=`basename $TESTS_ARCHIVE`
  cd tests
  unzip -q ../$tests_archive_name
  cd ..
}

function setup_web-platform_profile {
  cd $WORKSPACE/tests
  mkdir -p profiles
  cp web-platform/prefs/prefs_general.js profiles
}

download_archive $FIREFOX_ARCHIVE
if [ "$PLATFORM" = "mac" ] ; then
  unpack_mac_archive
elif [ "$PLATFORM" = "win32" ] ; then
  unpack_win_archive
elif [ "$PLATFORM" = "win64"] ; then
  unpack_win_archive
else
  unpack_linux_archive
fi

download_archive $TESTS_ARCHIVE
unpack_tests_archive
setup_web-platform_profile

cd $WORKSPACE/tests/web-platform/harness
pip install -r requirements_firefox.txt
cd .

if [ "$PLATFORM" = "mac" ]; then
  BINARY="$WORKSPACE/firefox.app/Contents/MacOS/firefox"
elif [ "$PLATFORM" = "win32" ]; then
  BINARY="$WORKSPACE/firefox/firefox.exe"
elif [ "$PLATFORM" = "win64" ]; then
  BINARY="$WORKSPACE/firefox/firefox.exe"
else
  BINARY="$WORKSPACE/firefox/firefox"
fi

if [ "$PLATFORM" = "win32" ]; then
  CERTUTIL="$WORKSPACE/tests/bin/certutil.exe"
elif [ "$PLATFORM" = "win64" ]; then
  CERTUTIL="$WORKSPACE/tests/bin/certutil.exe"
else
  CERTUTIL="$WORKSPACE/tests/bin/certutil"
fi

cd $WORKSPACE/tests/web-platform
if [ "$PLATFORM" = "mac" ] ; then
	DYLD_LIBRARY_PATH=$WORKSPACE/firefox.app/Contents/MacOS python runtests.py --product=firefox --include=media-source --log-mach=- --log-raw=$WORKSPACE/tests.log --binary=$BINARY --certutil=$CERTUTIL --ca-cert-path=$WORKSPACE/tests/web-platform/certs/cacert.pem --host-cert-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.pem --host-key-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.key
elif [ "$PLATFORM" = "win32" ] ; then
    PATH=$WORKSPACE/firefox:$PATH python runtests.py --product=firefox --include=media-source --log-mach=- --log-raw=$WORKSPACE/tests.log --binary=$BINARY --certutil=$CERTUTIL --ca-cert-path=$WORKSPACE/tests/web-platform/certs/cacert.pem --host-cert-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.pem --host-key-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.key
elif [ "$PLATFORM" = "win64" ] ; then
    PATH=$WORKSPACE/firefox/$PATH python runtests.py --product=firefox --include=media-source --log-mach=- --log-raw=$WORKSPACE/tests.log --binary=$BINARY --certutil=$CERTUTIL --ca-cert-path=$WORKSPACE/tests/web-platform/certs/cacert.pem --host-cert-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.pem --host-key-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.key
else
    LD_LIBRARY_PATH=$WORKSPACE/firefox python runtests.py --product=firefox --include=media-source --log-mach=- --log-raw=$WORKSPACE/tests.log --binary=$BINARY --certutil=$CERTUTIL --ca-cert-path=$WORKSPACE/tests/web-platform/certs/cacert.pem --host-cert-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.pem --host-key-path=$WORKSPACE/tests/web-platform/certs/web-platform.test.key	
fi	
UNEXPECTED_RESULTS=`grep --count expected\": $WORKSPACE/tests.log`
if [ $UNEXPECTED_RESULTS -ne "0" ]; then
  exit 1
fi
exit 0
