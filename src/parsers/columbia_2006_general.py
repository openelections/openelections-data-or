#!/usr/bin/python
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

import re
import os
import sys
import pdb
import fileinput
import unicodecsv
import pprint
from collections import OrderedDict

# Configure variables
county = 'Lane'
outfile = '20061107__or__general__columbia__precinct.csv'


headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
party_prefixes = ['DEM', 'REP']

office_lookup = {
	'UNITED STATES PRESIDENT AND VICE PRESIDENT': 'President',
	'UNITED STATES SENATOR': 'U.S. Senate',
	'UNITED STATES REP IN CONGRESS': 'U.S. House',
	'SECRETARY OF STATE': 'Secretary of State',
	'STATE TREASURER': 'State Treasurer',
	'ATTORNEY GENERAL': 'Attorney General',
	'GOVERNOR': 'Governor',
	'STATE REPRESENTATIVE': 'State House',
	'STATE SENATOR': 'State Senate'
}

candidate_lookup = {
	'WRITE-IN': 'Write-in',
	'OVER VOTES': 'Over Votes',
	'UNDER VOTES': 'Under Votes'
}


def main():
	currentCanvass = ""
	allCanvasses = []
	divisionRE = re.compile("={132}")

	# read from stdin
	for line in sys.stdin.readlines():
		if divisionRE.match(line):
			previousCanvass = OfficeCanvass(currentCanvass)

			# pdb.set_trace()

			if previousCanvass.office.upper() in office_lookup: # exclude lower offices
				allCanvasses.append(previousCanvass)
			else:
				print "FAILED: %s" % previousCanvass

			currentCanvass = ""
		
		currentCanvass += line

	# printCanvasses(allCanvasses)
	writeCSV(allCanvasses)

def writeCSV(allCanvasses):
	with open(outfile, 'wb') as csvfile:
		w = unicodecsv.writer(csvfile, encoding='utf-8')
		w.writerow(headers)

		for canvass in allCanvasses:
			for precinct, results in canvass.results.iteritems():
				for index, result in enumerate(results):
					normalisedOffice = office_lookup[canvass.office.upper()] # Normalise the office
					candidate, party = canvass.candidates.items()[index]
					normalisedCandidate = candidate_lookup.get(candidate, normaliseName(candidate)) # Normalise the candidate
					normalisedPrecinct = precinct.replace("*", "")

					row = [county, normalisedPrecinct, normalisedOffice, canvass.district,
							party, normalisedCandidate, result]

					print row
					w.writerow(row)

def normaliseName(name):
	name = name.title()

	mistakes = {'Defazio': 'DeFazio',
				'Mccorkle': 'McCorkle',
				'Pole Ii': 'Pole II'}

	for mistake, correction in mistakes.iteritems():
		if mistake in name:
			name = name.replace(mistake, correction)

	return name

def printCanvasses(allCanvasses):
	pp = pprint.PrettyPrinter(indent=4)

	for canvass in allCanvasses:
		print canvass.office
		print canvass.district
		print canvass.candidates
		pp.pprint(canvass.results)
		print "====="



class OfficeCanvass(object):
	def __init__(self, text):
		self.lines = text.split('\n') # File uses Unix line endings
		self.header = []
		self.table = []
		self.results = {}
		self.district = ""
		self.candidates = OrderedDict()

		self.hyphenRE = re.compile("----")
		self.districtRE = re.compile(",? (\d\d?)\w\w Dist(rict)?")
		self.precintRE = re.compile("\d\d? PRECINCTS")
		self.partyRE = re.compile(" \((\w\w\w)\)")
		self.candidateRE = re.compile("(\d\d) = ([A-Za-z\. \(\)-]+)")

		self.parseTitle()
		self.parseOfficeDistrict()
		self.populateHeaderAndTable()
		self.parseHeader()
		self.parseResults()

	def __repr__(self):
		return "%s => %s" % (self.title, self.office)

	def parseTitle(self):
		self.title = self.lines[3].strip()
		
	def parseOfficeDistrict(self):
		self.office = self.title

		m = self.districtRE.search(self.office)
		if m:
			self.district = m.group(1)
			self.office = self.office.replace(m.group(0), "") # Remove district from office

	def populateHeaderAndTable(self):
		hyphenLineCount = 0

		for line in self.lines[3:]:
			if hyphenLineCount < 2:
				self.header.append(line)
				if self.hyphenRE.search(line):
					hyphenLineCount += 1
			else:
				if len(line.strip()):
					self.table.append(line)

	def parseHeader(self):
		headerLines = self.header

		cols = {}
		for line in self.header:
			for m in self.candidateRE.finditer(line):
				cols[m.group(1)] = m.group(2).strip()

		for key, val in sorted(cols.iteritems()):
			m = self.partyRE.search(val)
			if m:
				name = val.replace(m.group(0), "")
				self.candidates[name] = m.group(1)
			else:
				self.candidates[val] = ""

	def parseResults(self):
		for line in self.table:
			columns = line.split()
			candidateCount = len(self.candidates)

			votes = columns[-candidateCount:]
			del(columns[-candidateCount:])
			del(columns[0]) # Remove duplicitive, zero-padded precinct number
			
			precinct = " ".join(columns)
			self.results[precinct] = votes



# Default function is main()
if __name__ == '__main__':
	main()