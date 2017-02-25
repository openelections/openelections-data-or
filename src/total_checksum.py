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
import argparse
import pandas


def main():
	args = parseArguments()

	checker = TotalChecker(args.path)
	checker.singleError = args.singleError

	# Candidate total
	checkedCandidateTotals = checker.checkTotals('precinct', ['office', 'district', 'candidate', 'party'])

	# Precinct total
	checkedPrecinctTotals = checker.checkTotals('candidate', ['office', 'district', 'precinct', 'party'])

	if not checkedCandidateTotals and not checkedPrecinctTotals:
		print("No totals to check")


class TotalChecker(object):
	def __init__(self, path):
		self.path = path
		self.singleError = False

		print("==> {}".format(os.path.basename(path)))

		self.populateResults()

	def populateResults(self):
		self.results = pandas.read_csv(self.path).fillna('')
		self.results[['votes']] = self.results[['votes']].apply(pandas.to_numeric)
		self.results['precinct'] = self.results['precinct'].astype(str)

		self.results_sans_totals = self.results.loc[(self.results.candidate != 'Total') & (self.results.precinct != 'Total')]	

	def checkTotals(self, totalColumn, columns):
		contests = self.results.drop_duplicates(columns)[columns].values
		total_data = self.results.loc[self.results[totalColumn] == 'Total']
		
		if len(total_data):
			# Calculate our own totals to compare
			totals = self.results_sans_totals.groupby(columns).votes.sum()

			for index, row in total_data.iterrows():
				file_total = row.votes
				index_values = tuple(row[x] for x in columns)
				actual_total = totals.loc[index_values]

				if file_total != actual_total:
					lineNo = index + 2 # 1 for header, 1 for zero-indexing
					print("ERROR: {} total incorrect, line {}. {} != {}".format(
						"precinct" if totalColumn == "candidate" else "candidate",
						lineNo, file_total, actual_total))
					print(row.to_dict())

					if self.singleError:
						break

			return True

		return False

def parseArguments():
	parser = argparse.ArgumentParser(description='Verify votes are correct using a simple checksum')
	parser.add_argument('--verbose', '-v', dest='verbose', action='store_true')
	parser.add_argument('--excludeOverUnder', dest='includeOverUnder', action='store_false')
	parser.add_argument('--singleError', dest='singleError', action='store_true', help='Display only the first error in each file')
	parser.add_argument('path', type=str, help='path to a CSV file')
	parser.set_defaults(verbose=False)

	return parser.parse_args()


# Default function is main()
if __name__ == '__main__':
	main()