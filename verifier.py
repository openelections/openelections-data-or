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

import csv
import os
import re
import argparse

def main():
	args = parseArguments()

	for path in args.paths:
		verifier = OEVerifier(path)

		if verifier.ready and "matrix" not in verifier.filename:
			verifier.verify()


def parseArguments():
	parser = argparse.ArgumentParser(description='Verify openelections CSV files')
	parser.add_argument('paths', metavar='N', type=str, nargs='+',
	                   help='path to a CSV file')

	return parser.parse_args()


class OEVerifier(object):
	validColumns = frozenset(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes', 'notes'])
	requiredColumns = frozenset(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'])
	validOffices = frozenset(['President', 'U.S. Senate', 'U.S. House', 'Governor', 'State Senate', 'State House', 'Attorney General', 'Secretary of State', 'State Treasurer'])
	officesWithDistricts = frozenset(['U.S. House', 'State Senate', 'State House'])

	def __init__(self, path):
		self.path = path
		self.columns = []
		self.reader = None
		self.ready = False

		self.countyRE = re.compile("\d{8}__[a-z]{2}_")

		try:
			self.pathSanityCheck(path)

			self.filename = os.path.basename(path)
			self.filenameState, self.filenameCounty = self.deriveStateCountyFromFilename(self.filename)
		except Exception as e:
			print("ERROR: {}".format(e))

		self.ready = True

	def verify(self):
		filetype = self.deriveTypeFromPath(self.path)
		self.parseFileAtPath(self.path)

	def pathSanityCheck(self, path):
		if not os.path.exists(path) or not os.path.isfile(path):
			raise FileNotFoundError("Can't find file at path %s" % path)

		if not os.path.splitext(path)[1] == ".csv":
			raise ValueError("Filename does not end in .csv: %s" % path)

		print("==> {}".format(path))

	def deriveTypeFromPath(self, path):
		if "general" in self.filename:
			if "precinct" in self.filename:
				return OEFileType.GeneralPrecinct
			else:
				return OEFileType.General
		elif "primary" in self.filename:
			if "precinct" in self.filename:
				return OEFileType.PrimaryPrecinct
			else:
				return OEFileType.Primary
		elif "special" in self.filename and "precinct" in self.filename:
			return OEFileType.SpecialPrecinct

	def deriveStateCountyFromFilename(self, filename):
		components = filename.split("__")

		if len(components) == 5:
			return (components[1], components[3].replace("_", " ").title())

		return ""

	def parseFileAtPath(self, path):
		with open(path, 'rU') as csvfile:
			self.reader = csv.DictReader(csvfile)
			
			if self.verifyColumns(self.reader.fieldnames):
				for row in self.reader:
					self.verifyCounty(row['county'], row)
					self.verifyOffice(row['office'], row)
					self.verifyDistrict(row)

	def verifyColumns(self, columns):
		invalidColumns = set(columns) - OEVerifier.validColumns
		missingColumns = OEVerifier.requiredColumns - set(columns)

		if invalidColumns:
			self.printError("Invalid columns: {}".format(invalidColumns))

		if missingColumns:
			self.printError("Missing columns: {}".format(missingColumns))
			return False

		return True

	def verifyCounty(self, county, row):
		normalisedCounty = county.title()

		if not normalisedCounty == self.filenameCounty:
			self.printError("County doesn't match filename", row)

		if not county == normalisedCounty:
			self.printError("Use title case for the county", row)

	def verifyOffice(self, office, row):
		if not office in OEVerifier.validOffices:
			self.self.printError("Invalid office: {}".format(office), row)

	def verifyDistrict(self, row):
		if row['office'] in OEVerifier.officesWithDistricts:
			if not row['district']:
				self.printError("Office '{}' requires a district".format(row['office']), row)
			elif row['district'].lower() == 'x':
				if self.filenameState == 'ms':
					pass # This is legit in MS
				else:
					self.printError("District must be an integer", row)
			elif not self.verifyInteger(row['district']):
				self.printError("District must be an integer", row)

	def verifyInteger(self, numberStr):
		try:
			integer = int(numberStr)
		except ValueError as e:
			return False

		return True

	def printError(self, text, row=[]):
		print("ERROR: " + text)

		if row:
			print row



class OEFileType(object):
	General, Primary, GeneralPrecinct, PrimaryPrecinct, SpecialPrecinct = range(1,6)

# Default function is main()
if __name__ == '__main__':
	main()