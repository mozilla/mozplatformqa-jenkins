#!/bin/bash

platform=$1

function repackage_mac_dmg {
    if [ $platform = 'mac' ] || [ $platform = 'mac64' ]; then
        if [ ! -e firefox-latest-nightly.en-US.$platform.tar.bz2 ] || [ firefox-latest-nightly.en-US.$platform.dmg -nt firefox-latest-nightly.en-US.$platform.tar.bz2 ]; then
            hdiutil attach firefox-latest-nightly.en-US.$platform.dmg
            mkdir -p /tmp/releases/$platform
            rsync -avz --extended-attributes /Volumes/Nightly/FirefoxNightly.app /tmp/releases/$platform/
            rm -f firefox-latest-nightly.en-US.$platform.tar.bz2
            pushd /tmp/releases/$platform
            tar cvfj $wd/releases/firefox-latest-nightly.en-US.$platform.tar.bz2 ./FirefoxNightly.app
            popd
            umount /Volumes/Nightly
            rm -rf /tmp/releases/$platform/FirefoxNightly.app
        fi
   fi
}

if [ "$platform" = "" ]; then
    exit
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

target="firefox-latest-nightly.en-US.$web_platform.$archive_ext"

wd=`pwd`
mkdir -p releases
cd releases

mozdownload --type=daily --platform="$platform" --extension="$archive_ext"

if [ -e $target ]; then
    if [ `find . -type f -name \*.$web_platform.$archive_ext -newer $target` ]; then
	find . -type f -name \*.$web_platform.$archive_ext -not -newer $target -not -samefile $target -print -exec mv '{}' /tmp \;
	find . -type f -name \*.$web_platform.$archive_ext -newer $target -print -exec ../copy_latest.sh '{}' $target \;
    fi
else
    ../copy_latest.sh *mozilla*central*$web_platform.$archive_ext $target
fi
repackage_mac_dmg

cd $wd


