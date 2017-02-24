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
import pandas

def main():
	args = parseArguments()

	results = pandas.read_csv(args.path)
	results = results.fillna('')

	contest_by_cand_columns = ['office', 'district', 'candidate', 'party']
	contest_by_cand = results.drop_duplicates(contest_by_cand_columns)[contest_by_cand_columns].values

	contest_by_prec_columns = ['office', 'district', 'precinct', 'party']
	contest_by_prec = results.drop_duplicates(contest_by_prec_columns)[contest_by_prec_columns].values

	precinct_total_data = results.loc[results.candidate == 'Total']
	candidate_total_data = results.loc[results.precinct == 'Total']
	results_sans_totals = results.loc[(results.candidate != 'Total') & (results.precinct != 'Total')]

	if len(precinct_total_data):
		# Calculate our own totals per precinct to compare
		precinct_totals = results_sans_totals.groupby(contest_by_prec_columns).votes.sum()

		for index, row in precinct_total_data.iterrows():
			file_total = row.votes
			actual_total = precinct_totals[row.office, row.district, row.precinct, row.party]

			if file_total != actual_total:
				print("Error: total incorrect, line {}. {} != {}".format(index, file_total, actual_total))
				print(row)


	if len(candidate_total_data):
		# Calculate our own totals per precinct to compare
		candidate_totals = results_sans_totals.groupby(contest_by_cand_columns).votes.sum()

		for index, row in candidate_total_data.iterrows():
			file_total = row.votes
			actual_total = candidate_totals[row.office, row.district, row.candidate, row.party]

			if file_total != actual_total:
				print("Error: total incorrect, line {}. {} != {}".format(index, file_total, actual_total))
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