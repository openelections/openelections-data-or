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
import pdb
import fileinput
import unicodecsv
import pprint
import string

# Configure variables
county = 'Lane'
outfile = '20061107__or__general__lane__precinct.csv'


headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
party_prefixes = ['DEM', 'REP', '']

office_lookup = {
	'United States President': 'President',
	'United States Senator': 'U.S. Senate',
	'REPRESENTATIVE IN CONGRESS': 'U.S. House',
	'Secretary of State': 'Secretary of State',
	'State Treasurer': 'State Treasurer',
	'Attorney General': 'Attorney General',
	'GOVERNOR': 'Governor',
	'STATE REP': 'State House',
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
	divisionRE = re.compile("@@@")

	# read from stdin
	for line in fileinput.input():
		if divisionRE.match(line):
			previousCanvass = OfficeCanvass(currentCanvass)
			allCanvasses.append(previousCanvass)

			currentCanvass = ""
		else:
			currentCanvass += line

	allCanvasses.append(OfficeCanvass(currentCanvass))

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
					party = listGet(canvass.parties, index, "")
					normalisedCandidate = candidate_lookup.get(candidate, candidate) # Normalise the candidate

					row = [county, precinct, normalisedOffice, canvass.district,
							party, normalisedCandidate, result]

					print row
					w.writerow(row)

def printCanvasses(allCanvasses):
	pp = pprint.PrettyPrinter(indent=4)

	for canvass in allCanvasses:
		print canvass.office
		print canvass.district
		print canvass.candidates
		print canvass.parties
		pp.pprint(canvass.results)
		print "====="



class OfficeCanvass(object):
	def __init__(self, text):
		self.lines = text.split('\r\n') # File uses Windows line endings
		self.header = []
		self.table = []
		self.results = {}
		self.district = ""
		self.parties = []
		self.candidates = []

		self.hyphenRE = re.compile("(-----)")
		self.parenRE = re.compile("\(")
		self.endOfTableRE = re.compile("TOTALS")
		self.districtRE = re.compile(" (\d\d?)\w\w DISTRICT")

		self.parseTitle()
		self.parseOfficePartyDistrict()
		self.populateHeaderAndTable()
		self.parseHeader()
		self.parseResults()

	def __repr__(self):
		return self.title+self.party+self.office

	def parseTitle(self):
		self.title = self.lines[0].strip()
		
	def parseOfficePartyDistrict(self):
		self.office = self.title.strip()

		m = self.districtRE.search(self.office)
		if m:
			self.district = m.group(1)
			self.office = self.office.replace(m.group(0), "") # Remove district from office

	def populateHeaderAndTable(self):
		inTable = False

		for line in self.lines[1:]:
			if not inTable:
				self.header.append(line)
				if self.hyphenRE.search(line):
					inTable = True
			else:
				if self.endOfTableRE.search(line):
					break

				self.table.append(line)

	def parseHeader(self):
		# 1. Remove the diagonal printing
		i = 0
		headerLines = self.header

		# pdb.set_trace()

		for index, line in enumerate(headerLines):
			if index > 0 and not line.endswith("-----") and not self.parenRE.search(line): # Don't increment first and last lines
				i += 1

			headerLines[index] = line[i:]

		# 2. Find out where the columns are
		cols = []
		longestString = len(headerLines[-1])

		for m in self.hyphenRE.finditer(headerLines[-1]):
			for i, g in enumerate(m.groups()):
				cols.append(m.span(i))

		# 2.5. Remove the parties
		partyString = headerLines[-2].translate(string.maketrans("()", "  "))
		self.parties = partyString.split()
		del(headerLines[-2])

		# 3. Make sure all strings are the same length
		for i, line in enumerate(headerLines):
			headerLines[i] = headerLines[i].ljust(longestString)

		# 4. Create 2D array of characters to easily zip()
		bitmap = []
		for line in headerLines[:-1]: #skip line of hyphens
			bitmap.append(list(line))

		# 5. Read vertical columns of characters as the names, removing unused spaces
		names = []
		for i, col in enumerate(cols):
			name = ""
			for colNum in range(col[0], col[1]):
				name += "".join(zip(*bitmap)[colNum]) + " "
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