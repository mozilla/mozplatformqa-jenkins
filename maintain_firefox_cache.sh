#!/bin/bash

tests='no'
if [ $1 = '--tests' ]; then
    tests='yes'
    shift
fi

symbols='no'
if [ $1 = '--symbols' ]; then
    symbols='yes'
    shift
fi

platform=$1

if [ "$2" = "" ]; then
    release="nightly"
else
    release="$2"
    shift
fi

repackage=0
if [ "$2" = "repackage" ]; then
    repackage=1
fi

function usage {
    "Usage: maintain_firefox_cache.sh |--tests|--symbols|<platform>|<release>|"
    "<release> can be one of nightly, aurora, beta, release and esr."
    "Use at most one of --tests or --symbols."
    exit 1
}

function repackage_mac_dmg {
    if [ $tests = 'no' ]; then
        if [ $platform = 'mac' ] || [ $platform = 'mac64' ]; then
            if [ "$repackage" = "1" ] ; then
                if [ ! -e firefox-latest-$release.en-US.$platform.tar.bz2 ] || [ firefox-latest-$release.en-US.$platform.dmg -nt firefox-latest-$release.en-US.$platform.tar.bz2 ]; then
                    if [ "$release" = "nightly" ]; then
                        volname="Nightly"
                        appname="FirefoxNightly"
                    elif [ "$release" = "aurora" ]; then
                        volname="Aurora"
                        appname="FirefoxAurora"
                    else
                        volname="Firefox"
                        appname="Firefox"
                    fi

                    umount /Volumes/FF
                    hdiutil attach -quiet -mountpoint /Volumes/FF firefox-latest-$release.en-US.$platform.dmg
                    mkdir -p /tmp/releases/$platform

                    rm -f firefox-latest-$release.en-US.$platform.tar.bz2
                    pushd /Volumes/FF
                    gtar cvfj $wd/releases/firefox-latest-$release.en-US.$platform.tar.bz2 ./$appname.app
                    popd
                    umount /Volumes/FF
                    rm -rf /tmp/releases/$platform/$appname.app
                fi
            else
                rm -f firefox-latest-$release.en-US.$platform.tar.bz2
            fi
        fi
   fi
}

function download {
    branch_name=$release
    if [ "$release" = "beta" ]; then
        mozdownload --type=tinderbox --platform="$platform" --extension="$archive_ext" --branch=mozilla-beta
    elif [ "$release" = "aurora" ]; then
        mozdownload --type=daily --platform="$platform" --extension="$archive_ext" --branch=mozilla-aurora
    else
        branch_name="central"
        mozdownload --type=daily --platform="$platform" --extension="$archive_ext"
    fi
    status=$?
    if [ "$status" != 0 ]; then
        exit $status
    fi

    if [ -e $target ]; then
        results=`find . -type f -name \*$branch_name\*$web_platform.$archive_ext -newer $target`
        if [ "x$results" != 'x' ]; then
            find . -type f -name \*$branch_name\*$web_platform.$archive_ext -not -newer $target -not -samefile $target -print -delete
            find . -type f -name \*$branch_name\*$web_platform.$archive_ext -newer $target -print -exec ../copy_latest.sh '{}' $target \;
        fi
    else
        if [ "x$release" = "xnightly" ]; then
            tag="central"
        else
            tag="$release"
        fi
        ../copy_latest.sh *mozilla*$tag*$web_platform.$archive_ext $target
    fi
}

if [ "$platform" = "" ]; then
    usage
elif [ $platform = 'linux64' ]; then
    web_platform='linux-x86_64'
    archive_ext='tar.bz2'
elif [ $platform = 'linux' ]; then
    web_platform='linux-i686'
    archive_ext='tar.bz2'
elif [ $platform = 'mac' ]; then
    web_platform='mac'
    archive_ext='dmg'
elif [ $platform = 'mac64' ]; then
    web_platform='mac64'
    archive_ext='dmg'
elif [ $platform = 'win32' ]; then
    web_platform='win32'
    archive_ext='zip'
elif [ $platform = 'win64' ]; then
    web_platform='win64'
    archive_ext='zip'
fi

if [ "$tests" = 'yes' ]; then
    archive_ext='common.tests.zip'
fi

if [ "$symbols" = 'yes' ]; then
    archive_ext='crashreporter-symbols.zip'
fi

wd=`pwd`
mkdir -p releases
cd releases

target="firefox-latest-$release.en-US.$web_platform.$archive_ext"

download

# if binary was requested, also download txt
if [ "x$tests" != "xyes" -a "x$symbols" != "xyes" ] ; then
    archive_ext='txt'
    target="firefox-latest-$release.en-US.$web_platform.$archive_ext"
    download
fi

#repackage_mac_dmg

cd $wd
