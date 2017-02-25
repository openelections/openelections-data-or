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
import sys
import re
import argparse

def main():
	args = parseArguments()

	with open(args.path, 'rU') as csvfile:
		reader = csv.DictReader(csvfile)
		groupingColumns = ()
		totalColumn = ''
		
		currentGroup = ()
		voteTotal = 0

		# First, figure out if we're doing precinct totals or candidate totals
		for row in reader:
			if row["candidate"] == 'Total':
				totalColumn = 'candidate'
				groupingColumns = ('precinct', 'office', 'district')
				break
			elif row["precinct"] == 'Total':
				totalColumn = 'precinct'
				groupingColumns = ('candidate', 'office', 'district')
				break
		else:
			print("No totals to compare vote counts with")
			sys.exit(0)

		# Now that we've figured out which column to accumulate totals by, start over
		csvfile.seek(0)
		firstRow = True

		for row in reader:
			if firstRow:
				firstRow = False
				continue # due to the seek, have to manually skip the header row

			rowGroup = tuple(row[col] for col in groupingColumns)
			# print(rowGroup)

			try:
				votes = int(row["votes"].replace(",", ""))
			except Exception as e:
				print("ERROR: Could not convert this to an integer: '%s'" % row["votes"])
				print("ERROR: %s" % repr(row))
				continue

			if rowGroup == currentGroup:
				if not args.includeOverUnder and row["candidate"] in ("Under Votes", "Over Votes", "Total Votes Cast"):
					continue
				elif row[totalColumn] == "Total":
					if voteTotal != votes:
						print("ERROR: %d != %s" % (voteTotal, row["votes"]))
						print("ERROR: %s" % repr(row))
					if args.verbose:
						print("====")
						print(row)
				else:
					voteTotal += votes

					if args.verbose:
						print("====")
						print("total=%d" % voteTotal)
						print(row)
			else:
				currentGroup = rowGroup
				voteTotal = votes # reset the vote total

				if args.verbose:
					print("====")
					print("total=%d" % voteTotal)
					print(row)

def parseArguments():
	parser = argparse.ArgumentParser(description='Verify votes are correct using a simple checksum')
	parser.add_argument('--verbose', '-v', dest='verbose', action='store_true')
	parser.add_argument('--excludeOverUnder', dest='includeOverUnder', action='store_false')
	parser.add_argument('path', type=str, help='path to a CSV file')
	parser.set_defaults(verbose=False)

	return parser.parse_args()


# Default function is main()
if __name__ == '__main__':
	main()