#!/usr/local/bin/python3
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
import os
import pprint
import contextlib

outfileFormat = '{}.csv'

def main():
	args = parseArguments()

	# Start fresh
	with contextlib.suppress(FileNotFoundError):
		os.remove(outfileName(args.date))

	for path in args.paths:
		print(path)
		extractor = RTFExtractor(path)
		extractor.extract()

		# print(extractor.race)
		# pp = pprint.PrettyPrinter(indent=4)
		# pp.pprint(extractor.lines)
		extractor.writeToFile(outfileName(args.date))

def outfileName(date):
	name = outfileFormat.format(date)
	return name

def parseArguments():
	parser = argparse.ArgumentParser(description='Verify votes are correct using a simple checksum')
	parser.add_argument('date', type=str, help='Date of the election. Used in the generated filename.')
	parser.add_argument('paths', metavar='path', type=str, nargs='+',
					   help='path to a CSV file')

	parser.add_argument('--verbose', '-v', dest='verbose', action='store_true')
	parser.add_argument('--includeOverUnder', dest='includeOverUnder', action='store_true')

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

		self.reformatCandidates()
		self.reformatPrecinctLines()


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

	def reformatCandidates(self):
		headerLine = self.lines[1]
		headerComponents = [self.race, "Precinct", "Voters", "Trnout", "Pct"]

		for m in self.legendRE.finditer(self.lines[0]):
			headerComponents.append(m.group(2).strip())
			# nameComponents = m.group(2).strip().split(" ")
			# party = nameComponents.pop()
			# name = "{} ({})".format(" ".join(nameComponents), party)
			# headerComponents.append(name)

		headerComponents.extend(["Under Votes", "Over Votes", "Write-ins"])

		# print(headerComponents)
		headerLine = ",".join(headerComponents)

		del(self.lines[0:2])
		self.lines.insert(0, headerLine)

	def reformatPrecinctLines(self):
		# self.lines[1:] = [",".join(l.split()[1:]) for l in self.lines[1:]]
		for index, line in enumerate(self.lines):
			if index > 0:
				cols = line.split()
				cols[0] = "" # Remove "PCT", "Race"
				self.lines[index] = ",".join(cols)

	def reformatRace(self, race):
		raceComponents = race.split()
		party = raceComponents[0].rstrip(".")
		return "{} ({})".format(" ".join(raceComponents[1:]), party)

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
			self.race = self.reformatRace(line.split(":")[1].strip())
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
