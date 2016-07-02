import re
import unicodecsv
import requests
from BeautifulSoup import BeautifulSoup

headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

offices = ['President', 'US Senator', 'US Representative', 'State Representative',
'Governor', 'State Treasurer', 'Attorney General', 'Secretary of State', 'United States President and VP']

office_lookup = {
    'US Senator' : 'U.S. Senate', 'US Representative' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'State Representative, 58th District': 'State House', 'President': 'President',
    'Secretary of State': 'Secretary of State', 'State Treasurer': 'State Treasurer', 'Attorney General': 'Attorney General'
}

with open('20160517__or__primary__union__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    r = requests.get('http://union-county.org/elections/2016/EL30.HTM')
    soup = BeautifulSoup(r.text)
    lines = soup.find('pre').text.split('\r\n')
    keys = []
    for line in lines:
        if line.strip() == '\n':
            continue
        if "PRECINCT REPORT       Union County, Oregon" in line:
            continue
        if line.strip() == '':
            continue
        if 'Run Date:' in line:
            continue
        if 'RUN TIME:' in line:
            continue
        if 'VOTES  PERCENT' in line:
            continue
        if 'REGISTERED VOTERS' in line:
            continue
        if 'Vote For  1' in line:
            continue
        if 'BALLOTS CAST' in line:
            continue
        if 'VOTER TURNOUT' in line:
            continue
        if line.strip()[0:2] == '00':
            precinct = int(line.strip().split()[0])
        if any(office in line for office in offices):
            if "District" in line.strip():
                o, d = line.strip().split(', ')
                office = office_lookup[o]
                if office == 'U.S. House':
                    district = '2'
                else:
                    district = d[0:2]
            else:
                office = office_lookup[line.strip()]
                district = None
            continue
        if ".  .  ." in line:
            if not office:
                continue
            # candidate result
            cand_bits = [x.strip() for x in line.strip().split('  ') if x != '.']
            cand_bits = [x.strip() for x in cand_bits if x != '']
            if cand_bits[1] == '0':
                cand_and_party, votes = cand_bits
            elif len(cand_bits) == 2:
                cand_and_party, votes = cand_bits
            else:
                try:
                    cand_and_party, votes, pct = cand_bits
                except:
                    continue
            if "(" in cand_and_party:
                candidate, party = [x.strip() for x in cand_and_party.split('(')]
                party = party.replace(')','').replace('.','').strip()
            else:
                candidate = cand_and_party.strip().replace('.','')
                party = None
            w.writerow(['Union', precinct, office, district, party, candidate, votes])
#            if candidate == 'WRITE-IN':
#                office = None
