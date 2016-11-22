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

# Configure variables
county = 'Lane'
outfile = '20020521__or__primary__lane__precinct.csv'


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
	divisionRE = re.compile("\f")

	# read from stdin
	for line in sys.stdin.readlines():
		if divisionRE.match(line):
			# print "===="+currentCanvass
			previousCanvass = OfficeCanvass(currentCanvass)

			# pdb.set_trace()

			if previousCanvass.office in office_lookup: # exclude lower offices
				allCanvasses.append(previousCanvass)

			currentCanvass = ""
		
		currentCanvass += line

	# printCanvasses(allCanvasses)
	writeCSV(allCanvasses)

def writeCSV(allCanvasses):
	def listGet(inList, index, default):
		try:
			out = inList[index]
		except IndexError:
			out = default

		return out

	with open(outfile, 'wb') as csvfile:
		w = unicodecsv.writer(csvfile, encoding='utf-8')
		w.writerow(headers)

		for canvass in allCanvasses:
			for precinct, results in canvass.results.iteritems():
				for index, result in enumerate(results):
					normalisedOffice = office_lookup[canvass.office] # Normalise the office
					candidate = canvass.candidates[index]
					normalisedCandidate = candidate_lookup.get(candidate, normaliseName(candidate)) # Normalise the candidate

					row = [county, precinct, normalisedOffice, canvass.district,
							canvass.party, normalisedCandidate, result]

					print row
					w.writerow(row)

def normaliseName(name):
	name = name.title()

	# Fix "Mcneill"
	mcRE = re.compile("Mc([a-z])")
	m = mcRE.search(name)
	if m:
		i = m.start(1)
		name = name[:i] + name[i].swapcase() + name[i+1:]

	# Fix "Eliz Terry Beyer"
	if "Eliz " in name:
		name = name.replace("Eliz", "Elizabeth")

	return name

def printCanvasses(allCanvasses):
	pp = pprint.PrettyPrinter(indent=4)

	for canvass in allCanvasses:
		print canvass.party
		print canvass.office
		print canvass.district
		print canvass.candidates
		pp.pprint(canvass.results)
		print "====="



class OfficeCanvass(object):
	def __init__(self, text):
		self.lines = text.split('\r\n') # File uses Windows line endings
		self.header = []
		self.table = []
		self.results = {}
		self.district = ""
		self.party = ""
		self.candidates = []

		self.endOfTableRE = re.compile("\*\*\*\*")
		self.districtRE = re.compile(" (\d\d?)\w\w DIST(RICT)?")
		self.precintRE = re.compile("\d\d? PRECINCTS")
		self.partyRE = re.compile("\((\w\w\w)\)")
		self.pageNumberRE = re.compile("Page Number\s+[\.\d]+")
		self.partyRE = re.compile("\s?-\s?(DEM|REP)$")

		self.parseTitle()
		self.removeTurnoutColumns()
		self.parseOfficePartyDistrict()
		self.populateHeaderAndTable()
		self.parseHeader()
		self.parseResults()

	def __repr__(self):
		return self.title+self.party+self.office

	def removeTurnoutColumns(self):
		for index, line in enumerate(self.lines):
			if len(line) > 50:
				self.lines[index] = line[:26]+line[43:]

	def parseTitle(self):
		self.title = self.lines[1].strip()
		
	def parseOfficePartyDistrict(self):
		self.office = self.title

		m = self.partyRE.search(self.office)
		if m:
			if m.group(1) in party_prefixes:
				self.party = m.group(1)
				self.office = self.office.replace(m.group(0), "") # Remove party from office

		m = self.districtRE.search(self.office)
		if m:
			self.district = m.group(1)
			self.office = self.office.replace(m.group(0), "") # Remove district from office

	def parseParties(self):
		for index, candidate in enumerate(self.candidates):
			if ',' in candidate:
				name, party = candidate.split(",")
				self.candidates[index] = name
				self.parties.append(party.strip())
			else:
				self.parties.append('')

	def populateHeaderAndTable(self):
		self.header = self.lines[2:26]

		for line in self.lines[26:]:
			if self.endOfTableRE.search(line):
				break

			if len(line.strip()):
				self.table.append(line)

	def parseHeader(self):
		headerLines = self.header

		# 1. Remove the page number
		firstLine = headerLines[0]
		m = self.pageNumberRE.search(firstLine)

		if m:
			firstLine = firstLine.replace(m.group(0), " "*len(m.group(0)))
			headerLines[0] = firstLine

		# 2. List candidate columns
		cols = [35, 41, 47, 53, 59, 65]

		# 3. Make sure all strings are the same length
		longestString = len(headerLines[0])
		for i, line in enumerate(headerLines):
			headerLines[i] = headerLines[i].ljust(longestString)

		# 4. Create 2D array of characters to easily zip()
		bitmap = []
		for line in headerLines:
			bitmap.append(list(line))

		# for aList in bitmap:
		# 	print aList

		# print('\n'.join('{}: {}'.format(*k) for k in enumerate(list(headerLines[0]))))

		# 5. Read vertical columns of characters as the names, removing unused spaces
		names = []
		for col in cols:
			if longestString >= col:
				name = "".join(zip(*bitmap)[col])
				names.append(" ".join(name.split()))

		self.candidates = names

	def parseResults(self):
		for line in self.table:
			columns = line.split()
			candidateCount = len(self.candidates)

			votes = columns[-candidateCount:]
			del(columns[-candidateCount:])
			
			precinct = " ".join(columns)
			self.results[precinct] = votes



# Default function is main()
if __name__ == '__main__':
	main()