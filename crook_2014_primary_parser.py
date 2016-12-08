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

import csv
import sys
import re

office_lookup = {
	'UNITED STATES PRESIDENT AND VICE PRESIDENT': 'President',
	'US SENATOR': 'U.S. Senate',
	'US REPRESENTATIVE': 'U.S. House',
	'SECRETARY OF STATE': 'Secretary of State',
	'STATE TREASURER': 'State Treasurer',
	'ATTORNEY GENERAL': 'Attorney General',
	'GOVERNOR': 'Governor',
	'STATE REPRESENTATIVE': 'State House',
	'STATE SENATOR': 'State Senate'
}


# Configure variables
county = 'Crook'
outfile = '20140520__or__primary__crook__precinct.csv'

headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']


def main():
	csvLines = []

	with open(sys.argv[1], 'rb') as csvfile:
		reader = csv.reader(csvfile, delimiter=',', quotechar='"')
		header = []
		office = ""
		district = ""
		party = ""
		for row in reader:
			if not row[1]:
				office, district, party = parseOfficeDistrictParty(row[0])
			elif row[0] == "Precinct":
				header = row
			else:
				if row[0] == "TOTAL:":
					continue
				for index, votes in enumerate(row):
					if index > 0 and row[index]:
						normalisedOffice = office_lookup[office.upper()] # Normalise the office
						precinct = row[0].split()[1].lstrip("0")
						candidate = "Total" if header[index] == "Total Ballots Counted" else header[index]
						csvLines.append([county, precinct, normalisedOffice, district, party, candidate, votes])

	with open(outfile, 'wb') as csvfile:
		w = csv.writer(csvfile)
		w.writerow(headers)

		for row in csvLines:
			w.writerow(row)

def parseOfficeDistrictParty(text):
	party = ""
	district = ""
	office = text.strip()

	partyPostfixRE = re.compile(" (Democrat|Republican)$")
	districtPrefixRE = re.compile(",? (\d\d?)\w\w District")

	m = partyPostfixRE.search(office)

	if m:
		party = m.group(1)
		office = office.replace(m.group(0), "") # Remove party from text

	m = districtPrefixRE.search(office)

	if m:
		district = m.group(1)
		office = office.replace(m.group(0), "") # Remove district from office

	return (office, district, party)


# Default function is main()
if __name__ == '__main__':
	main()