import unicodecsv
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
offices = ['PRESIDENT OF THE UNITED STATES', 'United States Senator', 'Representative in Congress', 'Governor', 'State Senator', 'State Representative', 'Secretary of State', 'Attorney General', 'State Treasurer']
office_lookup = {
    'United States Senator' : 'U.S. Senate', 'Representative in Congress' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'Secretary of State' : 'Secretary of State', 'Attorney General' : 'Attorney General',
    'State Treasurer' : 'State Treasurer'
}

with open('20140520__or__primary__clackamas__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    lines = open('/Users/derekwillis/Downloads/52014finalcanvass.txt').readlines()
    for line in lines:
        if line == '\n':
            continue
        if len([x for x in offices if x in line]) > 0:
            office = [x for x in offices if x in line][0]
            if 'District' in line:
                office, district = line.split('\t')[0].split(', ')
                district = district.replace(' District','').strip()
                district = district.replace('th', '')
                district = district.replace('rd', '')
                district = district.replace('st', '')
                district = district.replace('nd', '')
            else:
                district = None
            if 'Democrat' in line:
                party = 'DEM'
            elif 'Republican' in line:
                party = 'REP'
            else:
                party = None
            display_office = office_lookup[office.strip()]
        elif 'Write-in' in line:
            candidate_bits = [x.strip() for x in line.split('\t')]
        else:
            bits = line.split()
            precinct = bits[0]
            over_votes = int(bits[2])
            under_votes = int(bits[3])
            candidate_results = [int(x) for x in bits[7:]]
            precinct_results = zip(candidate_bits, candidate_results)
            precinct_results.append(('Over Votes', over_votes))
            precinct_results.append(('Under Votes', under_votes))
            for candidate, votes in precinct_results:
                w.writerow(['Clackamas', precinct, display_office, district, party, candidate, votes])
