#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

# The MIT License (MIT)
# Copyright (c) 2016 Nick Kocharhook
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all 
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE 
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE 
# SOFTWARE.

import pdb
import csv
import os
import re
import argparse

def main():
	args = parseArguments()

	for path in args.paths:
		filler = Filler(path)

		if "matrix" not in filler.filename():
			if "primary" not in filler.filename():
				print("This script is only necessary on primary elections")
			else:
				filler.fill()


def parseArguments():
	parser = argparse.ArgumentParser(description='Use a fill down method to populate parties on every line')
	parser.add_argument('paths', metavar='path', type=str, nargs='+',
					   help='path to a CSV file')

	return parser.parse_args()


class Filler(object):
	def __init__(self, path):
		self.path = path
		self.rows = []

	def fill(self):
		self.fillFileAtPath(self.path)

	def filename(self):
		return os.path.basename(self.path)

	def fillFileAtPath(self, path):
		fields = []

		# pdb.set_trace()

		with open(path, 'rU') as csvfile:
			reader = csv.DictReader(csvfile)
			fields = reader.fieldnames
			lastParty = ''

			for index, row in enumerate(reader):
				if not row['party']:
					if lastParty:
						row['party'] = lastParty
				else:
					lastParty = row['party']

				self.rows.append(row)

		with open(self.newPath(), 'w') as newfile:
			writer = csv.DictWriter(newfile, fields, lineterminator="\n")

			writer.writeheader()

			for row in self.rows:
				writer.writerow(row)

	def newPath(self):
		(name, ext) = os.path.splitext(self.path)
		return name + '-filled' + ext

# Default function is main()
if __name__ == '__main__':
	main()