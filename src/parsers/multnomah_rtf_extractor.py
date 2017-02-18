#!/usr/bin/python3
# -*- coding: utf-8 -*-

# The MIT License (MIT)
# Copyright (c) 2016 OpenElections
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

import argparse
import csv
import sys
import re
import pprint

def main():
	args = parseArguments()

	for path in args.paths:
		print(path)
		extractor = RTFExtractor(path)
		extractor.extract()

		# print(extractor.race)
		# pp = pprint.PrettyPrinter(indent=4)
		# pp.pprint(extractor.lines)
		extractor.writeToFile('/Users/nick/Projects/openelections/OR-sources/Multnomah/2002/20021105.csv')


def parseArguments():
	parser = argparse.ArgumentParser(description='Verify votes are correct using a simple checksum')
	parser.add_argument('--verbose', '-v', dest='verbose', action='store_true')
	parser.add_argument('--includeOverUnder', dest='includeOverUnder', action='store_true')
	parser.add_argument('paths', metavar='path', type=str, nargs='+',
					   help='path to a CSV file')
	parser.set_defaults(verbose=False)

	return parser.parse_args()

class RTFExtractor(object):
	def __init__(self, path):
		self.resultsLineRE = re.compile(r'^[ ,\d%.]+$')
		self.legendRE = re.compile(r'(-\d-) ([A-Z .()]+)( ,)?')
		self.legendMarkerRE = re.compile(r'-\d-')

		self.path = path
		self.lines = []
		self.race = ''
		self.firstResultEncountered = False

	def extract(self):
		with open(self.path, 'r') as f:
			for line in f.readlines():
				csvLine = self.convert(line)

				lastLine = '' if not len(self.lines) else self.lines[-1]

				if self.goesWithPreviousLine(csvLine, lastLine):
					if len(self.lines):
						self.lines[-1] += csvLine
				elif not self.shouldDiscardLine(csvLine):
					self.lines.append(csvLine)

		# Reformat candidates
		headerLine = self.lines[1]
		headerComponents = [self.race, "Precinct", "Voters", "Trnout", "Pct"]

		for m in self.legendRE.finditer(self.lines[0]):
			nameComponents = m.group(2).strip().split(" ")
			party = nameComponents.pop()
			name = "{} ({})".format(" ".join(nameComponents), party)
			headerComponents.append(name)

		headerComponents.extend(["Under Votes", "Over Votes", "Write-ins"])

		headerLine = ", ".join(headerComponents)

		del(self.lines[0:2])
		self.lines.insert(0, headerLine)


	def convert(self, line):
		rtfCommandRE = re.compile(r'\\[a-z0-9-]+')

		outLine = line.replace("\\tab", ",")
		outLine = rtfCommandRE.sub('', outLine)
		# outLine = outLine.replace("\r\n", "")
		outLine = outLine.translate(str.maketrans('\r\n{}', '    '))
		outLine = outLine.strip(" ")

		return outLine

	def writeToFile(self, path):
		with open(path, 'a') as f:
			for line in self.lines:
				f.write(line+'\n')

	def goesWithPreviousLine(self, line, lastLine):
		if self.legendRE.search(line):
			if "Legend:" in lastLine:
				return True
		elif self.resultsLineRE.search(line):
			return True
		elif self.legendMarkerRE.search(line):
			if "Reg" in lastLine:
				return True
		elif " WI" in line and "Reg" in lastLine:
			return True

		return False

	def shouldDiscardLine(self, line):

		if "PCT" in line:
			self.firstResultEncountered = True
			return False
		elif "Race:" in line and not self.firstResultEncountered:
			self.race = line.split(",")[2].strip(" ")
		elif "Race Totals" in line:
			return False
		elif " WI" in line and not self.firstResultEncountered:
			return False
		elif self.legendMarkerRE.search(line) and not self.firstResultEncountered:
			return False

		return True


# Default function is main()
if __name__ == '__main__':
	main()
