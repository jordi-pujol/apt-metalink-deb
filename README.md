# apt-metalink-deb

This package contains a Debian apt program that downloads package files 
from HTTP repositories using the download utility Aria2.

This work is based on the apt-metalink utility from:
https://github.com/tatsuhiro-t/apt-metalink

It is enhanced in some points, the configuration is more easy, some 
bugs are solved, now works with any python version, apt config options 
may be specified, and all that is included in a Debian package to easy 
install.

For an overview of the Aria2 utility, see the 'aria2' package, or
go to the homepage: http://aria2.sourceforge.net/

Download the Debian package and install it.

Using apt-metalink has some advantages over apt-get. Unlike the 
traditional apt-get, this program will download simultaneously from 
multiple servers. Each download is done from a set of mirrors, and the 
job will be completed even if one of them is not responding or 
unavailable.

To use apt-metalink, the file /etc/apt/apt-metalink.conf may 
be customized to point the most reliable servers on your zone.

This method is designed to use the HTTP protocol, and so the other 
mirrors configured in /etc/apt/apt-metalink.conf must use the HTTP 
protocol. We must configure the servers and a list of mirrors for each 
server.

If your original sources.list had a line like this:

deb http://httpredir.debian.org/debian unstable main contrib non-free

Every line in file /etc/apt/apt-metalink.conf has the following form:

http://.*.debian.org/debian,http://ftp.at.debian.org/debian

There are two fields, first is a regex to match the original 
sources.list URL, the second is a corresponding alternative to this 
URL. Multiple lines of corresponding alternatives may be listed for a 
single original URL.

Usually, using apt-metalink the downloads are more reliable and 
sometimes slightly faster, but not too much.

We can look for some URLs of web pages that list Debian mirrors: 

http://www.debian.org/mirror/list-full

http://deb-multimedia.org/debian-m-testing.php

http://sidux.com/module-Content-view-pid-2.html

# Execution examples:

Upgrade in silent mode, replace configurations without any question.

apt-metalink -y -o Dpkg::Options::="--force-confnew" -o APT::Quiet="true" upgrade

Download upgradable packages.

apt-metalink -y --download-only dist-upgrade
