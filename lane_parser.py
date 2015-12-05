import re
import requests
import unicodecsv
from BeautifulSoup import BeautifulSoup
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
parties = ['(Democratic)', '(Republican)']
party_abbrevs = ['(REP)', '(DEM)', '(GRN)', '(PRO)', '(LIB)', '(CON)', '(PAC)', '(LBT)', '(WFP)', '(PGP)', '(PCE)', '(IND)']
offices = ['United States President', 'United States Senator', 'US Representative, 4th District', 'Representative in Congress, 4th District', 'State Representative 7th District', 'State Representative 8th District',
'State Representative 9th District', 'State Representative 11th District', 'Governor', 'State Representative 12th District', 'State Representative 13th District', 'State Representative 14th District',
'State Treasurer', 'Attorney General', 'Secretary of State', 'State Senator, 1st District', 'State Senator, 2nd District', 'Representative, Congress, 4th District', 'United States President and Vice President',
'Representative in Congress 4th District', 'State Senator, 5th District', 'State Senator 4th District', 'State Senator 6th District', 'State Senator 7th District', ]
office_lookup = {
    'United States Senator' : 'U.S. Senate', 'Representative in Congress' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'Secretary of State' : 'Secretary of State', 'Attorney General' : 'Attorney General',
    'State Treasurer' : 'State Treasurer', 'Representative' : 'U.S. House', 'United States President' : 'President', 'Representative, Congress' : 'U.S. House',
    'United States President and Vice President' : 'President', 'Representative in Congress 4th District': 'U.S. House', 'Representative, Congress 2nd Dist': 'U.S. House'
}

with open('20140520__or__primary__lane__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    r = requests.get("http://www.lanecounty.org/Departments/CAO/Operations/CountyClerk/elections/ElectionDocuments/20140520_FinalCanvas.HTM")
#    lines = r.text.split('\r\n')
    soup = BeautifulSoup(r.text)
    lines = soup.find('pre').text.split('\r\n')
    keys = []
    for line in lines:
        if line.strip() == '\n':
            continue
        if "NUMBERED KEY CANVASS" in line:
            continue
        if line.strip() == '':
            continue
#        if lines[1][0:10].strip() == '':
#            continue
        if 'RUN DATE:' in line:
            continue
        if 'STATISTICS' in line:
            continue
        if '- - -' in line:
            continue
        if '-----' in line:
            continue
        if '==' in line:
            continue
        if "REGISTERED VOTERS" in line:
            continue
        if "BALLOTS CAST" in line:
            continue
        if "VOTER TURNOUT" in line:
            continue
        if 'VOTERS' in line:
            continue
        if 'PERCENT' in line:
            continue
        if 'Election' in line:
            continue
        if 'of the' in line:
            continue
        if line.strip() == 'Vote For  1':
            continue
        if line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
            continue
        # for primary
        if any(party in line for party in parties):
            party = line.replace("(",'').replace(")",'').strip()[0:3].upper()
            continue
        # parse offices, reset keys
        if any(office in line for office in offices):
            print line.strip()
            if "DISTRICT" in line.strip().upper():
                if 'State Senator' in line.strip():
                    o = 'State Senator'
                    d = line.strip().split('State Senator ')[1]
                elif 'State Representative' in line.strip():
                    o = 'State Representative'
                    d = line.strip().split('State Representative ')[1]
                elif line.strip() == 'Representative in Congress 4th District':
                    o = 'Representative in Congress'
                    d = '4th District'
                else:
                    o, d = line.strip().split(', ')
                office = office_lookup[o.strip()]
                district = "".join([s for s in d if s.isdigit()])
            else:
                office = office_lookup[line.strip()]
                district = None
            keys = []
            continue
        if "=" in line:
            if office:
                # get candidate keys
                candidate_bits = [x.strip() for x in line.split('   ') if '=' in x]
                candidates = [x.split(' = ') for x in candidate_bits]
                for code, name in candidates:
#                    if any(party in line for party in party_abbrevs):
#                        party = next(party for party in party_abbrevs if party in line)
#                        name = name.replace(party, '').strip()
#                        party = party.replace('(','').replace(')','')
#                    else:
#                        party = None
#                    if name in ['WRITE-IN', 'OVER VOTES', 'UNDER VOTES']:
#                        party = None
                    keys.append({'code': int(code), 'name': name, 'party': party})
                continue
        # once we have all keys, sort them in order
        candidate_keys = sorted(keys, key=lambda k: k['code'])
        # parse vote data, should match keys
        result_bits = [x for x in line.split('  ') if x <> '']
        result_bits = [x.strip() for x in result_bits]
        result_bits = [x.replace(' . ', ' ') for x in result_bits]
#        print result_bits
        precinct = result_bits[0]
#        result_bits = result_bits[1:] # remove precinct info
        if len(result_bits) > 0:
            for cand in candidate_keys:
                w.writerow(['Lane', precinct, office, district, cand['party'], cand['name'], result_bits[cand['code']]])
