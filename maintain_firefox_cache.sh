#!/bin/bash

platform=$1

function repackage_mac_dmg {
    if [ $platform = 'mac' ]; then
        if [ ! -e firefox-latest-nightly.en-US.mac.tar.bz2 ] || [ firefox-latest-nightly.en-US.mac.dmg -nt firefox-latest-nightly.en-US.mac.tar.bz2 ]; then
            hdiutil attach firefox-latest-nightly.en-US.mac.dmg
            mkdir -p /tmp/releases/mac
            rsync -avz --extended-attributes /Volumes/Nightly/FirefoxNightly.app /tmp/releases/mac/
            rm -f firefox-latest-nightly.en-US.mac.tar.bz2
            cd /tmp/releases/mac
            tar cvfj $wd/releases/firefox-latest-nightly.en-US.mac.tar.bz2 ./FirefoxNightly.app
            cd $wd/releases
            umount /Volumes/Nightly
            rm -rf /tmp/releases/mac/FirefoxNightly.app
        fi
   fi
}

if [ $platform = "" ]; then
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
elif [ $platform = 'win' ]; then
    web_platform='win'
    archive_ext='zip'
fi

target="firefox-latest-nightly.en-US.$web_platform.$archive_ext"

wd=`pwd`
mkdir -p releases
cd releases

mozdownload --type=daily --platform=$platform

if [ -e $target ]; then
   if [ `find . -type f -name \*.$web_platform.$archive_ext -newer $target` ]; then
	find . -type f -name \*.$web_platform.$archive_ext -not -newer $target -not -samefile $target -print -exec mv '{}' /tmp \;
	find . -type f -name \*.$web_platform.$archive_ext -newer $target -print -exec ./mk_link.sh $target '{}' \;
   fi
else
    ln -s *mozilla*central*$web_platform.$archive_ext $target
fi
repackage_mac_dmg

cd $wd


