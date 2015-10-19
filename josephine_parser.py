import re
import unicodecsv
from BeautifulSoup import BeautifulSoup
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
parties = ['DEMOCRAT', 'REPUBLICAN']
party_abbrevs = ['(REP)', '(DEM)', '(GRN)', '(PRO)', '(LIB)', '(CON)', '(PAC)', '(LBT)', '(WFP)', '(PGP)', '(PCE)', '(IND)']
offices = ['United States President', 'United States Senator', 'Representative in Congress, 2nd District', 'Representative in Congress, 4th District', 'State Representative, 1st District', 'State Representative, 2nd District', 'State Representative, 3rd District', 'State Representative, 4th District', 'Governor', 'Representative, 2nd Dist. 2ND DISTRICT', 'Representative, 4th Dist. 4TH DISTRICT', 'State Treasurer', 'Attorney General', 'Secretary of State', 'State Senator, 1st District', 'State Senator, 2nd District', 'Representative, Congress, 2nd District', 'Representative, Congress, 4th District', 'United States President and VP',
'Representative, Congress 4th Dist. 4TH DISTRICT', 'Representative, Congress 2nd Dist. 2ND DISTRICT', 'State Senator, 1st District']
office_lookup = {
    'United States Senator' : 'U.S. Senate', 'Representative in Congress' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'Secretary of State' : 'Secretary of State', 'Attorney General' : 'Attorney General',
    'State Treasurer' : 'State Treasurer', 'Representative' : 'U.S. House', 'United States President' : 'President', 'Representative, Congress' : 'U.S. House',
    'United States President and VP' : 'President', 'Representative, Congress 4th Dist': 'U.S. House', 'Representative, Congress 2nd Dist': 'U.S. House'
}

with open('20081104__or__general__josephine__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    file = open("/Users/derekwillis/code/openelections-sources-or/Josephine/Nov08.htm").read()
    soup = BeautifulSoup(file)
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
        if 'PERCENT' in line:
            continue
        if 'ELECTION' in line:
            continue
        if 'of the' in line:
            continue
        if line.strip() == 'Vote For  1':
            continue
        if line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
            continue
        # for primary
        if any(party in line for party in parties):
            party = line.replace("(",'').replace(")",'').strip()
            continue
        # parse offices, reset keys
        if any(office in line for office in offices):
            print line.strip()
            if "DISTRICT" in line.strip().upper():
                if 'State' in line.strip():
                    o, d = line.strip().split(', ')
                elif 'Dist.' in line.strip():
                    o, d = line.strip().split('. ')
                else:
                    o1, o2, d = line.strip().split(', ')
                    o = o1.strip() + ', ' + o2.strip()
                office = office_lookup[o]
                district = d[0]
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
                    if any(party in line for party in party_abbrevs):
                        party = next(party for party in party_abbrevs if party in line)
                        name = name.replace(party, '').strip()
                        party = party.replace('(','').replace(')','')
                    else:
                        party = None
                    if name in ['WRITE-IN', 'OVER VOTES', 'UNDER VOTES']:
                        party = None
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
                w.writerow(['Josephine', precinct, office, district, cand['party'], cand['name'], result_bits[cand['code']]])
