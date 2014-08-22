#!/bin/bash

platform=$1
if [ "$2" = "" ]; then
    release="nightly"
else
    release="$2"
fi

function usage {
    "Usage: maintain_firefox_cache.sh <platform> |<release>|"
    "<release> can be one of nightly, aurora, beta, release and esr."
    exit 1
}

function repackage_mac_dmg {
    if [ $platform = 'mac' ] || [ $platform = 'mac64' ]; then
        if [ ! -e firefox-latest-$release.en-US.$platform.tar.bz2 ] || [ firefox-latest-$release.en-US.$platform.dmg -nt firefox-latest-$release.en-US.$platform.tar.bz2 ]; then
            hdiutil attach firefox-latest-$release.en-US.$platform.dmg
            mkdir -p /tmp/releases/$platform
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

            rsync -avz --extended-attributes /Volumes/$volname/$appname.app /tmp/releases/$platform/
            rm -f firefox-latest-$release.en-US.$platform.tar.bz2
            pushd /tmp/releases/$platform
            tar cvfj $wd/releases/firefox-latest-$release.en-US.$platform.tar.bz2 ./$appname.app
            popd
            umount /Volumes/$volname
            rm -rf /tmp/releases/$platform/$appname.app
        fi
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
    web_platform='win64-x86_64'
    archive_ext='zip'
fi

wd=`pwd`
mkdir -p releases
cd releases

target="firefox-latest-$release.en-US.$web_platform.$archive_ext"

if [ "$release" = "nightly" ] || [ "$release" = "aurora" ] ; then
    if [ "$release" = "aurora" ]; then
        mozdownload --type=daily --platform="$platform" --extension="$archive_ext" --branch=mozilla-aurora
    else
        mozdownload --type=daily --platform="$platform" --extension="$archive_ext"
    fi

    if [ -e $target ]; then
        if [ `find . -type f -name \*.$web_platform.$archive_ext -newer $target` ]; then
	    find . -type f -name \*.$web_platform.$archive_ext -not -newer $target -not -samefile $target -print -exec mv '{}' /tmp \;
	    find . -type f -name \*.$web_platform.$archive_ext -newer $target -print -exec ../copy_latest.sh '{}' $target \;
        fi
    else
        if [ "$release" = "nightly" ]; then
            tag="central"
        else
            tag="$release"
        fi
        ../copy_latest.sh *mozilla*$tag*$web_platform.$archive_ext $target
    fi
fi

repackage_mac_dmg

cd $wd


