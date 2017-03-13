#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

# The MIT License (MIT)
# Copyright (c) 2017 Nick Kocharhook
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
import collections
import pandas

def main():
	args = parseArguments()

	progress = OEProgress(args.path)
	progress.printProgress()


class OEProgress(object):
	def __init__(self, path):
		self.path = path
		self.counts = collections.defaultdict(int)
		self.weightedCounts = collections.defaultdict(int)
		self.statuses = {'': 'Incomplete', 'done': 'Complete', 'n/a': 'Source missing'}

		self.populateResults()

	def populateResults(self):
		self.elections = pandas.read_csv(self.path).fillna('')

	def printProgress(self):
		for index, series in self.elections.iterrows():
			precinctCount = series['precinct count']
			primaries = series.filter(regex='primary$')
			generals  = series.filter(regex='general$')

			for elections in [primaries, generals]:
				gb = elections.groupby(elections)

				# Because primaries involve two complete elections, the number of results is doubled
				multiplier = 2 if elections.equals(primaries) else 1

				for group, values in gb:
					self.counts[group] += len(values.index)
					self.weightedCounts[group] += len(values.index) * precinctCount * multiplier

		def printCount(counts, name):
			countSum = sum(counts.values())
			print(f"By {name}:")

			for status, count in counts.items():
				print("{:>15} {:>5} {:>6.1%}".format(self.statuses[status], count, count/countSum))

			print("{:>15} {:>5}".format("Total", countSum))
		
		printCount(self.counts, 'election')
		printCount(self.weightedCounts, 'precinct')

def parseArguments():
	parser = argparse.ArgumentParser(description='Verify votes are correct using a simple checksum')
	# parser.add_argument('--verbose', '-v', dest='verbose', action='store_true')
	parser.add_argument('path', type=str, help='Path to county_matrix CSV file.')
	parser.set_defaults(verbose=False)

	return parser.parse_args()


# Default function is main()
if __name__ == '__main__':
	main()