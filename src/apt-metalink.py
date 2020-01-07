#!/usr/bin/python
#
# apt-metalink - Download deb packages from multiple servers concurrently
# Copyright (C) 2010-2014 Tatsuhiro Tsujikawa
# Copyright (C) 2014-2019 Jordi Pujol
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# References:
# https://wiki.debian.org/RepositoryFormat
# http://apt.alioth.debian.org/python-apt-doc/library/apt_pkg.html

import os
import subprocess
import textwrap
import sys
import optparse
import errno
import hashlib
import copy
import datetime
import time
import re
import glob

import apt
import apt_pkg

class AptMetalink:

	def __init__(self, opts):
		apt_pkg.init_config()
		apt_pkg.init_system()
		self.cache = apt.Cache(apt.progress.text.OpProgress())
		self.opts = opts
		self.archive_dir = apt_pkg.config.find_dir('Dir::Cache::Archives')
		if not self.archive_dir:
			raise Exception(('No archive dir is set.'
							' Usually it is /var/cache/apt/archives/'))
		self.lists_dir = apt_pkg.config.find_dir('Dir::State::Lists')
		if not self.archive_dir:
			raise Exception(('No package lists dir is set.'
							' Usually it is /var/lib/apt/lists/'))
		for c in self.opts.aptconf:
			(cname, copt) = c.split("=", 1)
			apt_pkg.config.set(cname, copt)

	def update(self):
		self.cache.update()
		self._get_changes()

	def upgrade(self, dist_upgrade=False):
		self.cache.upgrade(dist_upgrade=dist_upgrade)
		self._get_changes()

	def install(self, pkg_names):
		if self.opts.fix_broken:
			depcache = apt_pkg.DepCache(apt_pkg.Cache(apt.progress.text.OpProgress()))
			depcache.read_pinfile()
			try:
				depcache.fix_broken()
			except OSError as e:
				print("apt can't fix this broken installation.")
				exit(1)
		for pkg_name in pkg_names:
			if pkg_name in self.cache:
				pkg = self.cache[pkg_name]
				if not pkg.installed:
					pkg.mark_install()
				elif pkg.is_upgradable:
					pkg.mark_upgrade()
			else:
				raise Exception('{0} is not found'.format(pkg_name))
		self._get_changes()

	def _get_changes(self):
		pkgs = sorted(self.cache.get_changes(), key=lambda p:p.name)
		if pkgs:
			_print_update_summary(self.cache, pkgs)
			if not self.opts.assume_yes:
				if sys.version_info[0] < 3:
					sys.stdout.write("Do you want to continue [Y/n]?")
					ans = sys.stdin.readline().strip()
				else:
					ans = input('Do you want to continue [Y/n]?').strip()
				if ans and ans.lower() != 'y':
					print("Abort.")
					exit(1)
			if self.cache.required_download > 0:
				pkgs = [pkg for pkg in pkgs if not pkg.marked_delete and \
							not self._file_downloaded(pkg, hash_check = \
								self.opts.hash_check)]
				if self.opts.metalink_out:
					with open(self.opts.metalink_out, 'w') as f:
						make_metalink(f, pkgs, self.opts.hash_check)
					return
				if not self._download(pkgs, num_concurrent=guess_concurrent(pkgs)):
					print("Some download fails. apt_pkg will take care of them.")
					exit(1)
			else:
				print("There is nothing to download.")
			if self.opts.metalink_out:
				return
			if self.opts.download_only:
				print("Download complete and in download only mode")
				return
			self.cache.commit(apt.progress.text.AcquireProgress())

	def _download(self, pkgs, num_concurrent=3):
		if not pkgs:
			return True
		partial_dir = os.path.join(self.archive_dir, 'partial')
		cmdline = [self.opts.aria2c,
					'--metalink-file=-',
					'--file-allocation=none',
					'--auto-file-renaming=false',
					'--dir={0}'.format(partial_dir),
					'--max-concurrent-downloads={0}'.format(num_concurrent),
					'--no-conf',
					'--remote-time=true',
					'--auto-save-interval=0',
					'--continue',
					'--enable-http-pipelining=true',
					'--uri-selector=adaptive',
					'--download-result=full'
					]
		if self.opts.hash_check:
			cmdline.append('--check-integrity=true')

		http_proxy = apt_pkg.config.find('Acquire::http::Proxy')
		https_proxy = apt_pkg.config.find('Acquire::https::Proxy', http_proxy)
		ftp_proxy = apt_pkg.config.find('Acquire::ftp::Proxy')

		if http_proxy:
			cmdline.append('='.join(['--http-proxy', http_proxy]))
		if https_proxy:
			cmdline.append('='.join(['--https-proxy', https_proxy]))
		if ftp_proxy:
			cmdline.append('='.join(['--ftp-proxy', ftp_proxy]))

		print('Download in progress...')
		time_start = time.time()
		proc = subprocess.Popen(cmdline,
								stdin=subprocess.PIPE,
								stdout=subprocess.PIPE,
								stderr=subprocess.STDOUT,
								env={"LANGUAGE": "en_US"},
								universal_newlines=True)
		make_metalink(proc.stdin, pkgs, self.opts.hash_check)
		proc.stdin.close()
		download_results = False
		download_items = False
		download_list = list()
		downloading = 0
		downloaded = 0
		while True:
			line = proc.stdout.readline()
			if line == '' and proc.poll() != None:
				break
			line = line.strip()
			if line == '':
				continue
			if line.startswith('Download Results:'):
				download_results = True
			if download_results:
				if partial_dir in line:
					download_items = True
					download_list.append(line.replace(partial_dir + "/", ''))
				else:
					if download_items:
						download_items = False
						download_list.sort(key = sort_filename)
						print(*download_list, sep = "\n")
					print(line)
				if 'Download complete' in line:
					break
			elif 'Downloading ' in line and ' item(s)' in line:
				if self.opts.verbose:
					l = line.split()
					i = l.index('Downloading')
					downloading = l[i+1]
					print('{0} {1} {2}'.format(l[i], downloading, l[i+2]))
			elif 'Download complete:' in line:
				if self.opts.verbose:
					l = line.split()
					downloaded += 1
					print('{0}/{1} {2}'.format(downloaded, downloading, \
						l[l.index('complete:')+1].replace(partial_dir + "/", '')))
		print()
		link_success = True
		time_end = time.time()
		print('Elapsed time: {0}'.format(apt_pkg.time_to_str(int(time_end - time_start))))
		print('Overall speed: {0}B/s'.format(apt_pkg.size_to_str(self.cache.required_download / (time_end - time_start))))
		# Link archives/partial/*.deb to archives/
		for pkg in pkgs:
			filename = get_filename(pkg.candidate)
			dst = os.path.join(self.archive_dir, filename)
			src = os.path.join(partial_dir, filename)
			ctrl_file = ''.join([src, '.aria2'])
			# If control file exists, we assume download is not complete.
			if os.path.exists(ctrl_file):
				continue
			try:
				# Making hard link because aria2c needs file in
				# partial directory to know download is complete
				# in the next invocation.
				os.rename(src, dst)
			except OSError as e:
				if e.errno != errno.ENOENT:
					print("Failed to move archive file", e)
				link_success = False
		return proc.returncode == 0 and link_success

	def _file_downloaded(self, pkg, hash_check=False):
		candidate = pkg.candidate
		path = os.path.join(self.archive_dir, get_filename(candidate))
		# if not os.path.exists(path) or os.stat(path).st_size != candidate.size:
		if not os.path.exists(path):
			return False
		if hash_check:
			hash_type, hash_value = get_hash(pkg.candidate)
			try:
				return check_hash(path, hash_type, hash_value)
			except IOError as e:
				if e.errno != errno.ENOENT:
					print("Failed to check hash", e)
				return False
		else:
			return True

def check_hash(path, hash_type, hash_value):
	hash_fun = hashlib.new(hash_type)
	with open(path) as f:
		while 1:
			bytes = f.read(4096)
			if not bytes:
				break
			hash_fun.update(bytes)
	return hash_fun.hexdigest() == hash_value

def get_hash(candidate):
	try:
		if candidate.sha256:
			return ("sha256", candidate.sha256)
		elif candidate.sha1:
			return ("sha1", candidate.sha1)
		elif candidate.md5:
			return ("md5", candidate.md5)
	except (SystemError, UnicodeDecodeError):
		pass
	return (None, None)

def sort_filename(v):
	a = v.split("|")
	return a[1] + "|" + a[4]
 
def get_filename(candidate):
	# TODO apt-get man page said filename and basename in URI
	# could be different.
	f = os.path.basename(candidate.filename)
	v = candidate._cand.ver_str
	if ":" in v:
		f = re.sub("_.*_", "_"+v.replace(":", "%3a")+"_", f)
	return f

def get_mirrors():
	mirrors_lst = '/etc/apt/apt-metalink.conf'
	if os.path.isfile(mirrors_lst):
		return list(l.rstrip('\n') for l in open(mirrors_lst))
	return list()

# install, update and upgrade. To download deb package files
def make_metalink(out, pkgs, hash_check):
	mList = get_mirrors()
	out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
	out.write('<metalink xmlns="urn:ietf:params:xml:ns:metalink">\n')
	for pkg in pkgs:
		candidate = pkg.candidate
		out.write('<file name="{0}">\n'.format(get_filename(candidate)))
		out.write('<size>{0}</size>\n'.format(candidate.size))
		if hash_check:
			hashtype, hashvalue = get_hash(candidate)
			if hashtype:
				out.write('<hash type="{0}">{1}</hash>\n'.format(hashtype, hashvalue))
		uris = []
		for uri in set(candidate.uris):
			uris.append('<url priority="{0}">{1}</url>\n'.format(5, uri))
			for uri1 in mList:
				uri2 = uri1.split(',')
				try:
					if uri2[0] and uri2[1]:
						q = re.match(uri2[0],uri)
						if q == None:
							continue
						try:
							p = re.match(r'(?P<m>[1-9])',uri2[2])
							if p == None:
								prio = 5
							else:
								prio = p.group('m')
						except IndexError:
							prio = 5
						uris.append('<url priority="{0}">{1}</url>\n'.format(prio, uri.replace(q.group(0), uri2[1])))
				except IndexError:
					continue
		for uri in set(uris):
			out.write(uri)
		out.write('</file>\n')
	out.write('</metalink>\n')

def guess_concurrent(pkgs):
	return 3

def pprint_names(msg, names):
	if names:
		print(msg)
		print(textwrap.fill(' '.join(names),
							width=78,
							initial_indent='  ',
							subsequent_indent='  ',
							break_long_words=False,
							break_on_hyphens=False))

def _print_update_summary(cache, pkgs):
	delete_names = []
	install_names = []
	upgrade_names = []
	# TODO marked_downgrade, marked_keep, marked_reinstall
	for pkg in pkgs:
		if pkg.marked_delete:
			delete_names.append(pkg.name)
		elif pkg.marked_install:
			install_names.append(pkg.name)
		elif pkg.marked_upgrade:
			upgrade_names.append(pkg.name)
	pprint_names('The following packages will be REMOVED:', delete_names)
	pprint_names('The following NEW packages will be installed:', install_names)
	pprint_names('The following packages will be upgraded:', upgrade_names)
	print(('{0} upgraded, {1} newly installed, {2} to remove and'
		   ' {3} not upgraded')\
		   .format(len(upgrade_names), len(install_names), len(delete_names),
				   cache.keep_count))
	print('Need to get {0}B of archives.'\
		.format(apt_pkg.size_to_str(cache.required_download)))
	if cache.required_space < 0:
		print('After this operation, {0}B of disk space will be freed.'\
			.format(apt_pkg.size_to_str(-cache.required_space)))
	else:
		print(('After this operation, {0}B of additional disk space will'
			   ' be used.').format(apt_pkg.size_to_str(cache.required_space)))

def main():
	usage = 'Usage: %prog [options] {help | upgrade | dist-upgrade | install pkg ...}'
	parser = optparse.OptionParser(usage=usage)
	parser.add_option('-d', '--download-only', action='store_true',
					  help="Download only. [default: %default]")
	parser.add_option('-m', '--metalink-out', metavar="FILE",
					  help=("""\
Instead of fetching the files, Metalink XML document is saved to given
FILE. Metalink XML document contains package's URIs and checksums.
"""))
	parser.add_option('-c', '--hash-check', action="store_true",
					  help=("Check hash of already downloaded files. [default: %default]"
							" If hash check fails, download file again."))
	parser.add_option('-x', '--aria2c', dest='aria2c',
					  help="path to aria2c executable [default: %default]")
	parser.add_option('-v', '--verbose' , action="store_true",
					  help="Print messages about what the program is doing. [default: %default]")
	parser.add_option('-y', '--assume-yes', '--yes', action='store_true',
					  help="Assume Yes to all queries and do not prompt. [default: %default]")
	parser.add_option('-o', dest='aptconf', action='append',
					  help="Apt configuration options")
	parser.add_option('-f', '--fix-broken', action="store_true",
					  help=("Try to fix all broken packages in the cache and return True in case of success. [default: %default]"))

	if apt_pkg.config.find('APT::Get::Download-Only') in ["1", "true"]:
		parser.set_defaults(download_only=True)
	else:
		parser.set_defaults(download_only=False)
	parser.set_defaults(hash_check=False)
	parser.set_defaults(aria2c='/usr/bin/aria2c')
	parser.set_defaults(verbose=False)
	if apt_pkg.config.find('APT::Get::Assume-Yes') in ["1", "true"]:
		parser.set_defaults(assume_yes=True)
	else:
		parser.set_defaults(assume_yes=False)
	parser.set_defaults(aptconf=[])
	parser.set_defaults(fix_broken=False)
	(opts, args) = parser.parse_args()

	if not args:
		print('No command is given.')
		parser.print_help()
		exit(1)

	command = args[0]

	if command != 'install' and len(args) > 1:
		print('Invalid args.' ', '.join(args))
		parser.print_help()
		exit(1)

	if command == 'help':
		parser.print_help()
		exit(0)

	am = AptMetalink(opts)
	if command == 'install':
		am.install(args[1:])
	elif opts.fix_broken:
		sys.stderr.write("Option fix-broken is only valid for the install command.\n")
		exit(1)
	else:
		if command == 'upgrade':
			am.upgrade()
		elif command in ['dist-upgrade', 'full-upgrade']:
			am.upgrade(dist_upgrade=True)
		elif command == 'update':
			am.update()
		else:
			sys.stderr.write("Command {0} is not supported.\n".format(command))
			exit(1)

if __name__ == '__main__':
	main()
