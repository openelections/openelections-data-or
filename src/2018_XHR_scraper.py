#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

# The MIT License (MIT)
# Copyright (c) 2017 OpenElections
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

from collections import namedtuple

import time
import argparse
import requests
import pandas as pd

import csv
import os
import re

Result = namedtuple('Result', 'county precinct office district party candidate votes')
Out_filename_format = '20181106__or__general__{county}__precinct.csv'

def main():
	args = parseArguments()
	scraper = ORScraper(args.metadataDirPath, args.outDirPath)

def parseArguments():
	parser = argparse.ArgumentParser(description='Scrape precinct results from OR SOS site via XHR calls')
	parser.add_argument('metadataDirPath', type=str,
						help='path to directory with metadata files, one per county from http://results.oregonvotes.gov/ResultsExport.aspx')
	parser.add_argument('outDirPath', type=str,
						help='path to output the CSV file(s) to')


	return parser.parse_args()


class ORScraper(object):
	def __init__(self, metadataDirPath, outDirPath):
		self.contests = {}
		self.results = []
		
		self.populateContests(metadataDirPath)
		self.fetchResults(metadataDirPath)
		self.writeOutResults(outDirPath)

		# sortedContests = sorted(self.contests, key = lambda x: (x['office'], x['district']))
		# for contest in sortedContests:
		# 	print(contest)

	def populateContests(self, metadataDirPath):
		for filename in ["Governor", "Statewide", "House and Senate"]:
			path = os.path.join(metadataDirPath, filename+'.csv')
			with open(path) as csvfile:
				for row in csv.DictReader(csvfile):
					contest = Contest(row)
					if not contest.id in self.contests:
						self.contests[contest.id] = contest

	def fetchResults(self, metadataDirPath):
		urlFormat = 'http://orresultswebservices.azureedge.net/ResultsAjax.svc/GetMapData?type={contestType}&category=PREC&raceID={raceID}&osn=0&county={countyID:02}&party=0'
		countyIDs = [35, 2, 7, 30, 8, 25, 3, 5, 6, 17, 11, 21, 32, 26, 19, 27, 34, 33, 24, 28, 18, 22] #range(1, 36) # Only some of the counties have precinct-level results presently
		count = 0

		for countyID in countyIDs:
			countyName = CountyIDs[f"{countyID:02}"]
			countyTotalFilePath = os.path.join(metadataDirPath, "county_totals", countyName+'.csv')

			countyTotalsDF = pd.read_csv(countyTotalFilePath, index_col=False).fillna('')
			countyContestIDs = countyTotalsDF["ContestID"].astype(str).values
			# Find only the relevant contests which occurred in this county
			relevantContestIDs = set(countyContestIDs).intersection(self.contests.keys())
			relevantContests = [self.contests[cid] for cid in relevantContestIDs]

			for contest in relevantContests:
				url = urlFormat.format_map({'contestType': contest.contestType, 'raceID': contest.id, 'countyID': countyID})
				print(f"Fetching {url}")
				r = requests.get(url)

				if r.status_code == 200:
					results = r.json()["d"]
					results = self.parseResults(countyName, contest, results)

				# Delay to prevent overwhelming the server
				time.sleep(1)
				
			# if count == 0:
			# 	break

			# count += 1

	def parseResults(self, county, contest, results):
		nonEmptyResults = [r for r in results if r["calcCandidateVotes"] > 0]

		if nonEmptyResults:
			for result in results:
				candidate = result["calcCandidate"]

				# For the verifier script, write-ins need to have this exact spelling
				if candidate == "Write-in":
					candidate += "s"

				r = Result(county, result["PrecinctName"], contest.office, contest.district, result["PartyCode"], candidate, result["calcCandidateVotes"])
				self.results.append(r)

	def writeOutResults(self, outDirPath):
		resultsDF = pd.DataFrame(self.results)
		resultsDF.sort_values(['county', 'precinct', 'office', 'district', 'party', 'candidate'], inplace=True)
		counties = resultsDF['county'].unique()

		for county in counties:
			countyResultsDF = resultsDF[resultsDF['county'] == county]
			countyName = county.replace(' ', '_').lower()
			outFilename = Out_filename_format.format(county=countyName)
			outFilePath = os.path.join(outDirPath, outFilename)

			with open(outFilePath, 'w') as outfile:
				writer = csv.writer(outfile, lineterminator='\n')
				writer.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']) # CSV header

				for index, row in countyResultsDF.iterrows():
					writer.writerow([row['county'], row['precinct'], row['office'], row['district'], row['party'], row['candidate'], row['votes']])




class Contest(object):
	normalizedOffices = {'US Representative': 'U.S. House',
						 'State Senator': 'State Senate',
						 'State Representative': 'State House'}
	contestTypes = {'US Representative': 'FED',
					'Governor': 'SWPAR',
					'State Senator': 'SENATE',
					'State Representative': 'HOUSE',}

	def __init__(self, row):
		self.id = row['ContestID']
		self.processContestName(row['ContestName'])
		self.processAreaNum(row['AreaNum'])

	def __repr__(self):
		if self.district:
			return f'{self.contestType} {self.office}, District {self.district}'
		else:
			return f'{self.contestType} {self.office}'
		

	def processContestName(self, contestName):
		print(contestName)
		contest = contestName.split(',')[0]
		print(contest)
		self.office = self.normalizedOffices.get(contest, contest)
		print(self.office)
		self.contestType = self.contestTypes.get(contest, '')

	def processAreaNum(self, areaNum):
		self.district = '' # By default, no district

		numRE = re.compile('(\d+)')
		m = numRE.search(areaNum)

		if m:
			self.district = m.group(1)

# Constants
CountyIDs = {
	"01": "Josephine",
	"02": "Curry",
	"03": "Jackson",
	"04": "Coos",
	"05": "Klamath",
	"06": "Lake",
	"07": "Douglas",
	"08": "Harney",
	"09": "Lane",
	"10": "Deschutes",
	"11": "Malheur",
	"12": "Crook",
	"13": "Benton",
	"14": "Linn",
	"15": "Jefferson",
	"16": "Grant",
	"17": "Lincoln",
	"18": "Wheeler",
	"19": "Polk",
	"20": "Baker",
	"21": "Marion",
	"22": "Yamhill",
	"23": "Clackamas",
	"24": "Wasco",
	"25": "Hood River",
	"26": "Multnomah",
	"27": "Sherman",
	"28": "Washington",
	"29": "Tillamook",
	"30": "Gilliam",
	"31": "Union",
	"32": "Morrow",
	"33": "Wallowa",
	"34": "Umatilla",
	"35": "Columbia",
	"36": "Clatsop",
}

# Default function is main()
if __name__ == '__main__':
	main()
