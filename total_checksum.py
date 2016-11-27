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

def main():
	with open(sys.argv[1], 'rU') as csvfile:
		reader = csv.DictReader(csvfile)
		
		currentOffice = ()
		voteTotal = 0
		for row in reader:
			rowOffice = (row["precinct"], row["office"], row["district"])

			try:
				votes = int(row["votes"].replace(",", ""))
			except Exception as e:
				print "ERROR: Could not convert this to an integer: '%s'" % row["votes"]
				print "ERROR: %s" % repr(row)

			if rowOffice == currentOffice:
				if row["candidate"] in ("Under Votes", "Over Votes", "Total Votes Cast", "Under-Votes", "Over-Votes"):
					continue
				elif row["candidate"] == "Total":
					if voteTotal != votes:
						print "ERROR: %d != %s" % (voteTotal, row["votes"])
						print "ERROR: %s" % repr(row)
					# print row
				else:
					voteTotal += votes
					# print "total=%d" % voteTotal
					# print row
			else:
				currentOffice = rowOffice
				voteTotal = votes # reset the vote total
				# print "total=%d" % voteTotal
				# print row


# Default function is main()
if __name__ == '__main__':
	main()